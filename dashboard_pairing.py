import base64
import contextvars
import json
import sqlite3
import time
from contextlib import closing, contextmanager

API_VERSION = '2016-08-01'
CLIENT_VERSION = '8.0.26121.10'
SESSION_TTL_SECONDS = 2 * 60 * 60
_request_trace = contextvars.ContextVar('dashboard_request_trace', default=None)


def cookie_header(cookies):
    return '; '.join(f'{name}={value}' for name, value in cookies.items())


def merge_cookies(current, response):
    merged = dict(current)
    for cookie in getattr(response, 'cookies', []) or []:
        name = getattr(cookie, 'name', None)
        value = getattr(cookie, 'value', None)
        if name and value:
            merged[name] = value
    return merged


def error_message(payload, fallback):
    if isinstance(payload, dict):
        message = payload.get('Description') or payload.get('description')
        if message:
            return message
    return fallback


def is_two_factor_challenge(status, payload):
    if status != 403 or not isinstance(payload, dict):
        return False
    return payload.get('Subcode', payload.get('subcode')) == 1110


def is_logged_on(payload):
    return isinstance(payload, dict) and bool(payload.get('isLoggedOn'))


def read_venues(payload):
    items = []
    if isinstance(payload, dict):
        items = (payload.get('results') or {}).get('items') or []
    venues = []
    for item in items:
        if not isinstance(item, dict):
            continue
        primary = item.get('primary') or {}
        organization = item.get('organization') or {}
        venue_id = item.get('id') or primary.get('id')
        organization_id = organization.get('id')
        if not venue_id or not organization_id:
            continue
        name = (primary.get('displayName') or primary.get('description') or '').strip()
        organization_name = (
            organization.get('displayName') or organization.get('title') or organization.get('description') or ''
        ).strip()
        venues.append({
            'id': venue_id,
            'name': name or 'Venue',
            'organization_id': organization_id,
            'organization_name': organization_name or 'Organization',
        })
    venues.sort(key=lambda venue: venue['name'].casefold())
    return venues


def jwt_expiry(token):
    if not isinstance(token, str) or token.count('.') != 2:
        return None
    segment = token.split('.')[1]
    padding = '=' * (-len(segment) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + padding))
    except Exception:
        return None
    exp = payload.get('exp') if isinstance(payload, dict) else None
    if isinstance(exp, (int, float)):
        return int(exp)
    return None


def token_is_expired(token, now=None):
    exp = jwt_expiry(token)
    if exp is None:
        return False
    if now is None:
        now = time.time()
    return exp <= now


def redact_body(body):
    if not isinstance(body, dict):
        return body
    redacted = dict(body)
    if 'password' in redacted:
        redacted['password'] = '***'
    return redacted


def format_payload(payload, fallback):
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2)
    return fallback or ''


@contextmanager
def capture_trace():
    entries = []
    token = _request_trace.set(entries)
    try:
        yield entries
    finally:
        _request_trace.reset(token)


def record_trace(url, headers, body, status, response):
    entries = _request_trace.get()
    if entries is None:
        return
    entries.append({
        'method': 'POST',
        'url': url,
        'status': status,
        'request_headers': headers,
        'request': json.dumps(redact_body(body), indent=2) if body is not None else '',
        'response': response,
    })


def front_door_request(http, base_url, path, cookies, authorization=None, body=None):
    headers = {
        'Accept': 'application/json',
        'x-api-version': API_VERSION,
        'x-api-clientversion': CLIENT_VERSION,
    }
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body)
    header = cookie_header(cookies)
    if header:
        headers['Cookie'] = header
    if authorization:
        headers['Authorization'] = f'bearer {authorization}'
    url = f'{base_url.rstrip("/")}{path}'
    try:
        response = http.request(
            'POST',
            url,
            headers=headers,
            data=data,
            timeout=30,
        )
    except Exception as exc:
        record_trace(url, headers, body, 'error', str(exc))
        raise DashboardError(str(exc)) from exc
    try:
        payload = response.json()
    except Exception:
        payload = None
    record_trace(url, headers, body, response.status_code, format_payload(payload, getattr(response, 'text', '')))
    return response.status_code, payload, merge_cookies(cookies, response)


class DashboardError(Exception):
    pass


def start_session(http, base_url, cookies):
    status, payload, cookies = front_door_request(
        http,
        base_url,
        '/api/security/sessioncreate',
        cookies,
    )
    token = None
    if isinstance(payload, dict):
        token = payload.get('Token') or payload.get('token')
    if status < 200 or status >= 300 or not token:
        raise DashboardError(error_message(payload, 'Unable to start a TipJAR session.'))
    return token, cookies


def log_on_device(http, base_url, cookies, jwt, email, password, pd_id, totp_code=None):
    body = {
        'email': email,
        'password': password,
        'pdId': pd_id,
        'deviceId': None,
        'deviceType': 'StripeServer',
        'rememberDevice': True,
    }
    if totp_code:
        body['totpCode'] = totp_code
    status, payload, cookies = front_door_request(
        http,
        base_url,
        '/api/security/logon/device',
        cookies,
        authorization=jwt,
        body=body,
    )
    return status, payload, cookies


def load_venues(http, base_url, cookies, jwt):
    status, payload, cookies = front_door_request(
        http,
        base_url,
        '/api/dashboard/search/organization/venue',
        cookies,
        authorization=jwt,
        body={'top': 100, 'skip': 0, 'timeZoneOffset': 0},
    )
    if status < 200 or status >= 300:
        raise DashboardError(error_message(payload, 'Unable to load venues.'))
    return read_venues(payload), cookies


def pair_device(http, base_url, cookies, jwt, pairing_code, organization_venue_id):
    status, payload, _cookies = front_door_request(
        http,
        base_url,
        '/api/dashboard/entity/device/pair',
        cookies,
        authorization=jwt,
        body={
            'pairingCode': pairing_code,
            'organizationVenueId': organization_venue_id,
        },
    )
    if status < 200 or status >= 300:
        raise DashboardError(error_message(payload, f'Pairing failed ({status}).'))
    return payload


class PairingSessionStore:
    def __init__(self, path):
        self.path = path
        with closing(self._connect()) as connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                '''
            )
            connection.commit()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def load(self, session_id):
        now = int(time.time())
        with closing(self._connect()) as connection:
            connection.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            row = connection.execute(
                'SELECT payload FROM sessions WHERE id = ?',
                (session_id,),
            ).fetchone()
            connection.commit()
        if row is None:
            return None
        return json.loads(row[0])

    def save(self, session_id, payload, ttl_seconds=SESSION_TTL_SECONDS):
        expires_at = int(time.time()) + ttl_seconds
        with closing(self._connect()) as connection:
            connection.execute(
                '''
                INSERT INTO sessions (id, payload, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    expires_at = excluded.expires_at
                ''',
                (session_id, json.dumps(payload), expires_at),
            )
            connection.commit()

    def delete(self, session_id):
        with closing(self._connect()) as connection:
            connection.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            connection.commit()
