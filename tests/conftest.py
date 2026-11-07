"""Tests run CVMaker in server mode (accounts, limits, CSRF) against a throw-away data folder. Desktop-mode checks
run in a subprocess (tests/test_desktop.py), because the mode is read once at start-up."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

_DATA = Path(tempfile.mkdtemp(prefix="cvmaker-tests-"))
os.environ.update({
    "CVMAKER_MODE": "server",
    "CVMAKER_DATA": str(_DATA),
    "CVMAKER_SECRET_KEY": "test-secret-key-for-tests-only",
    "CVMAKER_BACKEND": "api",
    "CVMAKER_PUBLIC_URL": "http://testserver",
    "CVMAKER_MAIL": "console",
    "CVMAKER_TASK_WORKERS": "0",
    "CVMAKER_SIGNUPS_PER_IP_HOUR": "10000",
    "CVMAKER_LOGINS_PER_IP_MINUTE": "10000",
    "CVMAKER_BACKUP_EVERY_HOURS": "0",
    "ANTHROPIC_API_KEY": "",
    "DATABASE_URL": os.environ.get("CVMAKER_TEST_DATABASE_URL", f"sqlite:///{_DATA / 'central.db'}"),
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_test",
    "STRIPE_PRICE_PRO": "price_test",
})

from fastapi.testclient import TestClient  # noqa: E402

from cvmaker import accounts, settings  # noqa: E402
from cvmaker.app import app  # noqa: E402

ORIGIN = {"Origin": "http://testserver"}


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA, ignore_errors=True)


def outbox() -> list[dict]:
    folder = settings.DATA_ROOT / "outbox"
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def last_link(to: str, path: str) -> str:
    for mail in reversed(outbox()):
        if mail["to"] == to:
            m = re.search(rf"http://testserver({re.escape(path)}[\w\-]+)", mail["text"])
            if m:
                return m.group(1)
    raise AssertionError(f"no {path} link mailed to {to}")


def client() -> TestClient:
    return TestClient(app, headers=ORIGIN, follow_redirects=False)


_n = [0]


def new_user(verified: bool = True, password: str = "correct horse battery") -> tuple[TestClient, dict]:
    """A logged-in client for a fresh account."""
    _n[0] += 1
    email = f"user{_n[0]}-{os.getpid()}@example.com"
    c = client()
    r = c.post("/signup", data={"email": email, "password": password, "name": f"User {_n[0]}", "agree": "1"})
    assert r.status_code == 303, r.text
    account = accounts.by_email(email)
    if verified:
        accounts.mark_verified(account["id"])
    return c, accounts.get(account["id"])


@pytest.fixture
def user():
    return new_user()
