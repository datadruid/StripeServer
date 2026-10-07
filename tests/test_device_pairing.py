import base64
from unittest.mock import MagicMock, patch

from app import app

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


def test_pair_page_posts_code_and_venue_to_frontdoor(monkeypatch):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    monkeypatch.setenv('FRONT_DOOR_URL', 'https://frontdoor.example')
    upstream = MagicMock(status_code=200, text='paired', json=lambda: {'ok': True})
    with patch('app.requests.post', return_value=upstream) as post:
        client = app.test_client()
        response = client.post(
            '/pair',
            data={'pairing_code': ' 482913 ', 'venue_id': ' venue-1 '},
            headers=auth_header(),
        )

    assert response.status_code == 200
    assert b'482913' in response.data
    post.assert_called_once_with(
        'https://frontdoor.example/api/dashboard/entity/device/pair',
        json={'PairingCode': '482913', 'VenueId': 'venue-1'},
        timeout=30,
    )


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


def test_pair_page_requires_a_password(monkeypatch):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    client = app.test_client()
    response = client.get('/pair')
    assert response.status_code == 401
