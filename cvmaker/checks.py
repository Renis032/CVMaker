"""Checks on a posting before you spend time on it.

- Still open? Asks the job board's API where the source has one (Greenhouse, Lever, Ashby, SmartRecruiters,
  Workable), otherwise loads the posting and looks for a 404/410, an "expired" redirect or "no longer accepting
  applications" wording. LinkedIn is never loaded (it is used only through JSearch), so those stay "unknown".
- Repost? Compares with the other jobs saved for the same company and title, and with the posting's own dates.
- Work permit and scam signs: wording in the posting, matched without Claude.
- Claude's review (optional, one call) weighs all of it and adds anything the patterns miss.

Jobs found closed are left out of the jobs list (a "Closed" tab shows them) but stay in the pipeline once applied.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from . import coach, db, people
from .models import Job, PostingReview, Profile
from .web_fetch import BlockedURL, html_to_text, http

CLOSED_WORDING = [
    "no longer accepting applications", "not accepting applications", "this job is no longer available",
    "job is no longer available", "position is no longer available", "position has been filled",
    "this position is closed", "this position has been closed", "this job has expired", "job has expired",
    "posting has expired", "listing has expired", "job posting has been removed", "vacancy is closed",
    "vacancy has been closed", "the job you are looking for is no longer", "job offer has expired",
    "обявата е изтекла", "обявата не е активна", "позицията е затворена", "обявата е деактивирана",
    "diese stelle ist nicht mehr verfügbar", "stelle ist bereits besetzt", "stellenanzeige ist nicht mehr",
]
NEVER_LOAD = ("linkedin.com", "jobs.bg")  # LinkedIn only through JSearch; jobs.bg refuses bots

# (pattern, what it means). Matched case-insensitively against the posting text.
PERMIT_PATTERNS = [
    (r"(?:must|need to|needs to|required to|should)\s+(?:already\s+)?(?:be\s+)?(?:legally\s+)?(?:authori[sz]ed|eligible|permitted)\s+to\s+work\s+in\s+[^.;\n]{2,40}", "Work authorisation required"),
    (r"\b(?:no|not|unable to|cannot|can't|don't|do not|will not|won't)\s+(?:currently\s+)?(?:offer|provide|support)?\s*(?:visa\s+)?sponsor(?:ship)?", "No visa sponsorship"),
    (r"\bvisa sponsorship (?:is )?not (?:available|provided|offered)", "No visa sponsorship"),
    (r"\b(?:US|U\.S\.|UK|EU|Canadian|Australian|German|American|British)\s+citizens?(?:hip)?\s+(?:only|is required|required)", "Citizenship required"),
    (r"\bsecurity clearance\b", "Security clearance"),
    (r"\bright to work in (?:the\s+)?[A-Z][^.;\n]{1,30}", "Right to work required"),
    (r"\b(?:must|need to|required to)\s+(?:be\s+)?(?:based|located|residing|living|reside)\s+in\s+[^.;\n]{2,40}", "Must live in a region"),
    (r"\b(?:US|USA|U\.S\.|North America|LATAM|Americas|UK|Canada)[- ]only\b", "Region only"),
    (r"\b(?:open|available) (?:only )?to (?:candidates|applicants) (?:based |located )?in [^.;\n]{2,40}", "Must live in a region"),
]
SCAM_PATTERNS = [
    (r"\b(?:telegram|whatsapp|signal)\b[^.\n]{0,40}\b(?:interview|chat|contact)", "Interview over a chat app"),
    (r"\b(?:registration|training|starter|application|processing)\s+fee\b", "Asks for a fee"),
    (r"\b(?:bank (?:account|details)|credit card|social security number|SSN)\b", "Asks for bank or ID details"),
    (r"\b(?:buy|purchase) (?:your own )?(?:equipment|laptop|starter kit)\b[^.\n]{0,40}\breimburs", "Pay first, reimbursed later"),
    (r"\bearn (?:up to )?\$?\d[\d,]*\s*(?:per|a|/)\s*(?:day|week)\b", "Pay per day or week promise"),
]
EU = {"austria", "belgium", "bulgaria", "croatia", "cyprus", "czech", "denmark", "estonia", "finland", "france",
      "germany", "greece", "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg", "malta",
      "netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden"}


# --- still open? ------------------------------------------------------------------------------------------


def _api_state(job: Job, c: httpx.Client) -> tuple[str, str] | None:
    """Ask the source's own API. None when the source has none we can use."""
    parts = job.id.split(":", 2)
    if len(parts) != 3:
        return None
    source, slug, ident = parts
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{ident}",
        "lever": f"https://api.lever.co/v0/postings/{slug}/{ident}",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{ident}",
        "workable": f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{ident}",
    }
    if source == "ashby":
        r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200:
            return None
        listed = any(j.get("id") == ident for j in r.json().get("jobs", []))
        return ("open", "listed on the company's job board") if listed else ("closed", "no longer on the company's job board")
    if source not in urls:
        return None
    r = c.get(urls[source])
    if r.status_code in (404, 410):
        return "closed", f"removed from {source.capitalize()} (HTTP {r.status_code})"
    if r.status_code == 200:
        if source == "smartrecruiters" and r.json().get("active") is False:
            return "closed", "marked inactive on SmartRecruiters"
        return "open", f"still listed on {source.capitalize()}"
    return None


def check_live(job: Job) -> tuple[str, str]:
    """('open' | 'closed' | 'unknown', why)."""
    if not job.url and ":" not in job.id:
        return "unknown", "no link to check"
    if any(host in job.url for host in NEVER_LOAD) and not job.id.startswith(("greenhouse:", "lever:", "ashby:")):
        return "unknown", "this site is not loaded by the app - open the posting to check"
    try:
        with http() as c:
            state = _api_state(job, c)
            if state:
                return state
            if not job.url:
                return "unknown", "no link to check"
            r = c.get(job.url)
    except httpx.HTTPError as e:
        return "unknown", f"could not load it ({type(e).__name__})"
    except BlockedURL as e:
        return "unknown", str(e)
    if r.status_code in (404, 410):
        return "closed", f"the posting page is gone (HTTP {r.status_code})"
    if r.status_code >= 400:
        return "unknown", f"the site answered HTTP {r.status_code}"
    final = str(r.url).lower()
    if any(s in final for s in ("error=true", "/expired", "job-expired", "jobnotfound", "job_not_found")):
        return "closed", "the link now redirects to an error or expired page"
    text = html_to_text(r.text).lower()
    hit = next((w for w in CLOSED_WORDING if w in text), None)
    if hit:
        return "closed", f"the page says “{hit}”"
    if len(text) < 400:
        return "unknown", "the page is built by JavaScript - open it to check"
    return "open", "the posting page loads normally"


def check_and_record(job_id: str) -> tuple[str, str]:
    found = db.get_job(job_id)
    if not found:
        return "unknown", "job not found"
    state, why = check_live(found[0])
    if state == "closed":
        db.set_open_state(job_id, True, why)
    elif state == "open":
        db.set_open_state(job_id, False)
    else:
        db.cache_put(f"live:{job_id}", {"state": state, "why": why})
        with db.connect() as c:
            c.execute("UPDATE jobs SET checked_at = ? WHERE id = ?", (time.time(), job_id))
        return state, why
    db.cache_put(f"live:{job_id}", {"state": state, "why": why})
    return state, why


def last_check(job_id: str) -> dict | None:
    return db.cache_get(f"live:{job_id}", 24 * 365)


# Stages worth checking automatically: what you are still deciding on. Applied jobs are left alone.
CHECK_STAGES = ["new", "liked"]


def check_many(job_ids: list[str], progress=None) -> dict:
    """Check jobs in parallel; returns counts per state."""
    counts = {"open": 0, "closed": 0, "unknown": 0}
    if progress:
        progress.set(step=f"checking {len(job_ids)} postings", done=0, total=len(job_ids))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [people.submit(pool, check_and_record, i) for i in job_ids]
        for n, f in enumerate(futures, 1):
            try:
                counts[f.result()[0]] += 1
            except Exception:
                counts["unknown"] += 1
            if progress:
                progress.set(done=n, total=len(job_ids), step=f"{n} of {len(job_ids)} checked · {counts['closed']} closed")
    return counts


# --- reposts and wording -------------------------------------------------------------------------------------


def _parse_date(value: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_dot, right_nl = text.find(".", end), text.find("\n", end)
    right = min(x for x in (right_dot, right_nl, len(text)) if x != -1)
    return " ".join(text[left:right + 1].split())[:240]


def _home(profile: Profile | None) -> str:
    return (profile.contact.location if profile else "").lower()


def _covers_candidate(snippet: str, home: str) -> bool:
    s = snippet.lower()
    words = [w.strip() for w in re.split(r"[,/()]", home) if w.strip()]
    if any(w and w in s for w in words):
        return True
    in_eu = any(c in home for c in EU)
    return in_eu and bool(re.search(r"\b(?:eu|european union|europe|eea|emea)\b", s))


def local_flags(job: Job, profile: Profile | None, app: dict | None = None) -> list[dict]:
    """Findings that need no network and no Claude: reposts, refreshed dates, permit and scam wording."""
    flags: list[dict] = []
    text = f"{job.title}\n{job.location}\n{job.description}"
    home = _home(profile)

    seen_permit = set()
    for pattern, label in PERMIT_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            snippet = _sentence(text, m.start(), m.end())
            if snippet in seen_permit:
                continue
            seen_permit.add(snippet)
            ok = bool(home) and _covers_candidate(snippet, home)
            flags.append({
                "kind": "work permit", "severity": "info" if ok else "amber", "finding": label,
                "evidence": snippet,
                "what_to_do": "This seems to include where you live." if ok else
                ("Check that you are allowed to work there before applying." if home else "Add your location to your profile to compare."),
            })
    for pattern, label in SCAM_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            flags.append({"kind": "legitimacy", "severity": "red", "finding": label, "evidence": _sentence(text, m.start(), m.end()),
                          "what_to_do": "Real employers don't ask for money, bank details or chat-app interviews. Verify the company independently."})

    others = db.similar_jobs(job.id, job.company, db.title_key_of(job.title))
    if others:
        first = others[0]
        closed_before = [o for o in others if o["closed_at"]]
        detail = f"{len(others)} other listing{'s' if len(others) != 1 else ''} with this title at {job.company}, first seen {datetime.fromtimestamp(first['first_seen']):%d %b %Y}"
        flags.append({
            "kind": "repost", "severity": "amber" if closed_before else "info",
            "finding": "Reposted after closing" if closed_before else "Listed more than once",
            "evidence": detail + (" (an earlier one closed)" if closed_before else ""),
            "what_to_do": "A role posted again and again can mean high turnover, a pipeline-building ad, or simply several openings. Ask how many people they are hiring and why the role is open.",
            "links": [o["id"] for o in others[:5]],
        })

    if app:
        posted = _parse_date(job.posted_at)
        first_seen = app.get("first_seen") or 0
        if posted and first_seen and posted.timestamp() - first_seen > 7 * 86400:
            flags.append({
                "kind": "repost", "severity": "info", "finding": "Posting date refreshed",
                "evidence": f"The app first saw it on {datetime.fromtimestamp(first_seen):%d %b %Y}, but it now says posted {posted:%d %b %Y}.",
                "what_to_do": "Employers bump old postings to keep them on top. It is still worth applying; just don't expect it to be brand new.",
            })
        age_days = (time.time() - (posted.timestamp() if posted else first_seen)) / 86400 if (posted or first_seen) else 0
        if age_days > 60:
            flags.append({
                "kind": "repost", "severity": "info", "finding": f"Open for about {int(age_days)} days",
                "evidence": f"Posted {posted:%d %b %Y}" if posted else f"First seen {datetime.fromtimestamp(first_seen):%d %b %Y}",
                "what_to_do": "Long-open roles are often hard to fill or 'always hiring'. Ask whether it is a real current opening.",
            })
    order = {"red": 0, "amber": 1, "info": 2}
    return sorted(flags, key=lambda f: order[f["severity"]])


def facts_for_claude(job: Job, flags: list[dict], live: dict | None, app: dict) -> list[str]:
    facts = [f"{f['finding']}: {f['evidence']}" for f in flags]
    if live:
        facts.append(f"Liveness check: {live['state']} - {live['why']}")
    if app.get("closed_at"):
        facts.append(f"Marked closed on {datetime.fromtimestamp(app['closed_at']):%d %b %Y}: {app.get('closed_reason')}")
    if app.get("first_seen"):
        facts.append(f"First found by the app on {datetime.fromtimestamp(app['first_seen']):%d %b %Y}, last seen in a search on {datetime.fromtimestamp(app['last_seen']):%d %b %Y}")
    return facts


def review(job: Job, profile: Profile, progress=None) -> PostingReview:
    app = db.get_application(job.id)
    if progress:
        progress.set(step="checking the posting is still open")
    state, why = check_and_record(job.id)
    flags = local_flags(job, profile, app)
    if progress:
        progress.set(step="Claude is reviewing the posting")
    made = coach.review_posting(profile, job, facts_for_claude(job, flags, {"state": state, "why": why}, db.get_application(job.id)))
    db.cache_put(f"postcheck:{job.id}", made.model_dump())
    return made


def cached_review(job_id: str) -> PostingReview | None:
    cached = db.cache_get(f"postcheck:{job_id}", 24 * 365)
    try:
        return PostingReview.model_validate(cached) if cached else None
    except ValueError:
        return None
