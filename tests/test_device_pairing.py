import base64
import json
import time
from unittest.mock import MagicMock, patch

from app import app
from dashboard_pairing import PairingSessionStore

app.config['TESTING'] = True

REGISTER_PASSWORD = 'test-pass'


def auth_header(password=REGISTER_PASSWORD):
    token = base64.b64encode(f'admin:{password}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


def test_login_is_proxied_to_the_partner_api(monkeypatch):
    monkeypatch.setenv('PARTNER_API_URL', 'https://partner.example')
    upstream = MagicMock(
        status_code=200,
        content=b'{"allocationStatus":0}',
        headers={'Content-Type': 'application/json'},
    )
    with patch('app.requests.request', return_value=upstream) as request:
        client = app.test_client()
        response = client.post(
            '/api/open/device/login',
            json={'partnerId': 'partner-1', 'deviceId': 'device-1'},
        )

    assert response.status_code == 200
    assert response.json['allocationStatus'] == 0
    request.assert_called_once()
    args, kwargs = request.call_args
    assert args[0] == 'POST'
    assert args[1] == 'https://partner.example/api/open/device/login'
    assert b'partner-1' in kwargs['data']


def test_status_forwards_the_bearer_token(monkeypatch):
    monkeypatch.setenv('PARTNER_API_URL', 'https://partner.example')
    upstream = MagicMock(
        status_code=200,
        content=b'{"allocationStatus":2}',
        headers={'Content-Type': 'application/json'},
    )
    with patch('app.requests.request', return_value=upstream) as request:
        client = app.test_client()
        response = client.get(
            '/api/open/device/status',
            headers={'Authorization': 'Bearer token-1'},
        )

    assert response.status_code == 200
    _, kwargs = request.call_args
    assert kwargs['headers']['Authorization'] == 'Bearer token-1'


class Cookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


def json_response(status, payload, cookies=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = str(payload)
    response.cookies = [Cookie(name, value) for name, value in (cookies or {}).items()]
    return response


def test_pair_page_signs_in_selects_a_venue_and_submits_the_code(monkeypatch, tmp_path):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    monkeypatch.setenv('FRONT_DOOR_URL', 'https://frontdoor.example')
    monkeypatch.setenv('PAIRING_SESSION_PATH', str(tmp_path / 'sessions.sqlite'))
    script = [
        json_response(200, {'Token': 'jwt-1'}, {'session': 'created'}),
        json_response(403, {'Subcode': 1110, 'Description': 'TOTP required'}, {'session': 'pre-2fa'}),
        json_response(200, {'isLoggedOn': True}, {'session': 'ok'}),
        json_response(200, {
            'results': {
                'items': [
                    {
                        'id': 'venue-1',
                        'primary': {'displayName': 'The Lockhart'},
                        'organization': {'id': 'org-1', 'displayName': 'TipJAR'},
                    },
                    {
                        'id': 'venue-2',
                        'primary': {'displayName': 'Other Bar'},
                        'organization': {'id': 'org-1', 'displayName': 'TipJAR'},
                    },
                ],
            },
        }),
        json_response(200, {'ok': True}),
        json_response(200, {'ok': True}),
    ]

    def request(method, url, headers=None, data=None, timeout=None):
        assert method == 'POST'
        assert timeout == 30
        return script.pop(0)

    client = app.test_client()
    with patch('app.requests.request', side_effect=request) as http:
        login = client.post(
            '/pair',
            data={'action': 'login', 'email': 'devin@tipjar.tech', 'password': 'secret'},
            headers=auth_header(),
        )
        assert login.status_code == 200
        assert b'6-digit code' in login.data
        assert b'jwt-1' in login.data
        assert b'/api/security/logon/device' in login.data
        assert b'secret' not in login.data

        verify = client.post(
            '/pair',
            data={'action': 'totp', 'totp_code': '123456'},
            headers=auth_header(),
        )
        assert verify.status_code == 200
        assert b'The Lockhart' in verify.data
        assert b'venue-1' in verify.data

        choose = client.post(
            '/pair',
            data={'action': 'venue', 'venue_id': 'venue-1'},
            headers=auth_header(),
        )
        assert choose.status_code == 200
        assert b'Pairing code' in choose.data
        assert b'TipJAR' in choose.data

        forged = client.post(
            '/pair',
            data={'action': 'pair', 'pairing_code': '482913', 'organizationVenueId': 'venue-2'},
            headers=auth_header(),
        )
        assert forged.status_code == 200
        assert b'Device paired with The Lockhart using code 482913.' in forged.data
        assert b'jwt-1' in forged.data
        assert b'/api/dashboard/entity/device/pair' in forged.data
        assert b'Pairing code' in forged.data

        again = client.post(
            '/pair',
            data={'action': 'pair', 'pairing_code': '999111'},
            headers=auth_header(),
        )

    assert again.status_code == 200
    assert b'Device paired with The Lockhart using code 999111.' in again.data
    urls = [call.args[1] for call in http.call_args_list]
    assert urls.count('https://frontdoor.example/api/security/sessioncreate') == 1
    assert urls[-1] == 'https://frontdoor.example/api/dashboard/entity/device/pair'
    pair_call = http.call_args_list[-2]
    assert pair_call.args[0] == 'POST'
    assert pair_call.args[1] == 'https://frontdoor.example/api/dashboard/entity/device/pair'
    assert pair_call.kwargs['headers']['Authorization'] == 'bearer jwt-1'
    assert 'session=ok' in pair_call.kwargs['headers']['Cookie']
    assert '"pairingCode": "482913"' in pair_call.kwargs['data']
    assert '"organizationVenueId": "venue-1"' in pair_call.kwargs['data']
    assert 'venue-2' not in pair_call.kwargs['data']
    logon_body = http.call_args_list[1].kwargs['data']
    assert '"rememberDevice": true' in logon_body
    assert '"pdId"' in logon_body
    totp_body = http.call_args_list[2].kwargs['data']
    assert '"totpCode": "123456"' in totp_body


def test_configuration_forwards_the_query_and_bearer_token(monkeypatch):
    monkeypatch.setenv('PARTNER_API_URL', 'https://partner.example')
    upstream = MagicMock(
        status_code=200,
        content=b'{}',
        headers={'Content-Type': 'application/json'},
    )
    with patch('app.requests.request', return_value=upstream) as request:
        client = app.test_client()
        response = client.get(
            '/api/open/device/configuration?partnerId=partner-1&deviceId=device-1',
            headers={'Authorization': 'Bearer token-1'},
        )

    assert response.status_code == 200
    args, kwargs = request.call_args
    assert args == ('GET', 'https://partner.example/api/open/device/configuration?partnerId=partner-1&deviceId=device-1')
    assert kwargs['headers']['Authorization'] == 'Bearer token-1'


def expired_jwt():
    segment = base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) - 60}).encode()).decode().rstrip('=')
    return f'header.{segment}.sig'


def test_expired_bearer_token_returns_to_sign_in(monkeypatch, tmp_path):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    path = tmp_path / 'sessions.sqlite'
    monkeypatch.setenv('PAIRING_SESSION_PATH', str(path))
    token = expired_jwt()
    PairingSessionStore(str(path)).save('sid', {
        'pd_id': 'pd-1',
        'cookies': {'session': 'ok'},
        'jwt': token,
        'email': '',
        'password': '',
        'awaiting_totp': False,
        'venues': [{'id': 'venue-1', 'name': 'The Lockhart', 'organization_name': 'TipJAR'}],
        'organization_venue_id': 'venue-1',
        'venue_name': 'The Lockhart',
        'organization_name': 'TipJAR',
        'api_log': [],
    })
    client = app.test_client()
    client.set_cookie('pair_session', 'sid')
    response = client.get('/pair', headers=auth_header())

    assert response.status_code == 200
    assert b'Sign in to TipJAR' in response.data
    assert token.encode() not in response.data


def test_pair_page_requires_a_password(monkeypatch):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    client = app.test_client()
    response = client.get('/pair')
    assert response.status_code == 401
