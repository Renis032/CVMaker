import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yaml

from . import categories, db, people, settings
from .countries import COUNTRY_TERMS
from .models import Criteria, Job, Profile
from .salary import find_salary
from .sources import BOARD_SOURCES, COMPANY_SOURCES, board_applies, board_has_key

# One search at a time, whether started by hand or by the automatic schedule.
search_lock = threading.Lock()

SENIORITY_WORDS = {
    "junior": ["junior", "jr", "entry", "graduate", "intern", "internship", "trainee", "младши"],
    "senior": ["senior", "sr", "staff", "principal", "lead", "expert", "architect", "head", "старши"],
}
SENIORITY_LEVELS = ["junior", "mid", "senior"]


def load_companies() -> dict[str, list[dict]]:
    with open(settings.COMPANIES_FILE) as f:
        return yaml.safe_load(f) or {}


def seniority(job: Job) -> str:
    text = f"{job.title} {' '.join(job.tags)}".lower()
    for level, words in SENIORITY_WORDS.items():
        if any(_contains(text, w) for w in words):
            return level
    return "mid"


@dataclass
class SearchResult:
    job_ids: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _contains(text: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", term):
        return re.search(rf"\b{re.escape(term)}\b", text) is not None
    return term in text


def _location_ok(job: Job, criteria: Criteria) -> bool:
    loc = f" {job.location.lower()} "
    for country in criteria.countries:
        if any(_contains(loc, t) for t in COUNTRY_TERMS.get(country.upper(), [country.lower()])):
            return True
    if criteria.include_remote and (job.remote or "remote" in loc):
        if not job.location.strip() or "remote" == job.location.lower().strip():
            return True
        return any(_contains(loc, r) for r in criteria.remote_regions)
    return False


def _age_ok(job: Job, criteria: Criteria) -> bool:
    if not job.posted_at:
        return True
    try:
        posted = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted <= timedelta(days=criteria.max_age_days)


def _title_ok(job: Job, criteria: Criteria) -> bool:
    title = job.title.lower()
    if any(_contains(title, x) for x in criteria.exclude):
        return False
    return not criteria.role_keywords or any(_contains(title, k) for k in criteria.role_keywords)


def _options_ok(job: Job, criteria: Criteria) -> bool:
    if criteria.remote_only and not (job.remote or "remote" in job.location.lower()):
        return False
    if criteria.seniority and seniority(job) not in criteria.seniority:
        return False
    if criteria.salary_only and not job.salary:
        return False
    # Sources that list titles only have no description yet; keep them rather than drop unseen text.
    if criteria.must_mention and job.description:
        text = f"{job.title} {' '.join(job.tags)} {job.description}".lower()
        return any(_contains(text, k) for k in criteria.must_mention)
    return True


def _keep(job: Job, criteria: Criteria) -> bool:
    return _title_ok(job, criteria) and _location_ok(job, criteria) and _age_ok(job, criteria) and _options_ok(job, criteria)


def score(job: Job, criteria: Criteria, profile: Profile | None) -> int:
    """Cheap keyword score (0-100) for ranking; the AI fit check on the job page is the real judgement."""
    text = f"{job.title} {' '.join(job.tags)} {job.description}".lower()
    title = job.title.lower()
    wanted = {k.lower() for k in criteria.skill_keywords}
    if profile:
        wanted |= {i.lower() for g in profile.skills for i in g.items if len(i) > 1}
    if not wanted:
        return 0
    hits = sum(1 for w in wanted if _contains(text, w))
    title_hits = sum(1 for k in criteria.skill_keywords if _contains(title, k))
    return min(100, round(100 * hits / max(len(criteria.skill_keywords) + 3, 1)) + 15 * title_hits)


def _run_company(company: dict, criteria: Criteria) -> list[Job]:
    return COMPANY_SOURCES[company["type"]](company, criteria)


def run_search(
    criteria: Criteria, profile: Profile | None, on_progress: Callable[[dict], None] | None = None
) -> SearchResult:
    """Search every applicable source. on_progress gets {source: None (running) | count | "error"} as they finish."""
    with search_lock:
        return _run_search(criteria, profile, on_progress or (lambda _: None))


def _run_search(criteria: Criteria, profile: Profile | None, on_progress: Callable[[dict], None]) -> SearchResult:
    criteria = categories.expand(criteria)
    companies = load_companies()
    tasks = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        groups = [c.upper() for c in criteria.countries] + (["REMOTE"] if criteria.include_remote else [])
        for group in groups:
            for company in companies.get(group, []):
                if criteria.sources and company["type"] not in criteria.sources:
                    continue
                tasks[people.submit(pool, _run_company, company, criteria)] = company["name"]
        for name, fn in BOARD_SOURCES.items():
            if criteria.sources and name not in criteria.sources:
                continue
            if not board_applies(name, criteria):
                continue
            if not board_has_key(name) and name not in criteria.sources:
                continue
            tasks[people.submit(pool, fn, criteria)] = name

        result = SearchResult()
        found: dict[str, Job] = {}
        progress: dict = {label: None for label in tasks.values()}
        on_progress(dict(progress))
        for future in as_completed(tasks):
            label = tasks[future]
            try:
                jobs = future.result()
            except Exception as e:
                result.errors.append(f"{label}: {e}")
                progress[label] = "error"
                on_progress(dict(progress))
                continue
            for j in jobs:
                if not j.salary:
                    j.salary = find_salary(f"{j.title}\n{j.description}")
            kept = [j for j in jobs if _keep(j, criteria)]
            result.counts[label] = len(kept)
            progress[label] = len(kept)
            on_progress(dict(progress))
            for j in kept:
                found[j.id] = j

    from .taste import is_blocked  # taste imports this module

    deduped: dict[tuple[str, str], Job] = {}
    for job in found.values():
        if is_blocked(job.company):
            continue
        key = (job.company.lower().strip(), re.sub(r"\W+", " ", job.title.lower()).strip())
        if key not in deduped or len(job.description) > len(deduped[key].description):
            deduped[key] = job

    jobs = list(deduped.values())
    scores = {j.id: score(j, criteria, profile) for j in jobs}
    jobs = [j for j in jobs if scores[j.id] >= criteria.min_score]
    result.new_ids = db.upsert_jobs(jobs, scores)
    result.job_ids = [j.id for j in jobs]
    return result
