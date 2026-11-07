"""Paid plans through Stripe (server mode; off unless STRIPE_SECRET_KEY is set). Desktop mode is free.

- Checkout: a Stripe-hosted page for the Pro subscription (STRIPE_PRICE_PRO). Turn on Stripe Tax in the dashboard
  so EU VAT is charged and invoiced correctly.
- Customer portal: Stripe's page to change card, see invoices and cancel.
- Webhook (/billing/webhook): Stripe tells the app when a subscription starts, renews, changes or ends; the app
  sets the account's plan and paid-until date. The signature is checked with STRIPE_WEBHOOK_SECRET.

Talks to Stripe's REST API with httpx (no Stripe library needed).
"""

import hashlib
import hmac
import json
import time

import httpx

from . import accounts, settings

API = "https://api.stripe.com/v1"
TOLERANCE_SECONDS = 300


class BillingError(RuntimeError):
    pass


def _post(path: str, data: dict) -> dict:
    r = httpx.post(f"{API}{path}", data=data, auth=(settings.STRIPE_SECRET_KEY, ""), timeout=20)
    if r.status_code >= 300:
        raise BillingError(f"Stripe: {r.json().get('error', {}).get('message', r.text[:200])}")
    return r.json()


def checkout_url(account: dict) -> str:
    data = {
        "mode": "subscription",
        "line_items[0][price]": settings.STRIPE_PRICE_PRO,
        "line_items[0][quantity]": "1",
        "client_reference_id": account["id"],
        "success_url": f"{settings.PUBLIC_URL}/settings?billing=done#plan",
        "cancel_url": f"{settings.PUBLIC_URL}/settings#plan",
        "automatic_tax[enabled]": "true",
        "allow_promotion_codes": "true",
        "subscription_data[metadata][account_id]": account["id"],
    }
    if account.get("stripe_customer"):
        data["customer"] = account["stripe_customer"]
        data["customer_update[address]"] = "auto"
    else:
        data["customer_email"] = account["email"]
    return _post("/checkout/sessions", data)["url"]


def portal_url(account: dict) -> str:
    if not account.get("stripe_customer"):
        raise BillingError("No subscription yet.")
    return _post("/billing_portal/sessions", {"customer": account["stripe_customer"], "return_url": f"{settings.PUBLIC_URL}/settings#plan"})["url"]


def verify(payload: bytes, header: str, secret: str | None = None, now: float | None = None) -> dict:
    """The event in a webhook request, after checking Stripe's signature (and that it isn't an old replay)."""
    secret = secret or settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET is not set")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        key, _, value = item.partition("=")
        parts.setdefault(key.strip(), []).append(value.strip())
    try:
        stamp = int(parts["t"][0])
    except (KeyError, ValueError) as e:
        raise BillingError("Bad signature header") from e
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise BillingError("Bad signature")
    if abs((now or time.time()) - stamp) > TOLERANCE_SECONDS:
        raise BillingError("Signature too old")
    return json.loads(payload)


def _account_for(obj: dict) -> str | None:
    account_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("account_id")
    if account_id:
        return account_id
    from . import central

    customer = obj.get("customer")
    if customer:
        with central.connect() as c:
            row = c.one("SELECT id FROM accounts WHERE stripe_customer = ?", (customer,))
        return row["id"] if row else None
    return None


def handle(event: dict) -> str:
    """Apply a webhook event to the account. Returns what was done (for the log)."""
    kind = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    account_id = _account_for(obj)
    if not account_id or not accounts.get(account_id):
        return f"ignored {kind}: no account"
    if kind == "checkout.session.completed":
        accounts.set_plan(account_id, "pro", time.time() + 3 * 86400, customer=obj.get("customer"), subscription=obj.get("subscription"))
        return f"{account_id}: pro (checkout)"
    if kind in {"customer.subscription.created", "customer.subscription.updated"}:
        active = obj.get("status") in {"active", "trialing", "past_due"}
        until = float(obj.get("current_period_end") or (obj.get("items", {}).get("data") or [{}])[0].get("current_period_end") or 0)
        accounts.set_plan(account_id, "pro" if active else "free", until + 86400 if active else 0,
                          customer=obj.get("customer"), subscription=obj.get("id"))
        return f"{account_id}: {'pro' if active else 'free'} ({obj.get('status')})"
    if kind == "customer.subscription.deleted":
        accounts.set_plan(account_id, "free", 0)
        return f"{account_id}: free (cancelled)"
    return f"ignored {kind}"
