"""Configuration, from the environment (and .env).

CVMaker runs in one of two modes:
- desktop (default): one person's computer. No login; the app only answers on 127.0.0.1; the user brings their own
  Anthropic API key (entered in Settings, stored on their machine).
- server: a hosted, multi-user site. People sign up and log in; each account's data is kept apart under
  data/accounts/<id>/; usage of the operator's Anthropic key is metered and limited per plan.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

from . import log

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    return default if not value else value.lower() in {"1", "true", "yes", "on"}


_level = _env("CVMAKER_LOG_LEVEL", "INFO").upper()
log.setLevel(_level if _level in {"DEBUG", "INFO", "WARNING", "ERROR"} else "INFO")


MODE = _env("CVMAKER_MODE", "desktop").lower()
if MODE not in {"desktop", "server"}:
    raise RuntimeError(f"CVMAKER_MODE must be 'desktop' or 'server', not {MODE!r}")
SERVER = MODE == "server"
DESKTOP = not SERVER

# Where all data lives (desktop: data/ next to the code unless CVMAKER_DATA points elsewhere).
DATA_ROOT = Path(_env("CVMAKER_DATA") or ROOT / "data").expanduser().resolve()
COMPANIES_FILE = ROOT / "companies.yaml"

# The central database: accounts, sessions, usage and the task queue. SQLite by default; a postgresql:// URL in
# server mode (needs the "server" extra: psycopg).
DATABASE_URL = _env("DATABASE_URL") or f"sqlite:///{DATA_ROOT / 'central.db'}"

# The public address of the site, for links in emails (server mode).
PUBLIC_URL = _env("CVMAKER_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
# Extra host names the app answers to (desktop: only localhost / 127.0.0.1; server: the PUBLIC_URL host + these).
ALLOWED_HOSTS = [h.strip().lower() for h in _env("CVMAKER_ALLOWED_HOSTS").split(",") if h.strip()]
# Behind a TLS-terminating proxy: mark cookies Secure.
SECURE_COOKIES = _flag("CVMAKER_SECURE_COOKIES", PUBLIC_URL.startswith("https://"))


def _secret_key() -> str:
    """Signs sessions and encrypts stored API keys. From CVMAKER_SECRET_KEY, else generated once and kept in the data
    folder (readable only by this user)."""
    if value := _env("CVMAKER_SECRET_KEY"):
        return value
    path = DATA_ROOT / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    if SERVER:
        raise RuntimeError("Set CVMAKER_SECRET_KEY in server mode (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    path.chmod(0o600)
    return value


SECRET_KEY = _secret_key()

# --- Claude ------------------------------------------------------------------------------------------------------
# "api" (the Anthropic API with a key) is the only backend in server mode. In desktop mode a developer can still set
# CVMAKER_BACKEND=claude-code to run calls through a local Claude Code login (personal use only, not for resale).
BACKEND = _env("CVMAKER_BACKEND", "api")
if SERVER and BACKEND != "api":
    raise RuntimeError("Server mode only supports CVMAKER_BACKEND=api")
CLAUDE_BIN = _env("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
# The operator's key (server mode, or a desktop default); desktop users normally enter their own in Settings.
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
# Model per kind of work: "fast" reads and extracts, "standard" writes and reviews, "best" tailors CVs and letters.
MODELS = {
    "fast": _env("CVMAKER_MODEL_FAST", "claude-haiku-5-5"),
    "standard": _env("CVMAKER_MODEL_STANDARD", "claude-sonnet-5-5"),
    "best": _env("CVMAKER_MODEL") or _env("CVMAKER_MODEL_BEST", "claude-opus-5-5"),
}
MODEL = MODELS["best"]

# --- usage limits (server mode, when the operator's key is used) -------------------------------------------------
# Monthly token allowance per plan (input + output). A user who enters their own Anthropic key is not limited.
PLAN_TOKENS = {
    "free": int(_env("CVMAKER_FREE_TOKENS", "300000")),
    "pro": int(_env("CVMAKER_PRO_TOKENS", "6000000")),
}
# Claude calls per user per minute (all plans).
AI_CALLS_PER_MINUTE = int(_env("CVMAKER_AI_CALLS_PER_MINUTE", "12"))
# Abuse limits for sign-up and login, per IP address.
SIGNUPS_PER_IP_HOUR = int(_env("CVMAKER_SIGNUPS_PER_IP_HOUR", "5"))
LOGINS_PER_IP_MINUTE = int(_env("CVMAKER_LOGINS_PER_IP_MINUTE", "10"))
# Saved searches that run on a schedule, per plan.
PLAN_SCHEDULED_SEARCHES = {"free": int(_env("CVMAKER_FREE_SCHEDULED", "1")), "pro": int(_env("CVMAKER_PRO_SCHEDULED", "10"))}

# --- job sources and GitHub: server-wide defaults (each user can set their own in Settings) ----------------------
RAPIDAPI_KEY = _env("RAPIDAPI_KEY")
ADZUNA_APP_ID = _env("ADZUNA_APP_ID")
ADZUNA_APP_KEY = _env("ADZUNA_APP_KEY")
GITHUB_USER = _env("GITHUB_USER")
GITHUB_TOKEN = _env("GITHUB_TOKEN")

# --- headless Chrome (PDFs and JavaScript-built career pages) ---------------------------------------------------
# Empty: the first Chromium-based browser found (Chrome, Chromium, Edge, Brave; see web_fetch.chrome_bin).
CHROME_BIN = _env("CHROME_BIN")
# Chrome's own sandbox stays on unless this is set. Only turn it off inside a container that is itself the sandbox
# (Docker without the needed kernel features); never on a normal server.
CHROME_NO_SANDBOX = _flag("CVMAKER_CHROME_NO_SANDBOX")
CAREER_PAGE_CACHE_HOURS = int(_env("CAREER_PAGE_CACHE_HOURS", "24"))

# --- background tasks ------------------------------------------------------------------------------------------
# Worker threads inside the web process. Set to 0 in server mode when separate `python -m cvmaker.worker` processes
# run the queue.
TASK_WORKERS = int(_env("CVMAKER_TASK_WORKERS", "4"))
# How many times a task interrupted by a restart is started again.
TASK_MAX_ATTEMPTS = int(_env("CVMAKER_TASK_ATTEMPTS", "3"))
# Days after applying until the app reminds you to follow up.
FOLLOW_UP_DAYS = float(_env("CVMAKER_FOLLOW_UP_DAYS", "10"))
# Fetch the ECB's daily exchange rates (once a day, when a search runs) for comparing salaries. Off: fixed rates.
LIVE_RATES = _flag("CVMAKER_LIVE_RATES", True)

# --- uploads ---------------------------------------------------------------------------------------------------
MAX_UPLOAD_MB = float(_env("CVMAKER_MAX_UPLOAD_MB", "15"))

# --- email (server mode: sign-up, password reset, reminders) ----------------------------------------------------
# console (prints / logs the email; for development), smtp, resend or postmark.
MAIL_BACKEND = _env("CVMAKER_MAIL", "console")
MAIL_FROM = _env("CVMAKER_MAIL_FROM", "CVMaker <no-reply@localhost>")
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
RESEND_API_KEY = _env("RESEND_API_KEY")
POSTMARK_TOKEN = _env("POSTMARK_TOKEN")

# --- Google sign-in (server mode; off unless both are set) ------------------------------------------------------
GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")

# --- payments (server mode; off unless STRIPE_SECRET_KEY is set) ------------------------------------------------
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_PRO = _env("STRIPE_PRICE_PRO")
BILLING = SERVER and bool(STRIPE_SECRET_KEY)

# --- backups to object storage (S3 or compatible; off unless S3_BUCKET is set) ----------------------------------
S3_BUCKET = _env("S3_BUCKET")
S3_PREFIX = _env("S3_PREFIX", "cvmaker")
S3_ENDPOINT = _env("S3_ENDPOINT")  # for S3-compatible stores (Hetzner, Scaleway, R2, MinIO)
S3_REGION = _env("S3_REGION", "eu-central-1")
# Hours between automatic backups of every account (0 turns them off).
BACKUP_EVERY_HOURS = float(_env("CVMAKER_BACKUP_EVERY_HOURS", "24" if SERVER else "0"))
# Days an account's data is kept after the account is deleted (then it is wiped).
DELETED_RETENTION_DAYS = int(_env("CVMAKER_DELETED_RETENTION_DAYS", "0"))

# --- legal pages (server mode) ----------------------------------------------------------------------------------
OPERATOR_NAME = _env("CVMAKER_OPERATOR", "the operator of this site")
OPERATOR_EMAIL = _env("CVMAKER_OPERATOR_EMAIL", "")
HOSTING_PROVIDER = _env("CVMAKER_HOSTING_PROVIDER", "")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) CVMaker/0.1 (job search)"

# A default for the Telegram bot (desktop mode); normally saved in Settings → Telegram instead.
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")

_PERSON_FILES = {
    "DB_PATH": "cvmaker.db",
    "PROFILE_PATH": "profile.json",
    "PHOTO_PATH": "photo.png",
    "OUTPUT_DIR": "cvs",
}


def __getattr__(name: str) -> Path:
    """DATA_DIR and the paths inside it belong to the current account and person (see people.py)."""
    from . import people

    if name == "DATA_DIR":
        return people.data_dir()
    if name in _PERSON_FILES:
        return people.data_dir() / _PERSON_FILES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
