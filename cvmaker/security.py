"""Request-level protection, applied to every request before the app sees it:

- Host check: the app only answers to its own host names (desktop: localhost / 127.0.0.1), which stops DNS-rebinding
  attacks from web pages against the local app.
- CSRF: a request that changes something (POST, PUT, PATCH, DELETE) must come from the app's own pages - its Origin
  (or, without one, its Referer) must be the app's. Login and session cookies are also SameSite=Lax.
- Login (server mode): every page except the public ones needs a logged-in, verified account; the account comes from
  the session cookie only. Then the person inside the account is picked from the person cookie (people.use only
  accepts people of that account).
- Security headers on every response.
"""

from urllib.parse import quote, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import accounts, people, settings

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Reachable without logging in (server mode).
PUBLIC_PREFIXES = ("/login", "/signup", "/logout", "/forgot", "/reset", "/verify", "/magic", "/auth/google", "/legal/",
                   "/billing/webhook", "/healthz", "/favicon", "/sw.js", "/static/app/")
# Logged in but not yet verified: only these.
UNVERIFIED_OK = ("/verify", "/logout", "/legal/", "/settings/account/delete")
# Requests that prove themselves another way (Stripe signs its webhook).
CSRF_EXEMPT = ("/billing/webhook",)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def allowed_hosts() -> set[str]:
    hosts = set(settings.ALLOWED_HOSTS)
    if settings.DESKTOP:
        hosts |= LOCAL_HOSTS
    else:
        hosts.add((urlsplit(settings.PUBLIC_URL).hostname or "").lower())
    return hosts


def _host_ok(request: Request) -> bool:
    host = (request.headers.get("host") or "").lower()
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name in allowed_hosts() or "*" in settings.ALLOWED_HOSTS


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        # No Origin and no Referer: not sent by a browser page. Fine on one's own machine (scripts, curl), refused on
        # a server, where every legitimate change comes from the app's own pages.
        return settings.DESKTOP and not request.headers.get("origin")
    parts = urlsplit(source)
    return f"{parts.hostname}:{parts.port or ''}".lower() == f"{request.url.hostname}:{request.url.port or ''}".lower() and (
        parts.hostname or ""
    ).lower() in allowed_hosts() | {(request.url.hostname or "").lower()}


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(("/api/", "/editor/api/")) or "application/json" in request.headers.get("accept", "")


def _login_redirect(request: Request) -> Response:
    if _wants_json(request):
        return JSONResponse({"error": "Log in again"}, status_code=401)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)


def secure_headers(response: Response) -> Response:
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    if settings.SECURE_COOKIES:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


async def guard(request: Request, call_next):
    path = request.url.path
    if not _host_ok(request):
        return PlainTextResponse("Unknown host", status_code=421)
    if request.method in UNSAFE and not path.startswith(CSRF_EXEMPT) and not _same_origin(request):
        return PlainTextResponse("This request didn't come from CVMaker's own pages, so it was refused.", status_code=403)

    account_id = people.LOCAL if settings.DESKTOP else None
    account = None
    if settings.SERVER:
        account = await run_in_threadpool(accounts.session_account, request.cookies.get(accounts.SESSION_COOKIE))
        public = path.startswith(PUBLIC_PREFIXES)
        if account:
            account_id = account["id"]
            if not account["verified"] and not path.startswith(UNVERIFIED_OK + ("/static",)) and not public:
                return RedirectResponse("/verify", status_code=303)
        elif not public:
            return _login_redirect(request)
    request.state.account = account

    account_token = people.use_account(account_id)
    person_token = people.use(request.cookies.get(people.COOKIE, people.DEFAULT)) if account_id else None
    try:
        response = await call_next(request)
    finally:
        if person_token is not None:
            people.reset(person_token)
        people.reset_account(account_token)
    return secure_headers(response)


def set_session_cookie(response: Response, token: str) -> Response:
    response.set_cookie(accounts.SESSION_COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=settings.SECURE_COOKIES, path="/")
    return response


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
