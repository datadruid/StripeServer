import os
import uuid
from functools import wraps

import requests
import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

# Load environment variables from .env file
load_dotenv()

# Set your secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

TERMINAL_LOCATION_ID = 'tml_FoRubQTyJs4cwC'

app = Flask(__name__)
# Enable CORS so your app can call this endpoint
CORS(app)


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


@app.route('/pair', methods=['GET', 'POST'])
@require_register_password
def pair_device():
    error = None
    paired = None
    pairing_code = ''
    venue_id = ''

    if request.method == 'POST':
        pairing_code = (request.form.get('pairing_code') or '').strip()
        venue_id = (request.form.get('venue_id') or '').strip()
        if not pairing_code or not venue_id:
            error = 'Pairing code and venue are required'
        else:
            try:
                upstream = requests.post(
                    f'{front_door_url()}/api/dashboard/entity/device/pair',
                    json={'PairingCode': pairing_code, 'VenueId': venue_id},
                    timeout=30,
                )
            except requests.RequestException as exc:
                error = str(exc)
            else:
                if upstream.status_code < 200 or upstream.status_code >= 300:
                    error = upstream.text or f'Pairing failed ({upstream.status_code})'
                else:
                    paired = {'pairing_code': pairing_code, 'venue_id': venue_id}

    return render_template(
        'pair.html',
        error=error,
        paired=paired,
        pairing_code=pairing_code,
        venue_id=venue_id,
    )


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
