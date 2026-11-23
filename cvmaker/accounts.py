"""Accounts for server mode: sign-up, passwords, login sessions, email tokens (verify, reset, magic link), Google
sign-in, plans, and deleting an account. Desktop mode has no accounts (everything belongs to the "local" one).

Secrets are never stored as given: passwords are scrypt hashes; session and email tokens are random and only their
SHA-256 is kept, so a copy of the database can't be used to log in.
"""

import hashlib
import hmac
import re
import secrets
import shutil
import time

from . import central, log, people, settings

SESSION_COOKIE = "cvmaker_session"
SESSION_DAYS = 30
TOKEN_HOURS = {"verify": 48, "reset": 2, "magic": 0.25}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


class AccountError(ValueError):
    """Shown to the person as is."""


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError("Enter a valid email address.")
    return email


# --- passwords -----------------------------------------------------------------------------------------------------

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def check_new_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise AccountError("That password is too long.")


# --- accounts ------------------------------------------------------------------------------------------------------


def get(account_id: str) -> dict | None:
    with central.connect() as c:
        return c.one("SELECT * FROM accounts WHERE id = ? AND deleted = 0", (account_id,))


def by_email(email: str) -> dict | None:
    with central.connect() as c:
        return c.one("SELECT * FROM accounts WHERE email = ? AND deleted = 0", (email,))


def create(email: str, password: str | None, name: str = "", verified: bool = False, google_sub: str | None = None) -> dict:
    email = normalise_email(email)
    if password is not None:
        check_new_password(password)
    if by_email(email):
        raise AccountError("There is already an account with that email. Log in, or reset your password.")
    account_id = secrets.token_hex(8)
    with central.connect() as c:
        c.execute(
            "INSERT INTO accounts (id, email, name, password_hash, verified, google_sub, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, email, name.strip()[:100], hash_password(password) if password else "", int(verified), google_sub, time.time()),
        )
    people.account_root(account_id).mkdir(parents=True, exist_ok=True)
    return get(account_id)


def login(email: str, password: str) -> dict:
    """The account for this email and password, or AccountError (the same message either way)."""
    try:
        account = by_email(normalise_email(email))
    except AccountError:
        account = None
    # Hash anyway so a missing account takes as long as a wrong password.
    ok = check_password(password or "", account["password_hash"] if account and account["password_hash"] else hash_password("x"))
    if not account or not account["password_hash"] or not ok:
        raise AccountError("Wrong email or password.")
    return account


def set_password(account_id: str, password: str) -> None:
    check_new_password(password)
    with central.connect() as c:
        c.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(password), account_id))
    end_sessions(account_id)


def mark_verified(account_id: str) -> None:
    with central.connect() as c:
        c.execute("UPDATE accounts SET verified = 1 WHERE id = ?", (account_id,))


def google_account(sub: str, email: str, name: str) -> dict:
    """Log in with Google: the account linked to this Google user, else the account with that (verified) email,
    else a new one."""
    with central.connect() as c:
        found = c.one("SELECT * FROM accounts WHERE google_sub = ? AND deleted = 0", (sub,))
    if found:
        return found
    existing = by_email(normalise_email(email))
    if existing:
        with central.connect() as c:
            c.execute("UPDATE accounts SET google_sub = ?, verified = 1 WHERE id = ?", (sub, existing["id"]))
        return get(existing["id"])
    return create(email, None, name=name, verified=True, google_sub=sub)


def set_plan(account_id: str, plan: str, until: float = 0, customer: str | None = None, subscription: str | None = None) -> None:
    with central.connect() as c:
        c.execute("UPDATE accounts SET plan = ?, plan_until = ? WHERE id = ?", (plan, until, account_id))
        if customer is not None:
            c.execute("UPDATE accounts SET stripe_customer = ? WHERE id = ?", (customer, account_id))
        if subscription is not None:
            c.execute("UPDATE accounts SET stripe_subscription = ? WHERE id = ?", (subscription, account_id))


def plan_of(account_id: str) -> str:
    """The plan in force now ("free" once a paid period has ended)."""
    if settings.DESKTOP:
        return "pro"
    account = get(account_id)
    if not account:
        return "free"
    if account["plan"] != "free" and account["plan_until"] and account["plan_until"] < time.time():
        return "free"
    return account["plan"]


def delete(account_id: str) -> None:
    """Delete an account: sessions and tokens go now, the account row is anonymised, and its data folder is wiped
    (or, with DELETED_RETENTION_DAYS, moved aside and wiped by `purge_deleted` after that many days)."""
    end_sessions(account_id)
    with central.connect() as c:
        c.execute("DELETE FROM email_tokens WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM queue WHERE account_id = ? AND status = 'queued'", (account_id,))
        c.execute(
            "UPDATE accounts SET deleted = ?, email = NULL, name = '', password_hash = '', google_sub = NULL WHERE id = ?",
            (time.time(), account_id),
        )
    from . import storage

    if storage.enabled():
        try:
            storage.delete_prefix(f"backups/{account_id}/")
        except Exception as e:  # the account is gone either way; the operator sees this in the logs
            log.error("could not delete S3 backups of %s: %s", account_id, e)
    folder = people.account_root(account_id)
    if not folder.exists():
        return
    if settings.DELETED_RETENTION_DAYS > 0:
        target = settings.DATA_ROOT / "deleted" / f"{account_id}-{int(time.time())}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(target))
    else:
        shutil.rmtree(folder, ignore_errors=True)


def purge_deleted() -> int:
    """Wipe data of deleted accounts kept longer than DELETED_RETENTION_DAYS. Returns how many were wiped."""
    folder = settings.DATA_ROOT / "deleted"
    if not folder.exists():
        return 0
    cutoff = time.time() - settings.DELETED_RETENTION_DAYS * 86400
    wiped = 0
    for path in folder.iterdir():
        stamp = path.name.rsplit("-", 1)[-1]
        if stamp.isdigit() and int(stamp) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            wiped += 1
    return wiped


# --- sessions ------------------------------------------------------------------------------------------------------


def start_session(account_id: str, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with central.connect() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        c.execute(
            "INSERT INTO sessions (token_hash, account_id, created, expires, user_agent) VALUES (?, ?, ?, ?, ?)",
            (_sha(token), account_id, now, now + SESSION_DAYS * 86400, user_agent[:200]),
        )
    return token


def session_account(token: str | None) -> dict | None:
    if not token:
        return None
    with central.connect() as c:
        row = c.one("SELECT account_id FROM sessions WHERE token_hash = ? AND expires > ?", (_sha(token), time.time()))
    return get(row["account_id"]) if row else None


def end_session(token: str | None) -> None:
    if token:
        with central.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


def end_sessions(account_id: str) -> None:
    with central.connect() as c:
        c.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))


# --- email tokens --------------------------------------------------------------------------------------------------


def make_token(account_id: str, purpose: str) -> str:
    token = secrets.token_urlsafe(32)
    with central.connect() as c:
        c.execute("DELETE FROM email_tokens WHERE account_id = ? AND purpose = ?", (account_id, purpose))
        c.execute(
            "INSERT INTO email_tokens (token_hash, account_id, purpose, expires) VALUES (?, ?, ?, ?)",
            (_sha(token), account_id, purpose, time.time() + TOKEN_HOURS[purpose] * 3600),
        )
    return token


def use_token(token: str, purpose: str) -> dict:
    """The account the token belongs to; the token can't be used again."""
    with central.connect() as c:
        row = c.one(
            "SELECT account_id FROM email_tokens WHERE token_hash = ? AND purpose = ? AND used = 0 AND expires > ?",
            (_sha(token or ""), purpose, time.time()),
        )
        if row:
            c.execute("UPDATE email_tokens SET used = ? WHERE token_hash = ?", (time.time(), _sha(token)))
    account = get(row["account_id"]) if row else None
    if not account:
        raise AccountError("This link has expired or was already used. Ask for a new one.")
    return account
