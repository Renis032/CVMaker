"""What your reactions teach: hide reasons and likes become a ranking nudge, filter suggestions and Claude context.

Every job is described by a few features: its seniority, company, location, the tech terms it mentions and
whether it states a salary. A hide with a reason counts against the features that reason is about (too
senior -> the seniority; wrong stack -> the tech terms you don't have; ...). Anything you liked or moved
into the pipeline counts a little for all its features. A job's taste score is the sum over its features,
so it can be explained ("↓ senior roles", "↑ spring boot"). It is recomputed whenever your feedback changes.
"""

import json
import math
import re
from collections import Counter, defaultdict

from . import db, settings
from .cvcheck import TECH_TERMS
from .models import Criteria, Job, Profile
from .search import SENIORITY_LEVELS, _contains, seniority


def _blocked_path():
    return settings.DATA_DIR / "feedback.json"


REASONS = {
    "too_senior": "Too senior",
    "too_junior": "Too junior",
    "stack": "Wrong tech stack",
    "location": "Wrong location",
    "salary": "Salary too low / not stated",
    "company": "Not this company (hide all its jobs)",
    "not_relevant": "Not relevant",
}
POSITIVE = set(db.STAGES)

# How much one reaction moves a feature, and how far feedback can move a job up or down the "For you" sort.
EXPLICIT, IMPLICIT_HIDE, LIKE = 1.0, 0.2, 0.5
SCALE, LIMIT = 10, 30
MIN_EXPLAIN = 4  # points a feature must contribute to be named on the job
# Practices and general words that appear in most postings: never blamed for a "wrong tech stack" hide.
GENERIC_TERMS = {
    "performance", "agile", "scrum", "design patterns", "oop", "solid", "tdd", "unit testing", "integration testing",
    "security", "cloud", "algorithms", "data structures", "english", "german", "git", "jira", "rest", "microservices",
    "distributed systems", "concurrency", "multithreading", "profiling", "ci/cd", "linux", "sql", "html", "css",
}
# Features most jobs share say nothing on their own; only an explicit hide reason moves them.
EXPLICIT_ONLY = {"salary:none"}
DESCRIPTION_CHARS = 3000


def _company(name: str) -> str:
    return " ".join(name.lower().split())


def _city(job: Job) -> str:
    if job.remote and (not job.location or "remote" in job.location.lower()):
        return "remote"
    first = re.split(r"[,(/;|-]", job.location.lower())[0].strip()
    return first or ""


# --- blocked companies and dismissed suggestions -----------------------------------------------------------


def _state() -> dict:
    if _blocked_path().exists():
        return json.loads(_blocked_path().read_text())
    return {"blocked_companies": [], "dismissed": []}


def _save_state(state: dict) -> None:
    _blocked_path().write_text(json.dumps(state, indent=2, ensure_ascii=False))


def blocked_companies() -> list[str]:
    return _state()["blocked_companies"]


def is_blocked(company: str) -> bool:
    return _company(company) in {_company(c) for c in blocked_companies()}


def block_company(company: str) -> None:
    state = _state()
    if not any(_company(c) == _company(company) for c in state["blocked_companies"]):
        state["blocked_companies"].append(company.strip())
        _save_state(state)


def unblock_company(company: str) -> None:
    state = _state()
    state["blocked_companies"] = [c for c in state["blocked_companies"] if _company(c) != _company(company)]
    _save_state(state)


def dismiss(suggestion_id: str) -> None:
    state = _state()
    state.setdefault("dismissed", []).append(suggestion_id)
    _save_state(state)


# --- features ------------------------------------------------------------------------------------------

# Keyed by what the features are computed from (the job's text and the terms looked for), never by ids alone:
# job ids and person ids repeat across accounts, and two profiles can have the same number of skills.
_feature_cache: dict[tuple, dict[str, str]] = {}
_FEATURE_CACHE_MAX = 20000


def _terms(profile: Profile | None) -> list[str]:
    skills = {i.lower() for g in profile.skills for i in g.items if 1 < len(i) < 30} if profile else set()
    return sorted({t.lower() for t in TECH_TERMS} | skills)


def features(job: Job, terms: list[str]) -> dict[str, str]:
    """feature id -> human label, e.g. {'level:senior': 'senior roles', 'tech:php': 'php'}."""
    key = (job.id, hash((job.title, job.company, job.location, job.remote, bool(job.salary), tuple(job.tags),
                         job.description[:DESCRIPTION_CHARS])), hash(tuple(terms)))
    if key in _feature_cache:
        return _feature_cache[key]
    if len(_feature_cache) >= _FEATURE_CACHE_MAX:
        _feature_cache.clear()
    text = f"{job.title}\n{job.description[:DESCRIPTION_CHARS]}".lower()
    level = seniority(job)
    found = {f"level:{level}": f"{level} roles", f"company:{_company(job.company)}": job.company}
    city = _city(job)
    if city:
        found[f"loc:{city}"] = city.title() if city != "remote" else "remote"
    if not job.salary:
        found["salary:none"] = "no salary stated"
    for t in terms:
        if _contains(text, t):
            found[f"tech:{t}"] = t
    # Drop a term contained in a longer one ("spring" inside "spring boot").
    techs = [f[5:] for f in found if f.startswith("tech:")]
    for t in techs:
        if any(t != o and _contains(o, t) for o in techs):
            found.pop(f"tech:{t}")
    _feature_cache[key] = found
    return found


def _tech(feats: dict[str, str]) -> list[str]:
    return [f for f in feats if f.startswith("tech:")]


# --- learning ------------------------------------------------------------------------------------------


def learn(profile: Profile | None) -> dict:
    """Feature weights plus the counts behind them, from every job you reacted to."""
    terms = _terms(profile)
    have = {i.lower() for g in profile.skills for i in g.items} if profile else set()
    weights: dict[str, float] = defaultdict(float)
    labels: dict[str, str] = {}
    reasons: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    stack_terms: Counter = Counter()
    stack_title_terms: Counter = Counter()
    hidden_levels: Counter = Counter()
    low_salary = 0.0
    liked_titles = []

    for r in db.feedback_rows():
        job = r["job"]
        feats = features(job, terms)
        labels.update(feats)
        if r["status"] in POSITIVE:
            liked_titles.append(job.title)
            techs = _tech(feats)
            for f in feats:
                if f not in EXPLICIT_ONLY:
                    weights[f] += LIKE / (math.sqrt(len(techs)) if f.startswith("tech:") and techs else 1)
            continue
        reason = r["feedback"]
        reasons[reason or "none"] += 1
        if len(examples[reason]) < 4:
            examples[reason].append(f"{job.title} @ {job.company}")
        if reason in ("too_senior", "too_junior"):
            level = next(f for f in feats if f.startswith("level:"))
            weights[level] -= EXPLICIT
            hidden_levels[level[6:]] += 1
        elif reason == "stack":
            # Blame the specific tech you don't have: in the title first, else in the description.
            specific = [f for f in _tech(feats) if f[5:] not in have and f[5:] not in GENERIC_TERMS]
            in_title = [f for f in specific if _contains(job.title.lower(), f[5:])]
            foreign = in_title or specific
            for f in foreign:
                weights[f] -= EXPLICIT / math.sqrt(len(foreign))
                stack_terms[f[5:]] += 1
                if _contains(job.title.lower(), f[5:]):
                    stack_title_terms[f[5:]] += 1
        elif reason == "location":
            for f in feats:
                if f.startswith("loc:"):
                    weights[f] -= EXPLICIT
        elif reason == "salary":
            if not job.salary:
                weights["salary:none"] -= EXPLICIT
            elif r["salary_month"]:
                low_salary = max(low_salary, r["salary_month"])
        elif reason == "company":
            weights[f"company:{_company(job.company)}"] -= 3 * EXPLICIT
        else:  # no reason or "not relevant": a little against everything about it
            for f in feats:
                if f not in EXPLICIT_ONLY:
                    weights[f] -= IMPLICIT_HIDE
    return {
        "weights": dict(weights),
        "labels": labels,
        "reasons": reasons,
        "examples": examples,
        "stack_terms": stack_terms,
        "stack_title_terms": stack_title_terms,
        "hidden_levels": hidden_levels,
        "low_salary": low_salary,
        "liked_titles": liked_titles,
        "terms": terms,
    }


def score_job(job: Job, salary_month: float | None, model: dict) -> tuple[int, list[str]]:
    """(taste score, explanations like '↓ senior roles') for one job."""
    contributions = []
    for f, label in features(job, model["terms"]).items():
        w = model["weights"].get(f, 0.0)
        if w:
            contributions.append((w * SCALE, label))
    if model["low_salary"] and salary_month and salary_month <= model["low_salary"]:
        contributions.append((-EXPLICIT * SCALE, "salary like ones you hid"))
    total = max(-LIMIT, min(LIMIT, round(sum(c for c, _ in contributions))))
    named = sorted((c for c in contributions if abs(c[0]) >= MIN_EXPLAIN), key=lambda c: -abs(c[0]))[:3]
    return total, [("↑ " if c > 0 else "↓ ") + label for c, label in named]


def recompute(profile: Profile | None = None) -> None:
    """Re-score every open job from the current feedback."""
    from . import assistant

    profile = profile or assistant.load_profile()
    model = learn(profile)
    db.set_tastes([(*score_job(job, salary, model), job.id) for job, salary in db.open_jobs()])


# --- what to show and tell Claude ------------------------------------------------------------------------


def summary(profile: Profile | None) -> dict:
    model = learn(profile)
    likes = sorted(((w, f) for f, w in model["weights"].items() if w > 0.4), reverse=True)[:8]
    dislikes = sorted((w, f) for f, w in model["weights"].items() if w < -0.4)[:8]
    return {
        "hidden": sum(model["reasons"].values()),
        "liked": len(model["liked_titles"]),
        "reasons": [(REASONS.get(k, "No reason given"), n) for k, n in model["reasons"].most_common()],
        "likes": [model["labels"].get(f, f) for _, f in likes],
        "dislikes": [model["labels"].get(f, f) for _, f in dislikes],
        "model": model,
    }


def for_prompt(profile: Profile | None) -> str:
    """The candidate's preferences as shown by their own reactions, for Claude's fit ratings ('' if none yet)."""
    s = summary(profile)
    model = s["model"]
    if not s["hidden"] and not s["liked"]:
        return ""
    lines = []
    for key, n in model["reasons"].most_common():
        label = REASONS.get(key, "no reason given").split(" (")[0].lower()
        examples = "; ".join(model["examples"].get(key if key != "none" else "", [])[:3])
        lines.append(f"- Hid {n} job{'s' if n != 1 else ''} as '{label}'" + (f" (e.g. {examples})" if examples else ""))
    if model["stack_terms"]:
        lines.append(f"- Tech they rejected: {', '.join(t for t, _ in model['stack_terms'].most_common(8))}")
    if blocked_companies():
        lines.append(f"- Does not want to work for: {', '.join(blocked_companies())}")
    if s["dislikes"]:
        lines.append(f"- Patterns they tend to avoid: {', '.join(s['dislikes'])}")
    if model["liked_titles"]:
        lines.append(f"- Jobs they liked or applied to: {'; '.join(model['liked_titles'][:8])}")
    if s["likes"]:
        lines.append(f"- Patterns they tend to like: {', '.join(s['likes'])}")
    return "\n".join(lines)


def suggestions(criteria: Criteria, model: dict) -> list[dict]:
    """Filter changes your feedback points to, that the active search doesn't have yet."""
    dismissed = set(_state().get("dismissed", []))
    out = []
    current_levels = criteria.seniority or SENIORITY_LEVELS
    for level, n in model["hidden_levels"].most_common():
        if n >= 2 and level in current_levels and len(current_levels) > 1:
            keep = [lvl for lvl in current_levels if lvl != level]
            out.append({
                "id": f"seniority:{level}",
                "text": f"You hid {n} {level}-level jobs as the wrong level. Search only {' + '.join(keep)} roles?",
                "kind": "seniority", "value": level,
            })
    excluded = {x.lower() for x in criteria.exclude}
    for term, n in model["stack_title_terms"].most_common(5):
        if n >= 2 and term not in excluded:
            out.append({
                "id": f"exclude:{term}",
                "text": f"You hid {n} jobs with “{term}” in the title for the tech stack. Exclude titles containing it?",
                "kind": "exclude", "value": term,
            })
    no_salary = model["weights"].get("salary:none", 0)
    if no_salary <= -3 * EXPLICIT and not criteria.salary_only:
        out.append({
            "id": "salary_only",
            "text": f"You hid {round(-no_salary)} jobs for not stating a salary. Only show jobs that state one?",
            "kind": "salary_only", "value": "1",
        })
    return [s for s in out if s["id"] not in dismissed]


def apply_suggestion(criteria: Criteria, kind: str, value: str) -> Criteria:
    if kind == "seniority":
        criteria.seniority = [lvl for lvl in (criteria.seniority or SENIORITY_LEVELS) if lvl != value]
    elif kind == "exclude" and value.lower() not in {x.lower() for x in criteria.exclude}:
        criteria.exclude.append(value)
    elif kind == "salary_only":
        criteria.salary_only = True
    return criteria
