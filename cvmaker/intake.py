"""Add a job you found yourself: paste its link or its text, and the app saves it, then (optionally) checks your
fit, tailors a one-page CV, or makes the whole application pack - one step instead of several.

Links are loaded like any posting (headless Chrome when the page is built by JavaScript). LinkedIn and jobs.bg
pages are not loaded - paste the posting text for those."""

import hashlib
import re

from . import assistant, auto, checks, coach, db, pack
from .models import Job
from .salary import find_salary
from .search import score
from .web_fetch import fetch_html, html_to_text

URL_RE = re.compile(r"^\s*(https?://\S+)\s*$")
# What to do after saving: each adds to the one before.
STEPS = {
    "save": "Just save it",
    "fit": "Save and check my fit",
    "cv": "Save, check my fit and tailor a one-page CV",
    "pack": "Full application pack (fit, CV, cover letter, interview prep, project ideas)",
}


class IntakeError(RuntimeError):
    pass


def split_input(text: str) -> tuple[str, str]:
    """(url, pasted text). A lone link is a URL; text with a link in its first line keeps both."""
    m = URL_RE.match(text)
    if m:
        return m.group(1), ""
    first = text.strip().splitlines()[0] if text.strip() else ""
    link = re.search(r"https?://\S+", first)
    return (link.group(0) if link else ""), text.strip()


def _load(url: str) -> str:
    if any(host in url for host in checks.NEVER_LOAD):
        raise IntakeError("The app doesn't load LinkedIn or jobs.bg pages. Open the posting, copy its text and paste it here instead.")
    try:
        text = html_to_text(fetch_html(url))
    except Exception:
        text = ""
    if len(text) < 600:
        try:
            text = html_to_text(fetch_html(url, render=True))
        except Exception as e:
            raise IntakeError(f"Could not load the page ({e}). Paste the posting text instead.") from e
    return text


def add(raw: str, progress=None) -> str:
    """Read, save and like the posting. Returns its job id (an existing one if the link was saved before)."""
    url, pasted = split_input(raw)
    if not url and not pasted:
        raise IntakeError("Paste a link or the posting text.")
    if url and not pasted:
        existing = db.find_by_url(url)
        if existing:
            return existing
        if progress:
            progress.set(step="loading the page")
        text = _load(url)
    else:
        text = pasted
    if progress:
        progress.set(step="Claude is reading the posting")
    parsed = coach.parse_posting(text, url)
    if not parsed.is_job_posting or not parsed.title:
        raise IntakeError("That doesn't look like a single job posting. Paste the posting text itself.")
    url = url or parsed.apply_url
    if url and (existing := db.find_by_url(url)):
        return existing
    digest = hashlib.sha1((url or f"{parsed.company}|{parsed.title}|{parsed.description[:500]}").encode()).hexdigest()[:12]
    job = Job(
        id=f"added:{digest}", source="added by you", company=parsed.company or "Unknown company", title=parsed.title,
        location=parsed.location, remote=parsed.remote, url=url, description=parsed.description or text,
        posted_at=parsed.posted_at, salary=parsed.salary or find_salary(parsed.description),
    )
    criteria = auto.active_search().criteria
    db.upsert_jobs([job], {job.id: score(job, criteria, assistant.load_profile())})
    db.mark_seen([job.id])
    db.promote(job.id, "liked")
    return job.id


def add_and_prepare(raw: str, step: str, progress=None) -> str:
    """The whole one-step flow. Returns the job page URL to open."""
    job_id = add(raw, progress)
    profile = assistant.load_profile()
    if step == "save" or not profile:
        return f"/job/{job_id}"
    if step == "pack":
        return pack.make_pack(job_id, progress)
    job, _ = db.get_job(job_id)
    pack.fit_check(profile, job, progress)
    if step == "fit":
        return f"/job/{job_id}#fit"
    cv_id = pack.tailored_cv(profile, job, None, {"font_pt": None, "one_page": True, "instructions": ""}, progress)
    return f"/cv/{cv_id}"
