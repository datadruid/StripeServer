import os
from functools import wraps

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
