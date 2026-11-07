"""API keys each account brings: its own Anthropic key, job-board keys and GitHub. Stored encrypted (with a key
derived from CVMAKER_SECRET_KEY) in the account folder, so a copied data folder alone doesn't reveal them.

When an account has no key of its own the server-wide default from the environment is used (in server mode the
operator's Anthropic key, metered per plan; see usage.py).
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from . import people, settings

# name -> (label, environment default, help)
KEYS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("Anthropic API key", "ANTHROPIC_API_KEY", "From console.anthropic.com → API keys. Starts with sk-ant-."),
    "rapidapi": ("JSearch key (RapidAPI)", "RAPIDAPI_KEY", "LinkedIn / Indeed / Glassdoor results via rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch."),
    "adzuna_id": ("Adzuna app ID", "ADZUNA_APP_ID", "developer.adzuna.com (free)."),
    "adzuna_key": ("Adzuna app key", "ADZUNA_APP_KEY", ""),
    "github_user": ("GitHub username", "GITHUB_USER", "Whose public repos the GitHub check reads."),
    "github_token": ("GitHub token", "GITHUB_TOKEN", "Optional: a read-only token raises GitHub's rate limit."),
}
# Shown in full (not secret).
PLAIN = {"github_user", "adzuna_id"}
# Kept with the keys, but set in its own card on the Settings page (the Telegram bot runs on the desktop only).
TELEGRAM = "telegram_token"
if settings.DESKTOP:
    KEYS[TELEGRAM] = ("Telegram bot token", "TELEGRAM_BOT_TOKEN", "")


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("cvmaker-keys:" + settings.SECRET_KEY).encode()).digest()))


def _path():
    return people.account_root() / "keys.json"


def _load() -> dict[str, str]:
    path = _path()
    if not path.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        return {}


def own(name: str) -> str:
    """The account's own value (empty when it has none)."""
    return _load().get(name, "")


def get(name: str) -> str:
    """The account's own value, else the server-wide default."""
    return own(name) or getattr(settings, KEYS[name][1].upper(), "")


def set_many(values: dict[str, str]) -> None:
    data = _load()
    for name, value in values.items():
        if name not in KEYS:
            continue
        value = (value or "").strip()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet().encrypt(json.dumps(data).encode()))
    path.chmod(0o600)


def masked(value: str) -> str:
    return "" if not value else (value[:7] + "…" + value[-4:] if len(value) > 14 else "••••")


def has_ai_key() -> bool:
    """Whether Claude calls can be made at all (own key, operator key, or the desktop Claude Code backend)."""
    return settings.BACKEND != "api" or bool(get("anthropic"))
