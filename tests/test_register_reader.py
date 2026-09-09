import base64
from unittest.mock import MagicMock, patch

import pytest

from app import app

app.config['TESTING'] = True

REGISTER_PASSWORD = 'test-pass'
LOCATION_ID = 'tml_FoRubQTyJs4cwC'


def auth_header(password=REGISTER_PASSWORD, username='admin'):
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('REGISTER_READERS_PASSWORD', REGISTER_PASSWORD)
    with app.test_client() as test_client:
        yield test_client


def test_register_requires_auth(client):
    response = client.get('/register')
    assert response.status_code == 401
    assert 'Basic' in response.headers.get('WWW-Authenticate', '')


def test_register_rejects_wrong_password(client):
    response = client.get('/register', headers=auth_header(password='nope'))
    assert response.status_code == 401


def test_register_disabled_when_password_unset(monkeypatch):
    monkeypatch.delenv('REGISTER_READERS_PASSWORD', raising=False)
    with app.test_client() as test_client:
        response = test_client.get('/register', headers=auth_header())
        assert response.status_code == 503


def test_register_form_shown_when_authenticated(client):
    response = client.get('/register', headers=auth_header())
    assert response.status_code == 200
    assert b'name="registration_code"' in response.data


def test_register_post_requires_auth(client):
    response = client.post(
        '/register',
        data={'registration_code': 'puppies-plug-scrub'},
    )
    assert response.status_code == 401


@patch('app.stripe.terminal.Reader.create')
def test_register_uses_pairing_code_as_label(mock_create, client):
    mock_create.return_value = MagicMock(
        id='tmr_123',
        label='puppies-plug-scrub',
        serial_number='SN-1',
        status='online',
    )

    response = client.post(
        '/register',
        data={'registration_code': '  puppies-plug-scrub  '},
        headers=auth_header(),
    )

    assert response.status_code == 200
    mock_create.assert_called_once_with(
        registration_code='puppies-plug-scrub',
        location=LOCATION_ID,
        label='puppies-plug-scrub',
    )
    assert b'tmr_123' in response.data
    assert b'SN-1' in response.data


@patch('app.stripe.terminal.Reader.create')
def test_register_shows_stripe_error(mock_create, client):
    mock_create.side_effect = Exception('Invalid registration code')

    response = client.post(
        '/register',
        data={'registration_code': 'bad-code'},
        headers=auth_header(),
    )

    assert response.status_code == 200
    assert b'Invalid registration code' in response.data
    assert b'tmr_' not in response.data


@patch('app.stripe.terminal.Reader.create')
def test_register_requires_pairing_code(mock_create, client):
    response = client.post(
        '/register',
        data={'registration_code': '   '},
        headers=auth_header(),
    )

    assert response.status_code == 200
    assert b'required' in response.data.lower()
    mock_create.assert_not_called()
