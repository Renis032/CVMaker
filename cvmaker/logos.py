"""Company logos for the job list: the company's own site icon, found once and kept on disk.

The domain comes from the job's link when that is the company's own site, else from your company list, else a
guess from the name (checked: an icon service has to know the domain). Misses are remembered too, so a company is
looked up at most once a month. Only the domain is sent out (to the company's site and DuckDuckGo's icon service).
"""

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import log, settings, web_fetch

# Job boards and hosting: their domain says nothing about the company.
BOARDS = (
    "linkedin.", "dev.bg", "smartrecruiters.", "greenhouse.io", "lever.co", "workable.", "remotive.", "remoteok.",
    "indeed.", "glassdoor.", "jobs.bg", "zaplata.bg", "google.", "myworkdayjobs.", "workday.", "ashbyhq.", "teamtailor.",
    "recruitee.", "bamboohr.", "personio.", "jobvite.", "icims.", "breezy.hr", "weworkremotely.", "wellfound.", "angel.co",
    "stackoverflow.", "github.", "jsearch", "rapidapi.", "careers-page.", "join.com", "jobs.lever", "successfactors.",
)
# Words left out when guessing a domain from a company name.
NOISE = {"bulgaria", "eood", "ood", "ad", "ltd", "llc", "inc", "gmbh", "plc", "sa", "bv", "group", "the", "co", "company",
         "corp", "corporation", "limited", "holding", "holdings", "international", "global", "technologies", "technology",
         "digital", "solutions", "services", "centre", "center", "software", "labs", "development", "and", "&"}
RETRY_AFTER = 30 * 86400
TIMEOUT = 4.0
_lock = threading.Lock()


def _dir() -> Path:
    path = settings.DATA_DIR / "logos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def key(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:80] or "unknown"


def _index_path() -> Path:
    return _dir() / "index.json"


def _index() -> dict:
    try:
        return json.loads(_index_path().read_text())
    except (OSError, ValueError):
        return {}


def _remember(k: str, entry: dict) -> None:
    with _lock:
        index = _index()
        index[k] = entry
        tmp = _index_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(index))
        tmp.replace(_index_path())


def company_domain(url: str) -> str:
    """The domain of a job link when it is the company's own site ('' for job boards)."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host or any(b in host for b in BOARDS):
        return ""
    parts = host.split(".")
    # careers.sumup.com -> sumup.com; keep two-part country domains like co.uk
    if len(parts) > 2:
        parts = parts[-3:] if parts[-2] in ("co", "com", "org") and len(parts[-1]) == 2 else parts[-2:]
    return ".".join(parts)


def _known_domains() -> dict[str, str]:
    """Company name -> domain from your company list (career page links)."""
    try:
        from . import companies

        out = {}
        for group in companies.load().values():
            for c in group:
                d = next((company_domain(str(v)) for k, v in c.items() if k in ("url", "website", "careers", "careers_url") and v), "")
                if c.get("name") and d:
                    out[key(c["name"])] = d
        return out
    except Exception as e:  # an unreadable company list only costs the logos their known domains
        log.warning("company list not readable for logos: %s", e)
        return {}


def _guesses(company: str, tld_hint: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower()) if w not in NOISE]
    if not words:
        return []
    joined = "".join(words)
    out = [f"{joined}.com"]
    if len(words) > 1:
        out.append(f"{words[0]}.com")
    if tld_hint:
        out += [f"{joined}.{tld_hint}", f"{words[0]}.{tld_hint}"]
    return list(dict.fromkeys(out))


def _is_image(r: httpx.Response) -> bool:
    kind = r.headers.get("content-type", "")
    return r.status_code == 200 and len(r.content) > 100 and (kind.startswith("image/") or r.content[:4] in (b"\x89PNG", b"\x00\x00\x01\x00") or r.content[:3] == b"\xff\xd8\xff")


def _fetch_icon(domain: str, c: httpx.Client) -> tuple[bytes, str] | None:
    for url in (f"https://{domain}/apple-touch-icon.png", f"https://icons.duckduckgo.com/ip3/{domain}.ico"):
        try:
            r = c.get(url)
        except (httpx.HTTPError, web_fetch.BlockedURL):
            continue
        if _is_image(r) and len(r.content) < 2_000_000:
            kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
            # never serve it back as anything but a plain image (no HTML, no SVG with scripts)
            return r.content, kind if kind in {"image/png", "image/jpeg", "image/x-icon", "image/vnd.microsoft.icon", "image/gif", "image/webp"} else "image/png"
    return None


def logo(company: str, job_url: str = "", location: str = "") -> tuple[bytes, str] | None:
    """The logo image for a company (bytes, media type), looking it up the first time."""
    k = key(company)
    entry = _index().get(k)
    if entry:
        if entry.get("file"):
            path = _dir() / entry["file"]
            if path.exists():
                return path.read_bytes(), entry.get("type", "image/png")
        elif time.time() - entry.get("checked", 0) < RETRY_AFTER:
            return None
    tld = "bg" if "bulgaria" in location.lower() or "българия" in location.lower() else ""
    candidates = [d for d in [company_domain(job_url), _known_domains().get(k)] if d]
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 CVMaker"},
                      event_hooks={"request": [web_fetch.guard_request]}) as c:
        found = None
        for domain in candidates:
            found = _fetch_icon(domain, c)
            if found:
                break
        if not found:
            # A guessed domain only counts when the icon service knows it (it answers 404 for unknown domains).
            for domain in _guesses(company, tld):
                found = _fetch_icon(domain, c)
                if found:
                    break
    if not found:
        _remember(k, {"checked": time.time()})
        return None
    data, kind = found
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico", "image/svg+xml": ".svg"}.get(kind, ".img")
    (_dir() / f"{k}{ext}").write_bytes(data)
    _remember(k, {"file": f"{k}{ext}", "type": kind, "checked": time.time()})
    return data, kind


def known(company: str) -> bool | None:
    """True when a logo is cached, False when it is known to be missing, None when not looked up yet."""
    entry = _index().get(key(company))
    if not entry:
        return None
    return bool(entry.get("file"))
