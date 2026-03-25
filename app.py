import os
import stripe
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set your secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

app = Flask(__name__)
# Enable CORS so your app can call this endpoint
CORS(app)

@app.route('/connection_token', methods=['POST'])
def connection_token():
    try:
        # Debug: check if key is present
        key = os.getenv('STRIPE_SECRET_KEY')
        if not key:
            print("CRITICAL: STRIPE_SECRET_KEY not found in environment!")
        else:
            print(f"Using Stripe key: {key[:7]}...")
            stripe.api_key = key

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 4242))
    print(f"Starting mock Stripe Terminal SDK server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
