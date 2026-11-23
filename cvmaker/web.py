"""What every page shares: the templates and their filters, the page() helper that fills in the shell (people,
notifications, the missing-key notice), redirects, and the small helpers the route modules use."""

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import assistant, db, people, keys, render, settings
from .llm import LLMError
from .models import (
    Job,
    Profile,
)
from .posting import posting_html
from .salary import format_monthly

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["md_bold"] = render.env.filters["md_bold"]
templates.env.filters["ago"] = lambda ts: _ago(ts)
templates.env.filters["monthly"] = format_monthly
templates.env.filters["posting"] = posting_html
# "[the bit you still have to add]" highlighted in stories and drafted answers.
templates.env.filters["ph"] = lambda text: Markup(
    re.sub(r"\[([^\]\n]{1,200})\]", r'<span class="ph">[\1]</span>', str(escape(text or "")))
)
templates.env.filters["hue"] = lambda text: int(hashlib.md5((text or "").lower().encode()).hexdigest()[:4], 16) % 360
templates.env.filters["initials"] = lambda text: "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", text or "?")[:2]).upper() or "?"
templates.env.filters["day"] = lambda ts: datetime.fromtimestamp(ts).strftime("%d %b %Y") if ts else ""


# --- the app's own CSS and JS (cvmaker/static/app) -----------------------------------------------------------------
STATIC = Path(__file__).parent / "static" / "app"
STATIC_TYPES = {".css": "text/css", ".js": "text/javascript"}
_static_versions: dict[str, tuple[float, str]] = {}


def static_version(name: str) -> str:
    """A short hash of the file's content (worked out again when the file changes, for development)."""
    path = STATIC / name
    mtime = path.stat().st_mtime
    cached = _static_versions.get(name)
    if not cached or cached[0] != mtime:
        cached = (mtime, hashlib.sha256(path.read_bytes()).hexdigest()[:12])
        _static_versions[name] = cached
    return cached[1]


def static(name: str) -> str:
    """The address of a file in static/app. It carries the content's hash, so browsers can keep the file for good
    and still get a new one the moment it changes."""
    return f"/static/app/{name}?v={static_version(name)}"


templates.env.globals["static"] = static
templates.env.globals["STAGES"] = db.STAGES
templates.env.globals["STAGE_LABELS"] = db.STAGE_LABELS
templates.env.globals["FOLLOW_UP_DAYS"] = settings.FOLLOW_UP_DAYS
templates.env.globals["SERVER"] = settings.SERVER


def _split(value: str) -> list[str]:
    return [x.strip() for x in value.replace("\n", ",").split(",") if x.strip()]


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    minutes = int((time.time() - ts) / 60)
    if minutes < 0:
        return f"in {_until(ts)}"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


def _until(ts: float) -> str:
    minutes = max(0, int((ts - time.time()) / 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        name,
        {
            "profile_exists": settings.PROFILE_PATH.exists(),
            "people": people.load(),
            "person": people.current_person(),
            "unseen_total": db.unseen_count(),
            "notes": db.notifications(20),
            "notes_unread": db.notifications_unread(),
            "now": time.time(),
            "today": datetime.now().strftime("%Y-%m-%d"),
            "needs_ai_key": not keys.has_ai_key(),
            "account_email": (getattr(request.state, "account", None) or {}).get("email", ""),
            **ctx,
        },
    )


def _not_found(what: str) -> str:
    others = len(people.load()) > 1
    return f"{what} not found" + (f" for {people.current_person().name} - it may belong to someone else; switch person in the top bar." if others else "")


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _job_label(verb: str, job: Job) -> str:
    return f"{verb} · {job.title} @ {job.company}"


def _need_profile() -> Profile:
    profile = assistant.load_profile()
    if not profile:
        raise LLMError("Set up your profile first (My profile → Import my CV).")
    return profile
