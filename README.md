# Mock Stripe Terminal Server

This is a local server built with Python and Flask that creates short-lived connection tokens for the Stripe Terminal SDK. This allows your mobile app to connect to Stripe and create/confirm Terminal payments in development.

## Requirements

1. Python 3+
2. Your Stripe Test Secret Key

## Setup & Running

1. Activate the Python virtual environment:
   ```bash
   source venv/bin/activate
   ```
2. Set up your environment variables:
   ```bash
   cp .env.example .env
   ```
   Then open `.env` and replace `sk_test_YOUR_STRIPE_SECRET_KEY` with your actual Stripe secret key from the Stripe Dashboard.
3. Start the server:
   ```bash
   python app.py
   ```
   The server will start on `http://0.0.0.0:4242` enabling access from other devices on your local Wi-Fi.

## Using The Endpoint

In your mobile app, configure your `Terminal` provider to post to:
**URL:** `http://192.168.0.188:4242/connection_token` (Replace `192.168.0.188` with your mac's current local IP if it changes).
**Method:** `POST`

The endpoint will return the following JSON on success:
```json
{
  "secret": "tct_test_..."
}
```

### Create Payment Intent

In your mobile app, you can create a PaymentIntent for Terminal by posting to:
**URL:** `http://192.168.0.188:4242/create_payment_intent`
**Method:** `POST`

**Request Body (JSON):**
```json
{
  "amount": 1000
}
```

The endpoint will return the client secret:
```json
{
  "client_secret": "pi_3..."
}
```
