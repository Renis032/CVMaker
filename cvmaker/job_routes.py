"""One job and the pipeline: the application stages, insights, the job page, fit check, application pack,
GitHub review and interview prep."""

from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from . import assistant, claims, db, github, insights, pack, render, roles, taste, tasks
from .llm import LLMError
from .models import (
    FitAssessment,
    GithubReview,
    InterviewPrep,
    Job,
    Project,
)
from .sources import ensure_description
from .web import _back, _job_label, _need_profile, _not_found, page

router = APIRouter()


# --- application pipeline --------------------------------------------------------------------------------


@router.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request):
    rows = db.list_jobs(statuses=db.STAGES, sort="newest", closed=None)
    columns = {s: [] for s in db.STAGES}
    for r in sorted(rows, key=lambda r: r["status_at"], reverse=True):
        columns[r["status"]].append(r)
    return page(request, "pipeline.html", columns=columns, due=db.due_follow_ups(), stats=db.stats())


@router.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, period: str = "90"):
    profile = assistant.load_profile()
    period = period if period in insights.PERIODS else "90"
    sources = insights.gap_sources()
    return page(
        request,
        "insights.html",
        period=period,
        periods=insights.PERIODS,
        market=insights.market(profile, period),
        keyword_gaps=insights.keyword_gaps(profile),
        analysis=insights.cached_analysis(),
        sources={"full": sum(s["kind"] == "full" for s in sources), "quick": sum(s["kind"] == "quick" for s in sources)},
        running=(db.task_active("gaps", "gaps") or [None])[0],
    )


@router.post("/insights/analyse")
def insights_analyse():
    _need_profile()
    tasks.submit("gaps", "Gap analysis across your jobs", "gaps", subject="gaps", url="/insights#gaps")
    return _back("/insights#gaps")


@tasks.runner("gaps")
def _gaps(progress):
    return insights.run_analysis(_need_profile(), progress)


@router.post("/job/{job_id:path}/status")
def job_status(
    request: Request,
    job_id: str,
    status: str = Form(...),
    back: str = Form(""),
    date: str = Form(""),
    note: str = Form(""),
    reason: str = Form(""),
):
    at = None
    if date:
        try:
            at = datetime.strptime(date, "%Y-%m-%d").timestamp() + 12 * 3600
        except ValueError:
            pass
    if status == "hidden":
        reason = reason if reason in taste.REASONS else ""
        note = note.strip() or (f"Hidden: {taste.REASONS[reason].split(' (')[0].lower()}" if reason else "")
        db.set_feedback(job_id, reason)
        found = db.get_job(job_id)
        if reason == "company" and found:
            taste.block_company(found[0].company)
            db.hide_company(found[0].company, except_id=job_id)
    if status in db.STAGES or status in ("new", "hidden"):
        db.set_status(job_id, status, at, note.strip())
        taste.recompute()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True})
    return _back(back or f"/job/{job_id}#application")


@router.post("/job/{job_id:path}/application")
def job_application(
    job_id: str,
    notes: str = Form(""),
    contact: str = Form(""),
    next_date: str = Form(""),
    sent_cv_id: int = Form(0),
    sent_letter_id: int = Form(0),
):
    db.save_application(
        job_id, notes=notes.strip(), contact=contact.strip(), next_date=next_date, sent_cv_id=sent_cv_id, sent_letter_id=sent_letter_id
    )
    return _back(f"/job/{job_id}#application")


@router.post("/job/{job_id:path}/followed-up")
def job_followed_up(job_id: str, days: float = Form(7), back: str = Form("/pipeline")):
    db.snooze_follow_up(job_id, days)
    return _back(back)


# --- job page --------------------------------------------------------------------------------------------


@router.get("/job/{job_id:path}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    found = db.get_job(job_id)
    if not found:
        return page(request, "error.html", message=_not_found("Job"))
    job, status = found
    db.mark_seen([job_id])
    fetch_error = ""
    try:
        job = ensure_description(job)
    except Exception as e:
        fetch_error = f"Could not load the full posting: {e}"
    return page(
        request,
        "job.html",
        job=job,
        status=status,
        app=db.get_application(job_id),
        history=db.company_history(job.company, job_id),
        active=db.tasks_for_job(job_id),
        fit=_cached_fit(job_id),
        gh=_cached_review(job_id),
        profile_links=_profile_project_links(),
        cvs=_cvs_with_scores(job_id),
        letters=db.letters_for_job(job_id),
        has_prep=_cached_prep(job_id) is not None,
        pack_info=pack.get_pack(job_id),
        fetch_error=fetch_error,
        roles=roles.load_roles(),
        font_choices=render.font_choices(),
        hide_reasons=taste.REASONS,
        **_later_stages(job),
    )


def _later_stages(job: Job) -> dict:
    """What the posting check, outreach, interview and offer tabs show."""
    from . import checks, stage, stories

    app_row = db.get_application(job.id)
    profile = assistant.load_profile()
    outreach = stage.cached_outreach(job.id)
    practice = db.practice_for_job(job.id)
    return {
        "live": checks.last_check(job.id),
        "local_flags": checks.local_flags(job, profile, app_row),
        "post_review": checks.cached_review(job.id),
        "outreach": outreach,
        "posting_contacts": stage.posting_contacts(job),
        "posting_emails": [c for c in stage.posting_contacts(job) if "@" in c],
        "linkedin_search": stage.linkedin_people_search,
        "debriefs": db.debriefs_for_job(job.id),
        "debrief_rounds": stage.DEBRIEF_ROUNDS,
        "practice_count": len(practice),
        "practice_avg": (lambda xs: round(sum(xs) / len(xs), 1) if xs else None)([p["score"] for p in practice if p["score"] is not None]),
        "story_count": len(stories.load()),
        "offer": stage.cached_offer(job.id),
        "skill_check": _draft_skill_check(job, profile, outreach),
    }


def _draft_skill_check(job: Job, profile, outreach: dict | None) -> dict:
    """Skill check of every draft on the job page that goes to the employer."""
    from . import stage

    texts = {}
    if outreach:
        k = outreach["kit"]
        texts |= {"note": k.linkedin_note, "msg": k.linkedin_message, "app": k.application_email.body, "fu": k.follow_up_email.body}
    for d in db.debriefs_for_job(job.id):
        if d["review"]:
            texts[f"ty-{d['id']}"] = d["review"]["thank_you_email"]["body"]
    offer = stage.cached_offer(job.id)
    if offer and offer.get("review"):
        texts["reply"] = offer["review"].reply_email.body
    return claims.check_many(texts, profile, job)


def _cached_fit(job_id: str) -> FitAssessment | None:
    cached = db.cache_get(f"fit2:{job_id}", 24 * 365)
    if not cached:
        return None
    # Checks made before cons had advice and before the gap plan existed: show what they have.
    for con in cached.get("cons", []):
        con.setdefault("advice", "")
    cached.setdefault("gap_plan", {"recommendation": "", "advice": "", "lacking_areas": [], "project_types": [],
                                   "action_plan": [], "interview_framing": [], "closer_roles": []})
    try:
        return FitAssessment.model_validate(cached)
    except ValidationError:
        return None


def _cvs_with_scores(owner_id: str) -> list[dict]:
    rows = db.cvs_for_job(owner_id)
    for r in rows:
        _, tailored, _ = db.get_cv(r["id"])
        r["match_score"] = tailored.match_score
        rating = db.cache_get(f"cvrate:{r['id']}:profile", 24 * 365)
        r["rated"] = rating["comparison"]["tailored"]["overall"] if rating else None
        r["baseline_rated"] = rating["comparison"]["baseline"]["overall"] if rating else None
    return rows


@router.post("/job/{job_id:path}/fit")
def job_fit(job_id: str):
    _need_profile()
    job, _ = db.get_job(job_id)
    tasks.submit("fit", _job_label("Fit check", job), "fit", subject=f"fit:{job_id}", job_id=job_id, url=f"/job/{job_id}#fit",
                 args={"job_id": job_id})
    return _back(f"/job/{job_id}#fit")


@tasks.runner("fit")
def _fit(progress, job_id: str) -> str:
    pack.fit_check(_need_profile(), ensure_description(db.get_job(job_id)[0]), progress)
    return f"/job/{job_id}#fit"


@router.post("/job/{job_id:path}/pack")
def job_pack(job_id: str):
    _need_profile()
    pack.submit_pack(job_id)
    return _back(f"/job/{job_id}")


def _cached_review(job_id: str) -> GithubReview | None:
    cached = db.cache_get(f"ghreview:{job_id}", 24 * 365)
    try:
        return GithubReview.model_validate(cached) if cached else None
    except ValidationError:
        return None


def _profile_project_links() -> set[str]:
    profile = assistant.load_profile()
    if not profile:
        return set()
    return {p.link.rstrip("/").lower() for p in profile.projects if p.link}


@router.post("/job/{job_id:path}/github")
def job_github(job_id: str, refresh: int = Form(0)):
    _need_profile()
    job, _ = db.get_job(job_id)
    tasks.submit("github", _job_label("GitHub check", job), "github", subject=f"github:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#github", args={"job_id": job_id, "refresh": bool(refresh)})
    return _back(f"/job/{job_id}#github")


@tasks.runner("github")
def _github(progress, job_id: str, refresh: bool) -> str:
    profile = _need_profile()
    progress.set(step="reading your repos")
    try:
        user = github.username(profile)
        repos = _refetch(user) if refresh else github.fetch_repos(user)
    except github.GithubError as e:
        raise LLMError(str(e)) from e
    progress.set(step="Claude is comparing them with the job")
    pack.github_check(profile, ensure_description(db.get_job(job_id)[0]), repos)
    return f"/job/{job_id}#github"


def _refetch(user: str) -> list[dict]:
    db.cache_delete(f"github:{user}")
    return github.fetch_repos(user)


@router.post("/job/{job_id:path}/github/add")
def job_github_add(job_id: str, repo: str = Form(...)):
    review = _cached_review(job_id)
    profile = assistant.load_profile()
    match = next((m for m in review.matches if m.repo == repo), None) if review else None
    if profile and match:
        existing = next(
            (
                p
                for p in profile.projects
                if p.link.rstrip("/").lower() == match.url.rstrip("/").lower()
                or (match.profile_project and p.name == match.profile_project)
            ),
            None,
        )
        if existing is None:
            profile.projects.append(
                Project(name=match.repo, tech=match.language, link=match.url, bullets=match.cv_bullets)
            )
        else:
            existing.link = match.url
            existing.bullets += [b for b in match.cv_bullets if b not in existing.bullets]
        assistant.save_profile(profile)
    return _back(f"/job/{job_id}#github")



# --- interview prep ----------------------------------------------------------------------------------------


def _cached_prep(job_id: str) -> InterviewPrep | None:
    cached = db.cache_get(f"prep:{job_id}", 24 * 365)
    try:
        return InterviewPrep.model_validate(cached) if cached else None
    except ValidationError:
        return None


@router.get("/prep/{job_id:path}", response_class=HTMLResponse)
def prep_page(request: Request, job_id: str):
    found = db.get_job(job_id)
    if not found:
        return page(request, "error.html", message=_not_found("Job"))
    return page(
        request, "prep.html", job=found[0], prep=_cached_prep(job_id), has_fit=_cached_fit(job_id) is not None,
        active=db.tasks_for_job(job_id),
    )


@router.post("/prep/{job_id:path}")
def prep_generate(job_id: str):
    _need_profile()
    job, _ = db.get_job(job_id)
    tasks.submit("prep", _job_label("Interview prep", job), "prep", subject=f"prep:{job_id}", job_id=job_id, url=f"/prep/{job_id}",
                 args={"job_id": job_id})
    return _back(f"/prep/{job_id}")


@tasks.runner("prep")
def _prep(progress, job_id: str) -> str:
    pack.prep(_need_profile(), ensure_description(db.get_job(job_id)[0]), _cached_fit(job_id), progress)
    return f"/prep/{job_id}"
