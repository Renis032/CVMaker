"""The insights page: the skills holding you back across all your jobs, and what the market you search looks like.

Everything except the Claude gap analysis is counted straight from the collected jobs, so it is instant and free:
- skill demand: how many postings mention each tech term, whether your profile mentions it, and whether it is
  asked for more or less often in the last four weeks than before,
- salaries (normalised to EUR per month) by seniority and by city,
- postings per week.
The gap analysis sends the gaps from every fit check (full and quick) to Claude, which groups them into a few
skills ranked by how many jobs each would open.
"""

import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import ValidationError

from . import assistant, db, taste
from .models import GapAnalysis, Profile
from .search import SENIORITY_LEVELS, _contains, seniority

PERIODS = {"30": ("Last 30 days", 30), "90": ("Last 90 days", 90), "all": ("Everything collected", None)}
TREND_DAYS = 28
TREND_MIN_JOBS = 5  # jobs needed on each side of the split before calling a trend
TREND_MIN_CHANGE = 0.08  # share of postings a skill must gain or lose to count as rising or falling
WEEKS = 12
TOP_SKILLS = 20
TOP_GAPS = 15
TOP_CITIES = 6
ANALYSIS_KEY = "gaps:analysis"
FIT_GAPS = 6  # gaps per job sent to Claude
# Spellings counted as one skill, and city names boards write in different ways.
SKILL_ALIASES = {"golang": "go", "node": "node.js", "nodejs": "node.js", "k8s": "kubernetes", "postgres": "postgresql"}
CITY_ALIASES = {"софия": "sofia", "sofia-city": "sofia", "пловдив": "plovdiv", "варна": "varna"}


def _when(row: dict) -> float:
    """When the job was posted, or when we first saw it if the board gives no date."""
    try:
        return datetime.fromisoformat(row["job"].posted_at[:10]).timestamp()
    except ValueError:
        return row["first_seen"]


def _in_period(rows: list[dict], period: str) -> list[dict]:
    days = PERIODS.get(period, PERIODS["90"])[1]
    if days is None:
        return rows
    since = time.time() - days * 86400
    return [r for r in rows if _when(r) >= since]


def _profile_text(profile: Profile | None) -> str:
    return profile.model_dump_json().lower() if profile else ""


# --- counted from the jobs -----------------------------------------------------------------------------


def skill_demand(rows: list[dict], profile: Profile | None) -> list[dict]:
    terms = taste._terms(profile)
    have_text = _profile_text(profile)
    recent_since = time.time() - TREND_DAYS * 86400
    recent = [r for r in rows if _when(r) >= recent_since]
    older = [r for r in rows if _when(r) < recent_since]

    jobs_by_term: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        found = {SKILL_ALIASES.get(f[5:], f[5:]) for f in taste.features(r["job"], terms) if f.startswith("tech:")}
        for term in found:
            jobs_by_term[term].append(r)

    out = []
    for term, found in jobs_by_term.items():
        trend = None
        if len(recent) >= TREND_MIN_JOBS and len(older) >= TREND_MIN_JOBS:
            ids = {id(r) for r in found}
            change = sum(id(r) in ids for r in recent) / len(recent) - sum(id(r) in ids for r in older) / len(older)
            if abs(change) >= TREND_MIN_CHANGE:
                trend = "rising" if change > 0 else "falling"
        out.append({
            "term": term,
            "count": len(found),
            "pct": round(100 * len(found) / len(rows)) if rows else 0,
            "have": _contains(have_text, term),
            "trend": trend,
            "jobs": [{"id": r["job"].id, "title": r["job"].title, "company": r["job"].company}
                     for r in sorted(found, key=_when, reverse=True)],
        })
    return sorted(out, key=lambda s: (-s["count"], s["term"]))


def _salary_group(label: str, values: list[float]) -> dict:
    values = sorted(values)
    return {"label": label, "n": len(values), "min": values[0], "max": values[-1],
            "median": statistics.median(values), "points": values}


def salaries(rows: list[dict]) -> dict:
    paid = [r for r in rows if r["salary_month"]]
    by_level, by_city = defaultdict(list), defaultdict(list)
    for r in paid:
        by_level[seniority(r["job"])].append(r["salary_month"])
        city = taste._city(r["job"])
        city = CITY_ALIASES.get(city, city)
        if city:
            by_city[city.title() if city != "remote" else "Remote"].append(r["salary_month"])
    levels = [_salary_group(f"{lvl.title()}", by_level[lvl]) for lvl in SENIORITY_LEVELS if by_level[lvl]]
    cities = sorted((_salary_group(c, v) for c, v in by_city.items()), key=lambda g: (-g["n"], g["label"]))[:TOP_CITIES]
    groups = levels + cities
    return {
        "n": len(paid),
        "median": statistics.median([r["salary_month"] for r in paid]) if paid else None,
        "levels": levels,
        "cities": cities,
        "axis_max": max((g["max"] for g in groups), default=0),
    }


def weekly(rows: list[dict]) -> list[dict]:
    today = datetime.now().date()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
    counts = [0] * WEEKS
    for r in rows:
        day = datetime.fromtimestamp(_when(r)).date()
        week = (day - start).days // 7
        if 0 <= week < WEEKS:
            counts[week] += 1
    return [{"start": (start + timedelta(weeks=i)).strftime("%d %b"), "count": n} for i, n in enumerate(counts)]


def market(profile: Profile | None, period: str) -> dict:
    rows = _in_period(db.market_rows(), period)
    skills = [s for s in skill_demand(rows, profile) if s["term"] not in taste.GENERIC_TERMS]
    return {
        "n": len(rows),
        "levels": {lvl: sum(seniority(r["job"]) == lvl for r in rows) for lvl in SENIORITY_LEVELS},
        "skills": skills[:TOP_SKILLS],
        "salaries": salaries(rows),
        "weekly": weekly(rows),
    }


def keyword_gaps(profile: Profile | None) -> dict:
    """Tech terms your open jobs ask for that your profile never mentions."""
    rows = [r for r in db.market_rows() if r["status"] != "hidden"]
    missing = [s for s in skill_demand(rows, profile) if not s["have"] and s["term"] not in taste.GENERIC_TERMS]
    return {"n": len(rows), "missing": missing[:TOP_GAPS]}


# --- Claude's gap analysis -----------------------------------------------------------------------------


def gap_sources() -> list[dict]:
    """Every open job with a fit check, with the gaps that check found."""
    out = []
    for r in db.market_rows():
        if r["status"] == "hidden":
            continue
        job = r["job"]
        full = db.cache_get(f"fit2:{job.id}", 24 * 365)
        if full:
            gaps = [f"{c['point']} (severity {c['rating']}/10)" for c in
                    sorted(full.get("cons", []), key=lambda c: -c["rating"])[:FIT_GAPS]]
            gaps += [f"{g['area']} (severity {g['severity']}/10)" for g in
                     (full.get("gap_plan") or {}).get("lacking_areas", [])[:FIT_GAPS]]
            kind, rating = "full", full.get("rating")
        elif r["fit_data"] and r["fit_data"].get("missing"):
            gaps, kind, rating = r["fit_data"]["missing"], "quick", r["fit"]
        else:
            continue
        out.append({"job_id": job.id, "title": job.title, "company": job.company, "fit": rating, "kind": kind,
                    "gaps": gaps})
    return out


def _signature(sources: list[dict]) -> list[str]:
    return sorted(f"{s['job_id']}:{s['kind']}" for s in sources)


def run_analysis(profile: Profile, progress) -> str:
    sources = gap_sources()
    if not sources:
        raise ValueError("No fit checks yet: rate some jobs with Claude first.")
    progress.set(step=f"Claude is grouping the gaps from {len(sources)} fit checks")
    refs = {f"J{i + 1}": s for i, s in enumerate(sources)}
    analysis = assistant.gap_analysis(profile, [
        {"ref": ref, "title": s["title"], "company": s["company"], "fit_rating": s["fit"], "gaps": s["gaps"]}
        for ref, s in refs.items()
    ])
    db.cache_put(ANALYSIS_KEY, {
        "analysis": analysis.model_dump(),
        "jobs": {ref: {"id": s["job_id"], "title": s["title"], "company": s["company"], "fit": s["fit"]}
                 for ref, s in refs.items()},
        "signature": _signature(sources),
    })
    return "/insights#gaps"


def _job_name(job: dict | None) -> str:
    return f"{job['title']} ({job['company']})" if job else ""


def cached_analysis() -> dict | None:
    """The last analysis with job refs resolved, and how many fit checks were added or changed since."""
    cached = db.cache_get(ANALYSIS_KEY, 24 * 365)
    if not cached:
        return None
    try:
        analysis = GapAnalysis.model_validate(cached["analysis"])
    except (ValidationError, KeyError, TypeError):  # saved by an older version: analysed again on request
        return None
    jobs = cached["jobs"]

    def named(text: str) -> str:
        # Claude is asked not to, but a stray "J7" would mean nothing to you: show the job instead.
        return re.sub(r"\bJ(\d+)\b", lambda m: _job_name(jobs.get(m.group(0))) or m.group(0), text)

    clusters = [
        {**c.model_dump(), "why": named(c.why), "how_to_close": named(c.how_to_close),
         "jobs": [jobs[ref] for ref in dict.fromkeys(c.job_refs) if ref in jobs]}
        for c in sorted(analysis.clusters, key=lambda c: (-len(set(c.job_refs)), -c.severity))
    ]
    with db.connect() as c:
        row = c.execute("SELECT fetched_at FROM cache WHERE key = ?", (ANALYSIS_KEY,)).fetchone()
    return {
        "summary": named(analysis.summary),
        "learn_next": [named(step) for step in analysis.learn_next],
        "clusters": clusters,
        "total_jobs": len(jobs),
        "at": row["fetched_at"] if row else None,
        "changed": len(set(_signature(gap_sources())) ^ set(cached["signature"])),
    }
