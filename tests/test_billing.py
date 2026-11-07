"""Stripe webhooks: signatures are checked, plans follow subscription events."""

import hashlib
import hmac
import json
import time

import pytest
from conftest import client, new_user

from cvmaker import accounts, billing


def _signed(event: dict, secret: str = "whsec_test", stamp: int | None = None) -> tuple[bytes, str]:
    payload = json.dumps(event).encode()
    stamp = stamp or int(time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={stamp},v1={sig}"


def test_verify_rejects_bad_and_old_signatures():
    payload, header = _signed({"type": "x"})
    assert billing.verify(payload, header)["type"] == "x"
    with pytest.raises(billing.BillingError):
        billing.verify(payload + b" ", header)
    old_payload, old_header = _signed({"type": "x"}, stamp=int(time.time()) - 3600)
    with pytest.raises(billing.BillingError):
        billing.verify(old_payload, old_header)
    with pytest.raises(billing.BillingError):
        billing.verify(*_signed({"type": "x"}, secret="whsec_other"))


def test_webhook_upgrades_and_downgrades():
    _, account = new_user()
    c = client()
    until = int(time.time()) + 30 * 86400
    payload, header = _signed({"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": account["id"], "customer": "cus_123", "subscription": "sub_123"}}})
    assert c.post("/billing/webhook", content=payload, headers={"stripe-signature": header, "Origin": ""}).status_code == 200
    assert accounts.plan_of(account["id"]) == "pro"
    payload, header = _signed({"type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_123", "customer": "cus_123", "status": "active", "current_period_end": until}}})
    assert c.post("/billing/webhook", content=payload, headers={"stripe-signature": header}).status_code == 200
    assert accounts.get(account["id"])["plan_until"] > until
    payload, header = _signed({"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_123", "customer": "cus_123"}}})
    c.post("/billing/webhook", content=payload, headers={"stripe-signature": header})
    assert accounts.plan_of(account["id"]) == "free"


def test_webhook_with_bad_signature_changes_nothing():
    _, account = new_user()
    payload, _ = _signed({"type": "checkout.session.completed", "data": {"object": {"client_reference_id": account["id"]}}})
    r = client().post("/billing/webhook", content=payload, headers={"stripe-signature": "t=1,v1=00"})
    assert r.status_code == 400
    assert accounts.plan_of(account["id"]) == "free"


def test_expired_paid_plan_counts_as_free():
    _, account = new_user()
    accounts.set_plan(account["id"], "pro", time.time() - 10)
    assert accounts.plan_of(account["id"]) == "free"
