"""Company list (companies.yaml): add, remove, and work out where a company publishes its jobs."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import yaml

from . import settings
from .models import Criteria, Job
from .sources import COMPANY_SOURCES
from .web_fetch import fetch_html, page_links

# Hiring platforms recognisable from a link or embed on a company's careers page.
PLATFORM_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ("greenhouse", r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    ("recruitee", r"([A-Za-z0-9-]+)\.recruitee\.com"),
    ("personio", r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
]
IGNORED_SLUGS = {"embed", "api", "j", "jobs", "v1", "www", "static", "assets", "careers", "job_board"}
CAREER_WORDS = re.compile(r"career|jobs?\b|join[- ]us|work[- ]with[- ]us|vacanc|openings|hiring|кариер|работа|позиции|свободни", re.I)


def _read() -> tuple[str, dict[str, list[dict]]]:
    text = settings.COMPANIES_FILE.read_text()
    lines = text.splitlines()
    first_data = next((i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")), len(lines))
    header = "\n".join(lines[:first_data]).rstrip() + "\n\n"
    return header, yaml.safe_load(text) or {}


def load() -> dict[str, list[dict]]:
    return _read()[1]


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under their key, matching the hand-written file."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _write(data: dict[str, list[dict]]) -> None:
    header, _ = _read()
    groups = [
        yaml.dump({group: items}, Dumper=_IndentedDumper, sort_keys=False, allow_unicode=True, width=1000)
        for group, items in data.items()
    ]
    settings.COMPANIES_FILE.write_text(header + "\n".join(groups))


def add(group: str, company: dict) -> None:
    _, data = _read()
    group = group.upper()
    entries = [c for c in data.get(group, []) if c["name"].lower() != company["name"].lower()]
    data[group] = entries + [company]
    _write(data)


def remove(group: str, name: str) -> None:
    _, data = _read()
    data[group] = [c for c in data.get(group, []) if c["name"] != name]
    if not data[group]:
        del data[group]
    _write(data)


def _platform_in(html: str) -> tuple[str, str] | None:
    for platform, pattern in PLATFORM_PATTERNS:
        for slug in re.findall(pattern, html):
            if slug.lower() not in IGNORED_SLUGS:
                return platform, slug
    return None


def _careers_link(html: str, base_url: str) -> str:
    domain = ".".join(urlparse(base_url).netloc.split(".")[-2:])
    scored = []
    for text, href in page_links(html, base_url):
        target = urlparse(href)
        if not target.netloc.endswith(domain) and not _platform_in(href):
            continue
        hits = len(CAREER_WORDS.findall(text)) * 2 + len(CAREER_WORDS.findall(target.path))
        if hits:
            scored.append((hits, -len(href), href))
    return max(scored)[2] if scored else ""


def _has_jobs(platform: str, slug: str) -> bool:
    try:
        return bool(COMPANY_SOURCES[platform]({"name": slug, "slug": slug}, Criteria(countries=[], sources=[])))
    except Exception:
        return False


def _guess_platform(url: str, name: str) -> tuple[str, str] | None:
    """Try the hiring platforms' public APIs with slugs made from the domain and company name."""
    stem = urlparse(url).netloc.removeprefix("www.").split(".")[0]
    slugs = list(dict.fromkeys(s for s in (stem, re.sub(r"[^a-z0-9]", "", name.lower())) if s))
    candidates = [(p, s) for s in slugs for p in ("greenhouse", "lever", "ashby", "workable", "recruitee", "personio")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        hits = list(pool.map(lambda c: _has_jobs(*c), candidates))
    return next((c for c, hit in zip(candidates, hits) if hit), None)


def discover(url: str, name: str = "") -> dict:
    """Find a company's jobs from its website or careers page.

    Returns {"type", "slug" or "url", "careers_url", "notes"}: a hiring-platform entry when the page links to
    a known platform, otherwise a careerpage entry for the page that lists the jobs.
    """
    if not re.match(r"https?://", url):
        url = "https://" + url
    notes = []
    html = fetch_html(url)
    careers_url = url
    found = _platform_in(html)
    if not found and not CAREER_WORDS.search(urlparse(url).path):
        link = _careers_link(html, url)
        if link:
            careers_url = link
            notes.append(f"Careers page found: {link}")
            html = fetch_html(link)
            found = _platform_in(link) or _platform_in(html)
    if not found:
        found = _guess_platform(url, name)
        if found:
            notes.append(f"Not linked from the site, but '{found[1]}' on {found[0]} has jobs - check they are this company's.")
    rendered = False
    if not found:
        try:
            html_js = fetch_html(careers_url, render=True)
            found = _platform_in(html_js)
            rendered = len(page_links(html_js, careers_url)) > len(page_links(html, careers_url)) + 5
        except Exception as e:
            notes.append(f"Could not render with Chrome: {e}")
    if found:
        platform, slug = found
        notes.append(f"Uses {platform} (board '{slug}') - jobs are read from its public API.")
        return {"type": platform, "slug": slug, "careers_url": careers_url, "notes": notes}
    notes.append("No known hiring platform found - Claude will read the job links on the careers page.")
    if rendered:
        notes.append("The page builds its job list with JavaScript, so it is rendered in Chrome first.")
    return {"type": "careerpage", "url": careers_url, "render": rendered, "careers_url": careers_url, "notes": notes}


def entry(name: str, found: dict, default_location: str = "") -> dict:
    company = {"name": name, "type": found["type"]}
    if found["type"] == "careerpage":
        company["url"] = found["url"]
        if found.get("render"):
            company["render"] = True
    else:
        company["slug"] = found["slug"]
    if default_location:
        company["default_location"] = default_location
    return company


def test(company: dict) -> list[Job]:
    """Fetch the company's current postings without any filtering."""
    return COMPANY_SOURCES[company["type"]](company, Criteria(countries=[], sources=[]))

