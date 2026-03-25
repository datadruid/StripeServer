# C# Stripe Terminal Server — Spec

## Requirement

A C# ASP.NET Core server that acts as a backend for the Stripe Terminal SDK. It exposes two endpoints:

- `POST /connection_token` — returns a short-lived connection token so the mobile app can authenticate with Stripe Terminal.
- `POST /create_payment_intent` — creates a Terminal payment intent and returns the client secret.

---

## Creating a Connection Token with Stripe.net

Add the `Stripe.net` NuGet package, then use `ConnectionTokenService` from the `Stripe.Terminal` namespace:

```csharp
using Stripe.Terminal;

StripeConfiguration.ApiKey = "sk_test_...";

app.MapPost("/connection_token", async () =>
{
    var service = new ConnectionTokenService();
    var token = await service.CreateAsync(new ConnectionTokenCreateOptions());
    return Results.Ok(new { secret = token.Secret });
});
```

**Response:**
```json
{ "secret": "tct_test_..." }
```

---

## Creating a Payment Intent with Stripe.net

```csharp
using Stripe;

app.MapPost("/create_payment_intent", async (CreatePaymentIntentRequest body) =>
{
    var service = new PaymentIntentService();
    var intent = await service.CreateAsync(new PaymentIntentCreateOptions
    {
        Amount = body.Amount,       // in pence, e.g. 1000 = £10.00
        Currency = "gbp",
        PaymentMethodTypes = new List<string> { "card_present" },
        CaptureMethod = "manual",
    });
    return Results.Ok(new { client_secret = intent.ClientSecret });
});
```

**Request body:**
```json
{ "amount": 1000 }
```

**Response:**
```json
{ "client_secret": "pi_3..." }
```
