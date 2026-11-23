"""Accounts and settings pages.

Server mode: sign-up, login (password, emailed link or Google), email verification, password reset, logout, the
account page (plan and usage, change password, export everything, delete the account), Stripe billing and the legal
pages. Desktop mode: only Settings (your own API keys, usage, export).
Included at the end of app.py (it uses app.py's page helpers).
"""

import secrets
import time
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import accounts, autostart, backup, billing, central, keys, log, mailer, people, security, settings, telegram, usage
from .app import _back, page, templates

router = APIRouter()
GOOGLE_STATE_COOKIE = "cvmaker_google_state"


def _auth_page(request: Request, template: str, **ctx) -> HTMLResponse:
    """A page for people who are not logged in (it can't use the app's page helper, which reads account data)."""
    return templates.TemplateResponse(
        request, template, {"google": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET), "SERVER": settings.SERVER,
                        "operator": settings.OPERATOR_NAME, **ctx},
    )


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


def _logged_in(request: Request, account: dict, target: str = "/") -> RedirectResponse:
    token = accounts.start_session(account["id"], request.headers.get("user-agent", ""))
    response = RedirectResponse(_safe_next(target), status_code=303)
    response.delete_cookie(people.COOKIE)
    return security.set_session_cookie(response, token)


def _limited(key: str, limit: int, seconds: float) -> bool:
    return not central.hit(key, limit, seconds)


def _send_verify(account: dict) -> None:
    token = accounts.make_token(account["id"], "verify")
    mailer.send(account["email"], "Confirm your email for CVMaker",
                f"Hi{(' ' + account['name']) if account.get('name') else ''},\n\nConfirm your email to start using CVMaker:\n"
                f"{mailer.link('/verify/' + token)}\n\nThe link works for 48 hours. If you didn't sign up, ignore this email.")


def _server_only():
    if not settings.SERVER:
        return RedirectResponse("/", status_code=303)
    return None


# --- sign up, log in, log out --------------------------------------------------------------------------------------


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, next: str = "/"):
    return _server_only() or _auth_page(request, "auth.html", mode="signup", next=_safe_next(next))


@router.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(""), agree: str = Form(""),
           next: str = Form("/")):
    if (r := _server_only()) is not None:
        return r
    if _limited(f"signup:{security.client_ip(request)}", settings.SIGNUPS_PER_IP_HOUR, 3600):
        return _auth_page(request, "auth.html", mode="signup", error="Too many sign-ups from here - try again in an hour.", email=email)
    if not agree:
        return _auth_page(request, "auth.html", mode="signup", error="Please accept the terms and the privacy policy.", email=email, name=name)
    try:
        account = accounts.create(email, password, name=name)
    except accounts.AccountError as e:
        return _auth_page(request, "auth.html", mode="signup", error=str(e), email=email, name=name)
    try:
        _send_verify(account)
    except mailer.MailError as e:
        log.error("verification email failed: %s", e)
    return _logged_in(request, account, "/verify")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", sent: int = 0, reset: int = 0):
    return _server_only() or _auth_page(request, "auth.html", mode="login", next=_safe_next(next), sent=sent, reset=reset)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if (r := _server_only()) is not None:
        return r
    ip = security.client_ip(request)
    if _limited(f"login-ip:{ip}", settings.LOGINS_PER_IP_MINUTE, 60) or _limited(f"login-email:{email.strip().lower()}", 20, 3600):
        return _auth_page(request, "auth.html", mode="login", error="Too many attempts - wait a minute and try again.", email=email, next=next)
    try:
        account = accounts.login(email, password)
    except accounts.AccountError as e:
        return _auth_page(request, "auth.html", mode="login", error=str(e), email=email, next=next)
    return _logged_in(request, account, next)


@router.post("/logout")
def logout(request: Request):
    accounts.end_session(request.cookies.get(accounts.SESSION_COOKIE))
    response = RedirectResponse("/login" if settings.SERVER else "/", status_code=303)
    response.delete_cookie(accounts.SESSION_COOKIE)
    response.delete_cookie(people.COOKIE)
    return response


# --- email verification --------------------------------------------------------------------------------------------


@router.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request, resent: int = 0):
    if (r := _server_only()) is not None:
        return r
    account = request.state.account
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["verified"]:
        return RedirectResponse("/", status_code=303)
    return _auth_page(request, "auth.html", mode="verify", email=account["email"], resent=resent)


@router.post("/verify/resend")
def verify_resend(request: Request):
    account = request.state.account
    if not account or account["verified"]:
        return RedirectResponse("/", status_code=303)
    if not _limited(f"verify:{account['id']}", 3, 3600):
        try:
            _send_verify(account)
        except mailer.MailError as e:
            return _auth_page(request, "auth.html", mode="verify", email=account["email"], error=str(e))
    return RedirectResponse("/verify?resent=1", status_code=303)


@router.get("/verify/{token}")
def verify_token(request: Request, token: str):
    try:
        account = accounts.use_token(token, "verify")
    except accounts.AccountError as e:
        return _auth_page(request, "auth.html", mode="message", title="Link expired", error=str(e))
    accounts.mark_verified(account["id"])
    return _logged_in(request, account, "/profile")


# --- forgotten password and login links ----------------------------------------------------------------------------


@router.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request, magic: int = 0):
    return _server_only() or _auth_page(request, "auth.html", mode="forgot", magic=magic)


@router.post("/forgot")
def forgot(request: Request, email: str = Form(...), magic: int = Form(0)):
    """Email a reset link (or a one-time login link). The answer is the same whether or not the email is known."""
    if (r := _server_only()) is not None:
        return r
    address = email.strip().lower()
    if not _limited(f"forgot:{address}", 3, 3600) and not _limited(f"forgot-ip:{security.client_ip(request)}", 10, 3600):
        account = accounts.by_email(address)
        if account:
            try:
                if magic:
                    token = accounts.make_token(account["id"], "magic")
                    mailer.send(address, "Your CVMaker login link",
                                f"Log in to CVMaker with this link:\n{mailer.link('/magic/' + token)}\n\nIt works once, for 15 minutes. "
                                "If you didn't ask for it, ignore this email.")
                else:
                    token = accounts.make_token(account["id"], "reset")
                    mailer.send(address, "Reset your CVMaker password",
                                f"Choose a new password here:\n{mailer.link('/reset/' + token)}\n\nThe link works for 2 hours. "
                                "If you didn't ask for it, ignore this email - your password stays the same.")
            except mailer.MailError as e:
                log.error("login/reset email failed: %s", e)
    return RedirectResponse("/login?sent=1", status_code=303)


@router.get("/magic/{token}")
def magic(request: Request, token: str):
    try:
        account = accounts.use_token(token, "magic")
    except accounts.AccountError as e:
        return _auth_page(request, "auth.html", mode="message", title="Link expired", error=str(e))
    accounts.mark_verified(account["id"])  # the link reached their inbox
    return _logged_in(request, account, "/")


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(request: Request, token: str):
    return _server_only() or _auth_page(request, "auth.html", mode="reset", token=token)


@router.post("/reset/{token}")
def reset(request: Request, token: str, password: str = Form(...)):
    try:
        accounts.check_new_password(password)
        account = accounts.use_token(token, "reset")
        accounts.set_password(account["id"], password)
        accounts.mark_verified(account["id"])
    except accounts.AccountError as e:
        return _auth_page(request, "auth.html", mode="reset", token=token, error=str(e))
    return _logged_in(request, account, "/")


# --- Google sign-in --------------------------------------------------------------------------------------------------


@router.get("/auth/google")
def google_start():
    if not (settings.SERVER and settings.GOOGLE_CLIENT_ID):
        return RedirectResponse("/login", status_code=303)
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": f"{settings.PUBLIC_URL}/auth/google/callback",
        "response_type": "code", "scope": "openid email profile", "state": state, "prompt": "select_account",
    })
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}", status_code=303)
    response.set_cookie(GOOGLE_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=settings.SECURE_COOKIES)
    return response


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    expected = request.cookies.get(GOOGLE_STATE_COOKIE)
    if not code or not expected or not secrets.compare_digest(state, expected):
        return _auth_page(request, "auth.html", mode="message", title="Google sign-in failed", error="Try again.")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{settings.PUBLIC_URL}/auth/google/callback", "grant_type": "authorization_code",
        })
        id_token = r.json().get("id_token") if r.status_code == 200 else None
        info = (await c.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})).json() if id_token else {}
    if info.get("aud") != settings.GOOGLE_CLIENT_ID or info.get("email_verified") not in ("true", True) or not info.get("email"):
        return _auth_page(request, "auth.html", mode="message", title="Google sign-in failed", error="Google didn't confirm the email address.")
    account = await run_in_threadpool(accounts.google_account, info["sub"], info["email"], info.get("name", ""))
    response = _logged_in(request, account, "/")
    response.delete_cookie(GOOGLE_STATE_COOKIE)
    return response


# --- settings: API keys, plan and usage, account ---------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, error: str = "", billing: str = ""):
    account = request.state.account
    rows = [
        {"name": name, "label": label, "help": help_, "own": keys.own(name), "shown": keys.own(name) if name in keys.PLAIN else keys.masked(keys.own(name)),
         "server_default": bool(getattr(settings, env.upper(), "")) and settings.SERVER}
        for name, (label, env, help_) in keys.KEYS.items() if name != keys.TELEGRAM
    ]
    return page(
        request, "settings.html", rows=rows, saved=saved, error=error, billing_state=billing, account=account,
        use=usage.this_month(), billing_on=settings.BILLING, SERVER=settings.SERVER, backend=settings.BACKEND,
        models=settings.MODELS, plan_tokens=settings.PLAN_TOKENS, telegram=telegram.status() if settings.DESKTOP else None,
        autostart={"on": autostart.enabled()} if settings.DESKTOP and autostart.available() else None,
    )


@router.post("/settings/keys")
async def settings_keys(request: Request):
    form = await request.form()
    values = {}
    for name in keys.KEYS:
        if form.get(f"clear_{name}"):
            values[name] = ""
        elif (value := str(form.get(name, "")).strip()):
            values[name] = value
    if (ant := values.get("anthropic")) and not ant.startswith("sk-ant-"):
        return _back("/settings?error=" + quote("That doesn't look like an Anthropic API key (they start with sk-ant-).") + "#keys")
    keys.set_many(values)
    return _back("/settings?saved=1#keys")


@router.post("/settings/keys/test")
def settings_keys_test():
    """Check the Anthropic key with a tiny request."""
    key = keys.get("anthropic")
    if not key:
        return JSONResponse({"ok": False, "message": "No key saved yet."})
    try:
        r = httpx.get("https://api.anthropic.com/v1/models?limit=1", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=15)
    except httpx.HTTPError as e:
        return JSONResponse({"ok": False, "message": f"Couldn't reach Anthropic: {type(e).__name__}"})
    if r.status_code == 200:
        return JSONResponse({"ok": True, "message": "The key works."})
    return JSONResponse({"ok": False, "message": "Anthropic refused the key." if r.status_code in (401, 403) else f"Anthropic answered {r.status_code}."})


@router.post("/settings/password")
def settings_password(request: Request, current: str = Form(""), password: str = Form(...)):
    account = request.state.account
    if not account:
        return _back("/settings")
    if account["password_hash"] and not accounts.check_password(current, account["password_hash"]):
        return _back("/settings?error=" + quote("Your current password is wrong.") + "#account")
    try:
        accounts.set_password(account["id"], password)
    except accounts.AccountError as e:
        return _back("/settings?error=" + quote(str(e)) + "#account")
    return _logged_in(request, accounts.get(account["id"]), "/settings?saved=1#account")


@router.post("/settings/export")
def settings_export():
    path = backup.export_account()
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/settings/account/delete")
def settings_delete(request: Request, confirm: str = Form("")):
    account = request.state.account
    if not account:
        return _back("/settings")
    if confirm.strip().lower() != (account["email"] or "").lower():
        return _back("/settings?error=" + quote("Type your email address to confirm.") + "#account")
    if account.get("stripe_subscription") and settings.BILLING and accounts.plan_of(account["id"]) != "free":
        return _back("/settings?error=" + quote("Cancel your subscription first (Plan → Manage subscription).") + "#account")
    accounts.delete(account["id"])
    response = _auth_page(request, "auth.html", mode="message", title="Your account is deleted",
                          info="Your account and everything in it have been deleted. Thanks for using CVMaker.")
    response.delete_cookie(accounts.SESSION_COOKIE)
    response.delete_cookie(people.COOKIE)
    return response


# --- billing -----------------------------------------------------------------------------------------------------


@router.post("/billing/checkout")
def billing_checkout(request: Request):
    if not settings.BILLING or not request.state.account:
        return _back("/settings")
    try:
        return RedirectResponse(billing.checkout_url(request.state.account), status_code=303)
    except (billing.BillingError, httpx.HTTPError) as e:
        return _back("/settings?error=" + quote(str(e)) + "#plan")


@router.post("/billing/portal")
def billing_portal(request: Request):
    if not settings.BILLING or not request.state.account:
        return _back("/settings")
    try:
        return RedirectResponse(billing.portal_url(request.state.account), status_code=303)
    except (billing.BillingError, httpx.HTTPError) as e:
        return _back("/settings?error=" + quote(str(e)) + "#plan")


@router.post("/billing/webhook")
async def billing_webhook(request: Request):
    if not settings.BILLING:
        return PlainTextResponse("billing off", status_code=404)
    payload = await request.body()
    try:
        event = billing.verify(payload, request.headers.get("stripe-signature", ""))
    except (billing.BillingError, ValueError) as e:
        return PlainTextResponse(str(e), status_code=400)
    result = await run_in_threadpool(billing.handle, event)
    log.info("billing: %s", result)
    return PlainTextResponse("ok")


# --- legal pages and health ------------------------------------------------------------------------------------------


@router.get("/legal/{name}", response_class=HTMLResponse)
def legal(request: Request, name: str):
    if name not in {"privacy", "terms", "cookies"}:
        return PlainTextResponse("Not found", status_code=404)
    return _auth_page(request, f"legal_{name}.html", mode="legal", settings=settings, updated=time.strftime("%d %B %Y"))


@router.get("/healthz")
def healthz():
    with central.connect() as c:
        c.one("SELECT 1 AS ok")
    return {"ok": True, "mode": settings.MODE}
