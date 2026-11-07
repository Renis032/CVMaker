"""Sign-up, verification, login, logout, password reset and login links."""

from conftest import client, last_link, new_user

from cvmaker import accounts


def test_signup_needs_verification_then_works():
    c, account = new_user(verified=False)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/verify"
    link = last_link(account["email"], "/verify/")
    r = c.get(link)
    assert r.status_code == 303
    assert accounts.get(account["id"])["verified"] == 1
    assert c.get("/").status_code == 200
    # a verification link works once
    assert "expired" in client().get(link).text


def test_anonymous_is_sent_to_login_and_api_gets_401():
    c = client()
    r = c.get("/profile")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert c.get("/editor/api/data").status_code == 401
    assert c.get("/login").status_code == 200
    assert c.get("/legal/privacy").status_code == 200


def test_login_wrong_password_same_message_as_unknown_email():
    _, account = new_user()
    c = client()
    wrong = c.post("/login", data={"email": account["email"], "password": "nope nope nope"})
    unknown = c.post("/login", data={"email": "nobody@example.com", "password": "nope nope nope"})
    assert "Wrong email or password" in wrong.text and "Wrong email or password" in unknown.text
    ok = c.post("/login", data={"email": account["email"], "password": "correct horse battery", "next": "/pipeline"})
    assert ok.status_code == 303 and ok.headers["location"] == "/pipeline"
    assert c.get("/pipeline").status_code == 200


def test_login_redirect_target_must_be_local():
    _, account = new_user()
    r = client().post("/login", data={"email": account["email"], "password": "correct horse battery", "next": "//evil.example/x"})
    assert r.headers["location"] == "/"


def test_duplicate_signup_and_short_password():
    _, account = new_user()
    r = client().post("/signup", data={"email": account["email"], "password": "another long one", "agree": "1"})
    assert "already an account" in r.text
    r = client().post("/signup", data={"email": "short@example.com", "password": "short", "agree": "1"})
    assert "at least 10" in r.text


def test_logout_ends_the_session():
    c, _ = new_user()
    assert c.get("/").status_code == 200
    c.post("/logout")
    assert c.get("/").status_code == 303


def test_password_reset_flow():
    _, account = new_user()
    c = client()
    c.post("/forgot", data={"email": account["email"]})
    link = last_link(account["email"], "/reset/")
    r = c.post(link, data={"password": "a brand new password"})
    assert r.status_code == 303
    assert c.get("/").status_code == 200
    assert accounts.login(account["email"], "a brand new password")["id"] == account["id"]
    # the old password no longer works, the link is spent
    assert "Wrong" in client().post("/login", data={"email": account["email"], "password": "correct horse battery"}).text
    assert "expired" in client().post(link, data={"password": "yet another password"}).text


def test_forgot_does_not_reveal_whether_email_exists():
    a = client().post("/forgot", data={"email": "ghost@example.com"})
    _, account = new_user()
    b = client().post("/forgot", data={"email": account["email"]})
    assert a.status_code == b.status_code == 303 and a.headers["location"] == b.headers["location"]


def test_magic_link_logs_in_once():
    _, account = new_user()
    client().post("/forgot", data={"email": account["email"], "magic": "1"})
    link = last_link(account["email"], "/magic/")
    c = client()
    assert c.get(link).status_code == 303
    assert c.get("/").status_code == 200
    assert "expired" in client().get(link).text


def test_login_rate_limit():
    """Guessing one account's password is stopped (20 tries an hour per email)."""
    _, account = new_user()
    c = client()
    for _ in range(25):
        r = c.post("/login", data={"email": account["email"], "password": "wrong password!"})
        if "Too many attempts" in r.text:
            break
    assert "Too many attempts" in r.text


def test_password_is_hashed():
    _, account = new_user()
    stored = accounts.get(account["id"])["password_hash"]
    assert stored.startswith("scrypt$") and "correct horse" not in stored
