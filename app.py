import os
import secrets
import time
import uuid
from functools import wraps

import requests
import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, make_response, render_template, request
from flask_cors import CORS

from dashboard_pairing import (
    SESSION_TTL_SECONDS,
    DashboardError,
    PairingSessionStore,
    capture_trace,
    is_logged_on,
    is_two_factor_challenge,
    jwt_expiry,
    load_venues,
    log_on_device,
    pair_device as submit_device_pair,
    start_session,
    token_is_expired,
)

# Load environment variables from .env file
load_dotenv()

# Set your secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

TERMINAL_LOCATION_ID = 'tml_FoRubQTyJs4cwC'

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY') or os.getenv('REGISTER_READERS_PASSWORD') or 'dev-pairing-secret'
# Enable CORS so your app can call this endpoint
CORS(app)

PAIR_SESSION_COOKIE = 'pair_session'


def require_register_password(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.getenv('REGISTER_READERS_PASSWORD')
        if not expected:
            return jsonify({'error': 'Reader registration is not configured'}), 503

        auth = request.authorization
        if auth is None or auth.password != expected:
            return Response(
                'Authentication required',
                401,
                {'WWW-Authenticate': 'Basic realm="Register reader"'},
            )
        return view(*args, **kwargs)

    return wrapped


@app.route('/connection_token', methods=['POST'])
def connection_token():
    try:
        # Create a ConnectionToken using Stripe SDK
        token = stripe.terminal.ConnectionToken.create()
        # Return the secret in a JSON object as required by the Terminal SDK
        return jsonify({'secret': token.secret})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/create_payment_intent', methods=['POST'])
def create_payment_intent():
    try:
        data = request.get_json() or {}
        amount = data.get('amount', 1000)
        
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='gbp',
            payment_method_types=['card_present'],
            capture_method='manual',
        )
        return jsonify({
            'client_secret': intent.client_secret,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/transaction_guid', methods=['POST'])
def transaction_guid():
    return jsonify({'transaction_guid': str(uuid.uuid4())})


def partner_api_url():
    return os.getenv('PARTNER_API_URL', 'https://tpjr-dev-api-partner.azurewebsites.net').rstrip('/')


def front_door_url():
    return os.getenv('FRONT_DOOR_URL', 'https://dev.tipjar.tech').rstrip('/')


def proxy_partner(path):
    url = f'{partner_api_url()}{path}'
    if request.query_string:
        url = f'{url}?{request.query_string.decode()}'
    headers = {'Accept': 'application/json'}
    authorization = request.headers.get('Authorization')
    if authorization:
        headers['Authorization'] = authorization
    data = None
    if request.method != 'GET':
        headers['Content-Type'] = 'application/json'
        data = request.get_data()
    try:
        upstream = requests.request(request.method, url, headers=headers, data=data, timeout=30)
    except requests.RequestException as exc:
        return jsonify({'error': str(exc)}), 502
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get('Content-Type', 'application/json'),
    )


@app.route('/api/open/device/login', methods=['POST'])
def device_login():
    return proxy_partner('/api/open/device/login')


@app.route('/api/open/device/pairing/request-code', methods=['POST'])
def device_request_code():
    return proxy_partner('/api/open/device/pairing/request-code')


@app.route('/api/open/device/status', methods=['GET'])
def device_status():
    return proxy_partner('/api/open/device/status')


@app.route('/api/open/device/configuration', methods=['GET'])
def device_configuration():
    return proxy_partner('/api/open/device/configuration')


def pairing_store():
    path = os.getenv('PAIRING_SESSION_PATH')
    if not path:
        os.makedirs(app.instance_path, exist_ok=True)
        path = os.path.join(app.instance_path, 'pairing-sessions.sqlite')
    return PairingSessionStore(path)


def blank_pair_session():
    return {
        'pd_id': str(uuid.uuid4()),
        'cookies': {},
        'jwt': None,
        'email': '',
        'password': '',
        'awaiting_totp': False,
        'venues': [],
        'organization_venue_id': '',
        'venue_name': '',
        'organization_name': '',
        'api_log': [],
    }


def clear_login(record):
    record['cookies'] = {}
    record['jwt'] = None
    record['email'] = ''
    record['password'] = ''
    record['awaiting_totp'] = False
    record['venues'] = []
    record['organization_venue_id'] = ''
    record['venue_name'] = ''
    record['organization_name'] = ''


def session_ttl(record):
    exp = jwt_expiry(record.get('jwt'))
    if exp:
        remaining = exp - int(time.time())
        if remaining > 0:
            return remaining
    return SESSION_TTL_SECONDS


def pair_step(record):
    if record.get('awaiting_totp'):
        return 'totp'
    if not record.get('jwt'):
        return 'login'
    if not record.get('organization_venue_id'):
        return 'venue'
    return 'code'


def selected_venue(record, venue_id):
    for venue in record.get('venues') or []:
        if venue.get('id') == venue_id:
            return venue
    return None


def remember_login(record, status, payload, cookies, email, password):
    record['cookies'] = cookies
    if is_two_factor_challenge(status, payload):
        record['awaiting_totp'] = True
        record['email'] = email
        record['password'] = password
        return None
    if status < 200 or status >= 300:
        raise DashboardError(error_message_from(payload, 'Unable to sign in.'))
    if not is_logged_on(payload):
        raise DashboardError('Unable to verify the sign-in code.')
    refreshed = payload.get('Token') or payload.get('token')
    if refreshed:
        record['jwt'] = refreshed
    record['awaiting_totp'] = False
    record['email'] = ''
    record['password'] = ''
    record['venues'], record['cookies'] = load_venues(
        requests,
        front_door_url(),
        cookies,
        record['jwt'],
    )
    if not record['venues']:
        raise DashboardError('No venues are available for this account.')
    return None


def error_message_from(payload, fallback):
    if isinstance(payload, dict):
        message = payload.get('Description') or payload.get('description')
        if message:
            return message
    return fallback


@app.route('/pair', methods=['GET', 'POST'])
@require_register_password
def pair_page():
    store = pairing_store()
    session_id = request.cookies.get(PAIR_SESSION_COOKIE)
    record = store.load(session_id) if session_id else None
    if record is None:
        session_id = secrets.token_urlsafe(32)
        record = blank_pair_session()
    if token_is_expired(record.get('jwt')):
        clear_login(record)

    error = None
    paired = None
    pairing_code = ''

    with capture_trace() as trace:
        if request.method == 'POST':
            action = request.form.get('action') or ''
            try:
                if action == 'login':
                    email = (request.form.get('email') or '').strip()
                    password = request.form.get('password') or ''
                    if not email or not password:
                        raise DashboardError('Email and password are required.')
                    record['jwt'], record['cookies'] = start_session(
                        requests,
                        front_door_url(),
                        record.get('cookies') or {},
                    )
                    status, payload, cookies = log_on_device(
                        requests,
                        front_door_url(),
                        record['cookies'],
                        record['jwt'],
                        email,
                        password,
                        record['pd_id'],
                    )
                    remember_login(record, status, payload, cookies, email, password)
                elif action == 'totp':
                    code = (request.form.get('totp_code') or '').strip()
                    if not record.get('awaiting_totp') or not record.get('jwt'):
                        raise DashboardError('Sign in before entering the code.')
                    if len(code) != 6 or not code.isdigit():
                        raise DashboardError('Enter the 6-digit code.')
                    status, payload, cookies = log_on_device(
                        requests,
                        front_door_url(),
                        record.get('cookies') or {},
                        record['jwt'],
                        record.get('email') or '',
                        record.get('password') or '',
                        record['pd_id'],
                        totp_code=code,
                    )
                    remember_login(
                        record,
                        status,
                        payload,
                        cookies,
                        record.get('email') or '',
                        record.get('password') or '',
                    )
                elif action == 'change_venue':
                    if not record.get('jwt') or record.get('awaiting_totp'):
                        raise DashboardError('Sign in before choosing a venue.')
                    record['organization_venue_id'] = ''
                    record['venue_name'] = ''
                    record['organization_name'] = ''
                elif action == 'venue':
                    if not record.get('jwt') or record.get('awaiting_totp'):
                        raise DashboardError('Sign in before choosing a venue.')
                    venue = selected_venue(record, (request.form.get('venue_id') or '').strip())
                    if venue is None:
                        raise DashboardError('Choose a venue from the list.')
                    record['organization_venue_id'] = venue['id']
                    record['venue_name'] = venue['name']
                    record['organization_name'] = venue['organization_name']
                elif action == 'pair':
                    pairing_code = (request.form.get('pairing_code') or '').strip()
                    venue_id = record.get('organization_venue_id') or ''
                    if not record.get('jwt') or not venue_id:
                        raise DashboardError('Sign in and choose a venue before pairing.')
                    if not pairing_code:
                        raise DashboardError('Pairing code is required.')
                    submit_device_pair(
                        requests,
                        front_door_url(),
                        record.get('cookies') or {},
                        record['jwt'],
                        pairing_code,
                        venue_id,
                    )
                    paired = {
                        'pairing_code': pairing_code,
                        'venue_name': record.get('venue_name') or venue_id,
                    }
                    pairing_code = ''
                else:
                    raise DashboardError('Unable to continue pairing.')
            except DashboardError as exc:
                error = str(exc)
                if action == 'login' and not record.get('awaiting_totp'):
                    record['jwt'] = None
                record['password'] = ''

    record['api_log'] = (record.get('api_log') or []) + trace
    record['api_log'] = record['api_log'][-20:]
    store.save(session_id, record, session_ttl(record))

    response = make_response(render_template(
        'pair.html',
        error=error,
        paired=paired,
        step=pair_step(record),
        venues=record.get('venues') or [],
        pairing_code=pairing_code,
        venue_name=record.get('venue_name') or '',
        organization_name=record.get('organization_name') or '',
        bearer_token=record.get('jwt') or '',
        api_log=record.get('api_log') or [],
    ))
    response.set_cookie(
        PAIR_SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite='Lax',
        secure=request.is_secure,
    )
    return response


@app.route('/register', methods=['GET', 'POST'])
@require_register_password
def register_reader():
    error = None
    reader = None

    if request.method == 'POST':
        registration_code = (request.form.get('registration_code') or '').strip()
        if not registration_code:
            error = 'Pairing code is required'
        else:
            try:
                reader = stripe.terminal.Reader.create(
                    registration_code=registration_code,
                    location=TERMINAL_LOCATION_ID,
                    label=registration_code,
                )
            except Exception as e:
                error = str(e)

    return render_template('register.html', error=error, reader=reader)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 4242))
    print(f"Starting mock Stripe Terminal SDK server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
