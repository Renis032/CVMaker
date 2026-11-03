import csv
import hashlib
import io
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import ValidationError

from . import assistant, auto, claims, companies, cvcheck, cvdiff, db, editor, github, insights, pack, people, render, roles, settings, taste, tasks
from .auto import SavedSearch, load_criteria
from .countries import COUNTRY_TERMS, country_name
from .llm import LLMError
from .models import (
    CVComparison,
    CVContent,
    Criteria,
    FitAssessment,
    GithubReview,
    InterviewPrep,
    InterviewQuestions,
    Job,
    Profile,
    Project,
    RoleProfile,
    SkillGroup,
)
from .posting import posting_html
from .salary import format_monthly
from .search import SENIORITY_LEVELS, load_companies
from .sources import BOARD_INFO, COMPANY_SOURCES, board_has_key, ensure_description


@asynccontextmanager
async def lifespan(app: FastAPI):
    for person in people.load():
        with people.acting_as(person.id):
            db.backfill()
            taste.recompute()
    tasks.start()
    auto.start()
    yield


app = FastAPI(title="CVMaker", lifespan=lifespan)


@app.middleware("http")
async def act_as_selected_person(request: Request, call_next):
    """Everything a request does - and the background tasks it starts - works on the selected person's data."""
    token = people.use(request.cookies.get(people.COOKIE, people.DEFAULT))
    try:
        return await call_next(request)
    finally:
        people.reset(token)
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
templates.env.globals["STAGES"] = db.STAGES
templates.env.globals["STAGE_LABELS"] = db.STAGE_LABELS
templates.env.globals["FOLLOW_UP_DAYS"] = settings.FOLLOW_UP_DAYS


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
            **ctx,
        },
    )


def _not_found(what: str) -> str:
    others = len(people.load()) > 1
    return f"{what} not found" + (f" for {people.current_person().name} - it may belong to someone else; switch person in the top bar." if others else "")


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LLMError)
async def llm_error(request: Request, exc: LLMError):
    return page(request, "error.html", message=str(exc))


SW_KILL_SWITCH = """self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil((async () => {
  await self.registration.unregister();
  for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
})()));
"""


@app.get("/sw.js")
def service_worker_kill_switch():
    """Another app once served on this address and left a service worker that shows its cached pages.
    Browsers re-check /sw.js on each visit; this replacement unregisters itself and reloads the page."""
    return Response(SW_KILL_SWITCH, media_type="application/javascript", headers={"Cache-Control": "no-store"})


# --- background tasks API (polled by every page) ----------------------------------------------------


def _task_json(t: dict) -> dict:
    keys = ("id", "kind", "label", "subject", "job_id", "url", "status", "progress", "error", "created", "started", "finished")
    return {k: t[k] for k in keys}


@app.get("/api/tasks")
def api_tasks():
    return {
        "now": time.time(),
        "active": [_task_json(t) for t in db.task_active()],
        "finished": [_task_json(t) for t in db.tasks_unseen_finished()],
        "unread": db.notifications_unread(),
    }


@app.post("/api/tasks/{task_id}/seen")
def api_task_seen(task_id: int):
    db.task_update(task_id, seen=1)
    return {"ok": True}


def _job_label(verb: str, job: Job) -> str:
    return f"{verb} · {job.title} @ {job.company}"


def _need_profile() -> Profile:
    profile = assistant.load_profile()
    if not profile:
        raise LLMError("Set up your profile first (My profile → Import my CV).")
    return profile


# --- jobs list & searches ----------------------------------------------------------------------------

ROLE_SUGGESTIONS = ["developer", "engineer", "programmer", "software", "backend", "frontend", "full stack", "devops", "qa"]
EXCLUDE_SUGGESTIONS = ["sales", "manager", "director", "intern", "lead", "principal", "support"]
REGION_SUGGESTIONS = ["worldwide", "anywhere", "europe", "emea", "eu", "bulgaria", "cet"]


def _suggestions() -> dict:
    profile = assistant.load_profile()
    skills = sorted({i for g in profile.skills for i in g.items if len(i) < 30}, key=str.lower) if profile else []
    codes = sorted(set(COUNTRY_TERMS) | {c for c in load_companies() if c != "REMOTE"})
    return {
        "countries": [(c, country_name(c)) for c in codes],
        "skills": skills,
        "roles": ROLE_SUGGESTIONS,
        "exclude": EXCLUDE_SUGGESTIONS,
        "regions": REGION_SUGGESTIONS,
    }


def _list_rows(
    search: SavedSearch, status: str, only_last: int, unseen: int, salary: int, sort: str, direction: str = ""
) -> list[dict]:
    ids = search.last_job_ids if only_last else None
    closed = status == "closed"
    return db.list_jobs(
        ids=ids, status=None if closed else status or None, unseen=bool(unseen), with_salary=bool(salary), sort=sort,
        direction=direction, closed=True if closed else False,
    )


def _filter_rows(rows: list[dict], q: str, where: str) -> list[dict]:
    """Narrow the job list by words (all must appear in the title, company, location or tags) and by work mode."""
    words = q.lower().split()
    out = []
    for r in rows:
        j = r["job"]
        hybrid = "hybrid" in [t.lower() for t in j.tags]
        if where == "remote" and not j.remote or where == "hybrid" and not hybrid or where == "onsite" and (j.remote or hybrid):
            continue
        text = " ".join([j.title, j.company, j.location, " ".join(j.tags), j.source]).lower()
        if all(w in text for w in words):
            out.append(r)
    return out


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    search: str = "",
    status: str = "",
    only_last: int = 0,
    role: str = "",
    unseen: int = 0,
    salary: int = 0,
    sort: str = "score",
    dir: str = "",
    new: int = 0,
    q: str = "",
    where: str = "",
):
    prefs = auto.load_prefs()
    if search and auto.get_search(search) and prefs.active_search != search:
        prefs.active_search = search
        auto.save_prefs(prefs)
    current = SavedSearch(id="", name="New search") if new else auto.active_search()
    criteria = current.criteria
    chosen = roles.get_role(role) if role else None
    if chosen:
        criteria.role_keywords = chosen.role_keywords or criteria.role_keywords
        criteria.skill_keywords = chosen.skill_keywords or criteria.skill_keywords
    searches = auto.load_searches()
    learned = taste.summary(assistant.load_profile())
    return page(
        request,
        "index.html",
        learned=learned,
        hide_reasons=taste.REASONS,
        blocked=taste.blocked_companies(),
        feedback_tips=taste.suggestions(current.criteria, learned["model"]) if current.id else [],
        current=current,
        criteria=criteria,
        searches=searches,
        running={s.id: auto.search_running(s.id) for s in searches},
        next_runs={s.id: _until(auto.next_run(s)) for s in searches if s.auto},
        prefs=prefs,
        rating_tasks=db.task_active("rate"),
        pack_tasks=db.task_active("pack"),
        packs=pack.recent_packs(8),
        roles=roles.load_roles(),
        chosen_role=chosen,
        rows=_filter_rows(_list_rows(current, status, only_last, unseen, salary, sort, dir), q, where),
        q=q,
        where=where,
        settings_open=bool(new or role),
        companies_seen=db.company_summaries(),
        stats=db.stats(),
        status=status,
        only_last=only_last,
        unseen=unseen,
        salary=salary,
        sort=sort,
        dir=db.sort_direction(sort, dir),
        company_sources=list(COMPANY_SOURCES),
        boards=[(name, info["label"], board_has_key(name), info["key"]) for name, info in BOARD_INFO.items()],
        seniority_levels=SENIORITY_LEVELS,
        suggest=_suggestions(),
        closed_total=db.closed_count(),
        check_tasks=db.task_active("check"),
    )


def _query(**params) -> str:
    """The current list's query string with some parameters changed (for tabs and sort links)."""
    return "?" + "&".join(f"{k}={v}" for k, v in params.items() if v not in ("", 0, None))


templates.env.globals["query"] = _query


@app.post("/search")
def search(
    search_id: str = Form(""),
    name: str = Form(""),
    action: str = Form("run"),
    role_keywords: str = Form(""),
    skill_keywords: str = Form(""),
    exclude: str = Form(""),
    countries: str = Form(""),
    include_remote: bool = Form(False),
    remote_regions: str = Form(""),
    sources: list[str] = Form([]),
    max_age_days: int = Form(30),
    remote_only: bool = Form(False),
    seniority: list[str] = Form([]),
    must_mention: str = Form(""),
    salary_only: bool = Form(False),
    min_score: int = Form(0),
    auto_run: bool = Form(False),
    every_hours: float = Form(6),
    notify: bool = Form(False),
    where: str = Form(""),
):
    """Save the form as a saved search (a new one with action 'new'), then run it in the background unless 'save'."""
    if where:  # the "Remote jobs" choice: not wanted / also include / remote only
        include_remote, remote_only = where in ("both", "remote"), where == "remote"
    criteria = Criteria(
        role_keywords=_split(role_keywords),
        skill_keywords=_split(skill_keywords),
        exclude=_split(exclude),
        countries=[c.upper() for c in _split(countries)],
        include_remote=include_remote or remote_only,
        remote_regions=_split(remote_regions) or Criteria().remote_regions,
        sources=sources,
        max_age_days=max_age_days,
        remote_only=remote_only,
        seniority=seniority,
        must_mention=_split(must_mention),
        salary_only=salary_only,
        min_score=min_score,
    )
    existing = auto.get_search(search_id) if search_id and action != "new" else None
    saved = (existing or SavedSearch(id="", name="")).model_copy(
        update={
            "name": name.strip() or (existing.name if existing else "New search"),
            "criteria": criteria,
            "auto": auto_run,
            "every_hours": max(0.5, every_hours),
            "notify": notify,
        }
    )
    saved = auto.save_search(saved)
    prefs = auto.load_prefs()
    prefs.active_search = saved.id
    auto.save_prefs(prefs)
    if action != "save":
        auto.submit_search(saved.id)
    return _back(f"/?search={saved.id}")


@app.post("/searches/{search_id}/run")
def search_run(search_id: str):
    if auto.get_search(search_id):
        auto.submit_search(search_id)
    return _back(f"/?search={search_id}")


@app.post("/searches/{search_id}/delete")
def search_delete(search_id: str):
    auto.delete_search(search_id)
    prefs = auto.load_prefs()
    prefs.active_search = auto.load_searches()[0].id
    auto.save_prefs(prefs)
    return _back("/")


@app.post("/prefs")
def prefs_save(
    rate_after_search: bool = Form(False),
    rate_top: int = Form(40),
    pack_after_search: bool = Form(False),
    pack_min_score: int = Form(80),
    pack_max: int = Form(3),
    check_open: bool = Form(False),
):
    prefs = auto.load_prefs()
    prefs.check_open = check_open
    prefs.rate_after_search, prefs.rate_top = rate_after_search, max(1, min(500, rate_top))
    prefs.pack_after_search = pack_after_search
    prefs.pack_min_score, prefs.pack_max = max(0, min(100, pack_min_score)), max(1, min(20, pack_max))
    auto.save_prefs(prefs)
    return _back("/#auto")


@app.post("/rate")
def rate_view(
    status: str = Form(""), only_last: int = Form(0), unseen: int = Form(0), salary: int = Form(0), back: str = Form("/")
):
    """Rate the not-yet-rated jobs in the list you are looking at (highest keyword score first)."""
    rows = _list_rows(auto.active_search(), status, only_last, unseen, salary, "score")
    auto.submit_rating(db.unrated([r["job"].id for r in rows])[: auto.load_prefs().rate_top], "Rate jobs in this list")
    return _back(back)


@app.post("/seen")
def mark_all_seen(back: str = Form("/")):
    db.mark_seen()
    return _back(back)


@app.post("/feedback/apply")
def feedback_apply(kind: str = Form(...), value: str = Form(""), suggestion_id: str = Form("")):
    """Apply a filter suggestion learned from your feedback to the search you're using."""
    search = auto.active_search()
    search.criteria = taste.apply_suggestion(search.criteria, kind, value)
    auto.save_search(search)
    taste.dismiss(suggestion_id)
    return _back(f"/?search={search.id}#feedback")


@app.post("/feedback/dismiss")
def feedback_dismiss(suggestion_id: str = Form(...)):
    taste.dismiss(suggestion_id)
    return _back("/#feedback")


@app.post("/feedback/unblock")
def feedback_unblock(company: str = Form(...)):
    taste.unblock_company(company)
    db.unhide_company(company)
    taste.recompute()
    return _back("/#feedback")


# --- export ---------------------------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "Title", "Company", "Location", "Remote", "Salary", "≈ EUR/month", "Keyword score", "Fit (1-10)", "Stage",
    "Stage since", "Next date", "Contact", "Notes", "Posted", "Found", "Source", "URL",
]


def _export_rows(rows: list[dict]) -> list[list]:
    def day(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""

    return [
        [
            r["job"].title, r["job"].company, r["job"].location, "yes" if r["job"].remote else "", r["job"].salary,
            r["salary_month"] or "", r["score"], r["fit"] or "", db.STAGE_LABELS.get(r["status"], r["status"]),
            day(r["status_at"]), r["next_date"], r["contact"], r["notes"], r["job"].posted_at[:10], day(r["first_seen"]),
            r["job"].source, r["job"].url,
        ]
        for r in rows
    ]


@app.get("/export.{fmt}")
def export(
    fmt: str, status: str = "", only_last: int = 0, unseen: int = 0, salary: int = 0, sort: str = "score", dir: str = "",
    pipeline: int = 0,
):
    rows = (
        db.list_jobs(statuses=db.STAGES, sort="newest", closed=None)
        if pipeline
        else _list_rows(auto.active_search(), status, only_last, unseen, salary, sort, dir)
    )
    name = f"cvmaker_{'pipeline' if pipeline else 'jobs'}_{datetime.now():%Y-%m-%d}"
    data = _export_rows(rows)
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Pipeline" if pipeline else "Jobs"
        ws.append(EXPORT_COLUMNS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in data:
            ws.append(row)
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEFGHIJKLMNOPQ", [40, 24, 24, 8, 22, 12, 8, 8, 12, 12, 12, 24, 40, 12, 12, 14, 50]):
            ws.column_dimensions[col].width = width
        buf = io.BytesIO()
        wb.save(buf)
        return Response(
            buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(data)
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


# --- application pipeline --------------------------------------------------------------------------------


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request):
    rows = db.list_jobs(statuses=db.STAGES, sort="newest", closed=None)
    columns = {s: [] for s in db.STAGES}
    for r in sorted(rows, key=lambda r: r["status_at"], reverse=True):
        columns[r["status"]].append(r)
    return page(request, "pipeline.html", columns=columns, due=db.due_follow_ups(), stats=db.stats())


@app.get("/insights", response_class=HTMLResponse)
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


@app.post("/insights/analyse")
def insights_analyse():
    profile = _need_profile()
    tasks.submit("gaps", "Gap analysis across your jobs", lambda progress: insights.run_analysis(profile, progress),
                 subject="gaps", url="/insights#gaps")
    return _back("/insights#gaps")


@app.post("/job/{job_id:path}/status")
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


@app.post("/job/{job_id:path}/application")
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


@app.post("/job/{job_id:path}/followed-up")
def job_followed_up(job_id: str, days: float = Form(7), back: str = Form("/pipeline")):
    db.snooze_follow_up(job_id, days)
    return _back(back)


# --- job page --------------------------------------------------------------------------------------------


@app.get("/job/{job_id:path}", response_class=HTMLResponse)
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


@app.post("/job/{job_id:path}/fit")
def job_fit(job_id: str):
    profile = _need_profile()
    job, _ = db.get_job(job_id)

    def run(progress):
        pack.fit_check(profile, ensure_description(job), progress)
        return f"/job/{job_id}#fit"

    tasks.submit("fit", _job_label("Fit check", job), run, subject=f"fit:{job_id}", job_id=job_id, url=f"/job/{job_id}#fit")
    return _back(f"/job/{job_id}#fit")


@app.post("/job/{job_id:path}/pack")
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


@app.post("/job/{job_id:path}/github")
def job_github(job_id: str, refresh: int = Form(0)):
    profile = _need_profile()
    job, _ = db.get_job(job_id)

    def run(progress):
        progress.set(step="reading your repos")
        try:
            user = github.username(profile)
            repos = _refetch(user) if refresh else github.fetch_repos(user)
        except github.GithubError as e:
            raise LLMError(str(e)) from e
        progress.set(step="Claude is comparing them with the job")
        pack.github_check(profile, ensure_description(job), repos)
        return f"/job/{job_id}#github"

    tasks.submit("github", _job_label("GitHub check", job), run, subject=f"github:{job_id}", job_id=job_id, url=f"/job/{job_id}#github")
    return _back(f"/job/{job_id}#github")


def _refetch(user: str) -> list[dict]:
    db.cache_delete(f"github:{user}")
    return github.fetch_repos(user)


@app.post("/job/{job_id:path}/github/add")
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


# --- CVs -----------------------------------------------------------------------------------------------


def _layout(one_page: bool, font: str, instructions: str = "") -> dict:
    try:
        font_pt = float(font) if font and font != "auto" else None
    except ValueError:
        font_pt = None
    return {"font_pt": font_pt, "one_page": one_page, "instructions": instructions.strip()}


@app.post("/job/{job_id:path}/tailor")
def job_tailor(
    job_id: str, instructions: str = Form(""), role_id: str = Form(""), one_page: bool = Form(False), font: str = Form("auto")
):
    profile = _need_profile()
    job, _ = db.get_job(job_id)
    role = roles.get_role(role_id) if role_id else None
    layout = _layout(one_page, font, instructions)

    def run(progress):
        return f"/cv/{pack.tailored_cv(profile, ensure_description(job), role, layout, progress)}"

    tasks.submit("tailor", _job_label("Tailored CV", job), run, subject=f"tailor:{job_id}", job_id=job_id, url=f"/job/{job_id}#docs")
    return _back(f"/job/{job_id}#docs")


def _owner(owner_id: str) -> tuple[Job | None, RoleProfile | None]:
    if owner_id.startswith("role:"):
        return None, roles.get_role(owner_id[5:])
    found = db.get_job(owner_id)
    return (found[0] if found else None), None


def refresh_cv_pdf(cv_id: int) -> None:
    """Re-render a CV's PDF after it was edited in the CV editor (the editor saves text, not PDFs)."""
    layout, _ = db.cv_layout(cv_id)
    if not layout.get("pdf_stale"):
        return
    _, tailored, pdf_path = db.get_cv(cv_id)
    _, pages, used = render.cv_pdf(tailored.cv, Path(pdf_path), layout.get("font_pt"), layout)
    db.update_cv(cv_id, layout={**layout, "font_used": used, "pdf_stale": False}, pages=pages)


@app.get("/cv/{cv_id}", response_class=HTMLResponse)
def cv_page(request: Request, cv_id: int, saved: int = 0):
    found = db.get_cv(cv_id)
    if not found:
        return page(request, "error.html", message=_not_found("CV"))
    refresh_cv_pdf(cv_id)
    found = db.get_cv(cv_id)
    owner_id, tailored, pdf_path = found
    job, role = _owner(owner_id)
    ratings = {}
    others = [c for c in db.cvs_for_job(owner_id) if c["id"] != cv_id]
    for baseline in ["profile"] + [str(c["id"]) for c in others]:
        cached = db.cache_get(f"cvrate:{cv_id}:{baseline}", 24 * 365)
        if cached:
            try:
                cached["comparison"] = CVComparison.model_validate(cached["comparison"])
                ratings[baseline] = cached
            except ValidationError:
                pass
    layout, pages = db.cv_layout(cv_id)
    if not pages and Path(pdf_path).exists():  # CVs made before page counts were stored
        pages = render.page_count(Path(pdf_path))
        db.update_cv(cv_id, pages=pages)
    profile = assistant.load_profile()
    app_info = db.get_application(owner_id) if job else {}
    return page(
        request,
        "cv_result.html",
        cv_id=cv_id,
        job=job,
        role=role,
        t=tailored,
        pdf_name=Path(pdf_path).name,
        others=others,
        ratings=ratings,
        letters=db.letters_for_job(owner_id) if job else [],
        layout=layout,
        pages=pages,
        font_choices=render.font_choices(),
        diff=cvdiff.diff(profile, tailored.cv, job) if profile else None,
        skills_text="\n".join(f"{g.category}: {', '.join(g.items)}" for g in tailored.cv.skills),
        cv_json=tailored.cv.model_dump_json(indent=2),
        active={t["kind"]: t for t in db.task_active() if t["subject"].endswith(f":cv:{cv_id}")},
        is_sent=bool(app_info) and app_info.get("sent_cv_id") == cv_id,
        saved=saved,
    )


def _lines(text: str) -> list[str]:
    return [line.strip().lstrip("•-* ").strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]


@app.post("/cv/{cv_id}/edit")
async def cv_edit(request: Request, cv_id: int):
    """Save hand edits (form fields, or the whole CV as JSON) and re-render the PDF."""
    owner_id, tailored, pdf_path = db.get_cv(cv_id)
    form = await request.form()
    if form.get("mode") == "json":
        try:
            tailored.cv = CVContent.model_validate(json.loads(str(form.get("cv_json", ""))))
        except (json.JSONDecodeError, ValidationError) as e:
            return page(request, "error.html", message=f"The CV JSON is not valid: {str(e)[:400]}")
    else:
        cv = tailored.cv
        cv.headline = str(form.get("headline", cv.headline)).strip()
        cv.summary = " ".join(str(form.get("summary", cv.summary)).split())
        for i, role in enumerate(cv.experience):
            role.bullets = _lines(str(form.get(f"exp_{i}", "\n".join(role.bullets))))
        for i, project in enumerate(cv.projects):
            project.bullets = _lines(str(form.get(f"proj_{i}", "\n".join(project.bullets))))
        groups = []
        for line in _lines(str(form.get("skills", ""))):
            category, _, items = line.partition(":")
            groups.append(SkillGroup(category=category.strip(), items=_split(items)) if items else SkillGroup(category="", items=_split(category)))
        cv.skills = groups
        cv.soft_skills = _lines(str(form.get("soft_skills", "")))
        cv.languages = _lines(str(form.get("languages", "")))
        cv.interests = _lines(str(form.get("interests", "")))
    layout, _ = db.cv_layout(cv_id)
    _, pages, used = render.cv_pdf(tailored.cv, Path(pdf_path), layout.get("font_pt"), layout)
    db.update_cv(cv_id, tailored, {**layout, "font_used": used, "pdf_stale": False}, pages)
    db.cache_delete(f"cvrate:{cv_id}:profile")
    return _back(f"/cv/{cv_id}?saved=1#edit")


@app.post("/cv/{cv_id}/layout")
def cv_layout(cv_id: int, font: str = Form("auto"), one_page: bool = Form(False)):
    """Change the font size / one-page setting. If it no longer fits on one page, Claude shortens it in the background."""
    owner_id, tailored, pdf_path = db.get_cv(cv_id)
    old, _ = db.cv_layout(cv_id)
    layout = _layout(one_page, font, old.get("instructions", ""))
    _, pages, used = render.cv_pdf(tailored.cv, Path(pdf_path), layout["font_pt"], {**old, **layout})
    db.update_cv(cv_id, layout={**old, **layout, "font_used": used, "pdf_stale": False}, pages=pages)
    if one_page and pages > 1:
        _submit_shorten(cv_id)
    return _back(f"/cv/{cv_id}#layout")


def _submit_shorten(cv_id: int) -> None:
    owner_id, _, _ = db.get_cv(cv_id)
    job, role = _owner(owner_id)

    def run(progress):
        _, tailored, pdf_path = db.get_cv(cv_id)
        layout, _ = db.cv_layout(cv_id)
        pages, used = pack.render_fitting(tailored, Path(pdf_path), {**layout, "one_page": True}, job and ensure_description(job), progress)
        db.update_cv(cv_id, tailored, {**layout, "font_used": used}, pages)
        db.cache_delete(f"cvrate:{cv_id}:profile")
        return f"/cv/{cv_id}"

    label = f"Fit CV #{cv_id} on one page" + (f" · {job.title} @ {job.company}" if job else "")
    tasks.submit("onepage", label, run, subject=f"onepage:cv:{cv_id}", job_id=job.id if job else "", url=f"/cv/{cv_id}")


@app.post("/cv/{cv_id}/one-page")
def cv_one_page(cv_id: int):
    layout, _ = db.cv_layout(cv_id)
    db.update_cv(cv_id, layout={**layout, "one_page": True})
    _submit_shorten(cv_id)
    return _back(f"/cv/{cv_id}#layout")


@app.post("/cv/{cv_id}/sent")
def cv_sent(cv_id: int):
    """Record this CV as the one sent with the application (and mark the job applied if it was not yet)."""
    owner_id, _, _ = db.get_cv(cv_id)
    found = db.get_job(owner_id)
    if found:
        db.save_application(owner_id, sent_cv_id=cv_id)
        if found[1] in ("new", "liked"):
            db.set_status(owner_id, "applied", note=f"Sent CV #{cv_id}")
    return _back(f"/cv/{cv_id}")


def _baseline(owner_id: str, baseline: str, profile: Profile) -> tuple[CVContent, Path, str]:
    """The CV to compare against: the original profile, or another CV generated for the same job."""
    if baseline != "profile":
        found = db.get_cv(int(baseline))
        if found and found[0] == owner_id:
            return found[1].cv, Path(found[2]), f"CV #{baseline}"
    original = CVContent.model_validate(profile.model_dump(include=set(CVContent.model_fields)))
    pdf, _, _ = render.cv_pdf(original, settings.OUTPUT_DIR / "_original_profile.pdf")
    return original, pdf, "Original (profile)"


@app.post("/cv/{cv_id}/rate")
def cv_rate(cv_id: int, baseline: str = Form("profile")):
    profile = _need_profile()
    found = db.get_cv(cv_id)
    if not found:
        return _back("/")
    owner_id, _, _ = found
    job, role = _owner(owner_id)

    def run(progress):
        refresh_cv_pdf(cv_id)
        _, tailored, pdf_path = db.get_cv(cv_id)
        full = ensure_description(job) if job else None
        extra = (role.key_skills + role.skill_keywords) if role else load_criteria().skill_keywords
        progress.set(step="measuring keywords and the PDF text")
        base_cv, base_pdf, label = _baseline(owner_id, baseline, profile)
        keywords = cvcheck.job_keywords(full, profile, extra)
        base_cov = cvcheck.coverage(keywords, cvcheck.cv_text(base_cv))
        new_cov = cvcheck.coverage(keywords, cvcheck.cv_text(tailored.cv))
        report = "\n".join(
            f"- {k}: baseline {'yes' if base_cov[k] else 'no'}, tailored {'yes' if new_cov[k] else 'no'}" for k in keywords
        ) or "(no keywords found)"
        progress.set(step="Claude is scoring both versions")
        comparison = assistant.compare_cvs(base_cv, tailored.cv, full, role, report)
        db.cache_put(
            f"cvrate:{cv_id}:{baseline}",
            {
                "label": label,
                "rated_at": time.time(),
                "comparison": comparison.model_dump(),
                "keywords": [[k, base_cov[k], new_cov[k]] for k in keywords],
                "ats_baseline": cvcheck.ats_checks(base_cv, base_pdf, keywords),
                "ats_tailored": cvcheck.ats_checks(tailored.cv, pdf_path, keywords),
            },
        )
        return f"/cv/{cv_id}?vs={baseline}#rating"

    target = f"{job.title} @ {job.company}" if job else (role.name if role else "")
    tasks.submit("cvrate", f"Rate CV #{cv_id} · {target}", run, subject=f"rate:{baseline}:cv:{cv_id}", job_id=job.id if job else "", url=f"/cv/{cv_id}?vs={baseline}#rating")
    return _back(f"/cv/{cv_id}#rating")


@app.get("/cv/{cv_id}/preview", response_class=HTMLResponse)
def cv_preview(cv_id: int):
    _, tailored, _ = db.get_cv(cv_id)
    layout, _ = db.cv_layout(cv_id)
    return HTMLResponse(render.cv_html(tailored.cv, layout.get("font_pt") or layout.get("font_used"), layout))


@app.get("/cv/{cv_id}/pdf")
def cv_pdf(cv_id: int):
    refresh_cv_pdf(cv_id)
    _, _, pdf_path = db.get_cv(cv_id)
    return FileResponse(pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)


# --- profile -------------------------------------------------------------------------------------------


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, error: str = "", backup_error: str = "", restored: int = 0):
    from . import backup, history

    profile = assistant.load_profile()
    return page(
        request,
        "profile.html",
        profile=profile,
        profile_json=profile.model_dump_json(indent=2) if profile else "",
        has_photo=settings.PHOTO_PATH.exists(),
        error=error,
        activity=db.tasks_recent(60),
        documents=db.all_documents(),
        importing=db.task_active("import"),
        editor_versions=[v["name"] for v in editor.load_layout()["variants"]] if editor.exists() else [],
        can_import=editor.CVEDITOR_DATA.exists(),
        imported_at=editor.load_layout().get("imported_at") if editor.exists() else None,
        cveditor_path=str(editor.CVEDITOR_DATA).replace(str(Path.home()), "~"),
        import_report=db.cache_get("editor:import-report", 24 * 30),
        history_items=[{**h, **history.summary(h["id"])} for h in history.list_snapshots()[:12]],
        history_total=len(history.list_snapshots()),
        backups=backup.list_backups(),
        backup_error=backup_error,
        backup_report=db.cache_get("backup:report", 1),
        restored=restored,
    )


@app.get("/profile/preview", response_class=HTMLResponse)
def profile_preview():
    profile = assistant.load_profile()
    return HTMLResponse(render.cv_html(profile) if profile else "No profile yet")


@app.post("/profile/import")
async def profile_import(cv: UploadFile = File(...)):
    data = await cv.read()

    def run(progress):
        progress.set(step="Claude is reading your CV")
        assistant.save_profile(assistant.import_cv(data))
        return "/profile"

    tasks.submit("import", f"Import CV · {cv.filename}", run, subject="import", url="/profile")
    return _back("/profile")


@app.post("/profile/photo")
async def profile_photo(photo: UploadFile = File(...)):
    settings.PHOTO_PATH.write_bytes(await photo.read())
    editor.use_profile_photo()
    return _back("/profile")


@app.post("/profile/save")
def profile_save(profile_json: str = Form(...)):
    try:
        profile = Profile.model_validate(json.loads(profile_json))
    except (json.JSONDecodeError, ValidationError) as e:
        return _back(f"/profile?error={str(e)[:300]}")
    assistant.save_profile(profile)
    return _back("/profile")


# --- interview -------------------------------------------------------------------------------------------

QUESTIONS_MAX_AGE_HOURS = 24 * 7


def _questions_key(job_id: str) -> str:
    return f"iq:{job_id or 'general'}"


@app.get("/interview", response_class=HTMLResponse)
def interview_page(request: Request, job_id: str = ""):
    if not assistant.load_profile():
        return _back("/profile")
    job = db.get_job(job_id)[0] if job_id and db.get_job(job_id) else None
    cached = db.cache_get(_questions_key(job_id), QUESTIONS_MAX_AGE_HOURS)
    questions = InterviewQuestions.model_validate(cached).questions if cached else None
    subject = f"questions:{job_id or 'general'}"
    return page(
        request,
        "interview.html",
        questions=questions,
        job=job,
        job_id=job_id,
        generating=(db.task_active("questions", subject) or [None])[0],
        merging=(db.task_active("answers", "answers") or [None])[0],
    )


@app.post("/interview/questions")
def interview_questions(job_id: str = Form("")):
    profile = _need_profile()
    job = db.get_job(job_id)[0] if job_id else None
    url = f"/interview?job_id={job_id}" if job_id else "/interview"

    def run(progress):
        progress.set(step="Claude is finding the gaps in your profile")
        questions = assistant.interview_questions(profile, ensure_description(job) if job else None)
        db.cache_put(_questions_key(job_id), questions.model_dump())
        return url

    label = _job_label("Interview questions", job) if job else "Interview questions · general"
    tasks.submit("questions", label, run, subject=f"questions:{job_id or 'general'}", job_id=job_id, url=url)
    return _back(url)


@app.post("/interview")
async def interview_submit(request: Request):
    form = await request.form()
    job_id = str(form.get("job_id", ""))
    qa = []
    for key in form:
        if key.startswith("q_"):
            qid = key[2:]
            qa.append((str(form[key]), str(form.get(f"a_{qid}", ""))))
    if not any(a.strip() for _, a in qa):
        return _back(f"/job/{job_id}" if job_id else "/profile")
    url = f"/job/{job_id}" if job_id else "/profile"

    def run(progress):
        progress.set(step="Claude is adding your answers to your profile")
        assistant.save_profile(assistant.merge_answers(assistant.load_profile(), qa))
        db.cache_delete(_questions_key(job_id))
        return url

    tasks.submit("answers", "Add interview answers to my profile", run, subject="answers", job_id=job_id, url="/profile")
    return _back(url)


# --- role profiles -----------------------------------------------------------------------------------------


@app.get("/roles", response_class=HTMLResponse)
def roles_page(request: Request, edit: str = ""):
    all_roles = roles.load_roles()
    editing = roles.get_role(edit) if edit else None
    cvs = {r.id: db.cvs_for_job(f"role:{r.id}") for r in all_roles}
    active = {t["subject"][len("tailor:role:"):]: t for t in db.task_active("tailor") if t["subject"].startswith("tailor:role:")}
    return page(
        request, "roles.html", roles=all_roles, editing=editing or RoleProfile(id="", name=""), cvs=cvs, active=active,
        font_choices=render.font_choices(),
    )


@app.post("/roles/save")
def roles_save(
    id: str = Form(""),
    name: str = Form(...),
    headline: str = Form(""),
    focus: str = Form(""),
    key_skills: str = Form(""),
    role_keywords: str = Form(""),
    skill_keywords: str = Form(""),
):
    roles.upsert_role(
        RoleProfile(
            id=id,
            name=name.strip(),
            headline=headline.strip(),
            focus=focus.strip(),
            key_skills=_split(key_skills),
            role_keywords=_split(role_keywords),
            skill_keywords=_split(skill_keywords),
        )
    )
    return _back("/roles")


@app.post("/roles/{role_id}/delete")
def roles_delete(role_id: str):
    roles.delete_role(role_id)
    return _back("/roles")


@app.post("/roles/{role_id}/cv")
def roles_cv(role_id: str, instructions: str = Form(""), one_page: bool = Form(False), font: str = Form("auto")):
    profile = _need_profile()
    role = roles.get_role(role_id)
    if not role:
        return _back("/roles")
    layout = _layout(one_page, font, instructions)

    def run(progress):
        progress.set(step="Claude is writing your CV")
        tailored = assistant.tailor(profile, None, role, instructions)
        cv_id = pack.save_cv(profile, f"role:{role_id}", tailored, role.name, layout, None, progress)
        return f"/cv/{cv_id}"

    tasks.submit("tailor", f"General CV · {role.name}", run, subject=f"tailor:role:{role_id}", url="/roles")
    return _back("/roles")


# --- cover letters -------------------------------------------------------------------------------------------


@app.post("/job/{job_id:path}/letter")
def job_letter(
    job_id: str,
    cv_id: str = Form(""),
    language: str = Form("English"),
    tone: str = Form("professional"),
    length: str = Form("standard"),
    instructions: str = Form(""),
):
    profile = _need_profile()
    job, _ = db.get_job(job_id)
    found = db.get_cv(int(cv_id)) if cv_id else None
    cv = found[1].cv if found and found[0] == job_id else None

    def run(progress):
        letter_id = pack.letter(profile, ensure_description(job), cv, language, tone, length, instructions, progress)
        return f"/letter/{letter_id}"

    tasks.submit("letter", _job_label("Cover letter", job), run, subject=f"letter:{job_id}", job_id=job_id, url=f"/job/{job_id}#letter")
    return _back(f"/job/{job_id}#letter")


@app.get("/letter/{letter_id}", response_class=HTMLResponse)
def letter_page(request: Request, letter_id: int, saved: int = 0):
    found = db.get_letter(letter_id)
    if not found:
        return page(request, "error.html", message=_not_found("Cover letter"))
    job, _ = db.get_job(found["job_id"])
    return page(
        request,
        "letter_result.html",
        l=found,
        job=job,
        saved=saved,
        others=[x for x in db.letters_for_job(found["job_id"]) if x["id"] != letter_id],
        pdf_name=Path(found["pdf_path"]).name,
        is_sent=db.get_application(found["job_id"]).get("sent_letter_id") == letter_id,
        skill_check=claims.unsupported(found["body"], assistant.load_profile(), job=job),
    )


@app.post("/letter/{letter_id}/save")
def letter_save(letter_id: int, body: str = Form(...)):
    found = db.get_letter(letter_id)
    profile = assistant.load_profile()
    if not found or not profile:
        return _back("/")
    job, _ = db.get_job(found["job_id"])
    db.update_letter_body(letter_id, body.replace("\r\n", "\n"))
    render.letter_pdf(body.replace("\r\n", "\n"), profile, job, Path(found["pdf_path"]))
    return _back(f"/letter/{letter_id}?saved=1")


@app.post("/letter/{letter_id}/sent")
def letter_sent(letter_id: int):
    found = db.get_letter(letter_id)
    if found:
        db.save_application(found["job_id"], sent_letter_id=letter_id)
    return _back(f"/letter/{letter_id}")


@app.get("/letter/{letter_id}/preview", response_class=HTMLResponse)
def letter_preview(letter_id: int):
    found = db.get_letter(letter_id)
    job, _ = db.get_job(found["job_id"])
    return HTMLResponse(render.letter_html(found["body"], assistant.load_profile(), job))


@app.get("/letter/{letter_id}/pdf")
def letter_pdf(letter_id: int):
    found = db.get_letter(letter_id)
    return FileResponse(found["pdf_path"], media_type="application/pdf", filename=Path(found["pdf_path"]).name)


# --- interview prep ----------------------------------------------------------------------------------------


def _cached_prep(job_id: str) -> InterviewPrep | None:
    cached = db.cache_get(f"prep:{job_id}", 24 * 365)
    try:
        return InterviewPrep.model_validate(cached) if cached else None
    except ValidationError:
        return None


@app.get("/prep/{job_id:path}", response_class=HTMLResponse)
def prep_page(request: Request, job_id: str):
    found = db.get_job(job_id)
    if not found:
        return page(request, "error.html", message=_not_found("Job"))
    return page(
        request, "prep.html", job=found[0], prep=_cached_prep(job_id), has_fit=_cached_fit(job_id) is not None,
        active=db.tasks_for_job(job_id),
    )


@app.post("/prep/{job_id:path}")
def prep_generate(job_id: str):
    profile = _need_profile()
    job, _ = db.get_job(job_id)

    def run(progress):
        pack.prep(profile, ensure_description(job), _cached_fit(job_id), progress)
        return f"/prep/{job_id}"

    tasks.submit("prep", _job_label("Interview prep", job), run, subject=f"prep:{job_id}", job_id=job_id, url=f"/prep/{job_id}")
    return _back(f"/prep/{job_id}")


# --- companies -----------------------------------------------------------------------------------------------


@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request):
    return page(
        request,
        "companies.html",
        groups=companies.load(),
        types=list(COMPANY_SOURCES),
        found=None,
        tested=None,
    )


@app.post("/companies/discover", response_class=HTMLResponse)
def companies_discover(request: Request, name: str = Form(...), url: str = Form(...), group: str = Form("BG")):
    error = ""
    found = postings = None
    try:
        found = companies.discover(url.strip(), name.strip())
        postings = companies.test(companies.entry(name.strip(), found))
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return page(
        request,
        "companies.html",
        groups=companies.load(),
        types=list(COMPANY_SOURCES),
        found=found,
        postings=postings,
        error=error,
        form={"name": name.strip(), "url": url.strip(), "group": group.strip().upper()},
        tested=None,
    )


@app.post("/companies/add")
def companies_add(
    name: str = Form(...),
    group: str = Form(...),
    type: str = Form(...),
    slug: str = Form(""),
    url: str = Form(""),
    render_js: bool = Form(False),
    default_location: str = Form(""),
):
    found = {"type": type, "slug": slug.strip(), "url": url.strip(), "render": render_js}
    companies.add(group.strip().upper() or "REMOTE", companies.entry(name.strip(), found, default_location.strip()))
    return _back("/companies")


@app.post("/companies/remove")
def companies_remove(group: str = Form(...), name: str = Form(...)):
    companies.remove(group, name)
    return _back("/companies")


@app.post("/companies/test", response_class=HTMLResponse)
def companies_test(request: Request, group: str = Form(...), name: str = Form(...)):
    company = next((c for c in companies.load().get(group, []) if c["name"] == name), None)
    tested = {"name": name, "error": "", "postings": []}
    try:
        tested["postings"] = companies.test(company)
    except Exception as e:
        tested["error"] = f"{type(e).__name__}: {e}"
    return page(request, "companies.html", groups=companies.load(), types=list(COMPANY_SOURCES), found=None, tested=tested)


# --- people ----------------------------------------------------------------------------------------------


def _as_person(response: RedirectResponse, person_id: str) -> RedirectResponse:
    response.set_cookie(people.COOKIE, person_id, max_age=10 * 365 * 24 * 3600, samesite="lax")
    return response


@app.get("/people", response_class=HTMLResponse)
def people_page(request: Request):
    rows = []
    for p in people.load():
        with people.acting_as(p.id):
            profile = assistant.load_profile()
            rows.append(
                {
                    "person": p,
                    "folder": str(people.data_dir(p.id)),
                    "headline": profile.headline if profile else "",
                    "totals": db.overview(),
                    "has_profile": profile is not None,
                }
            )
    return page(request, "people.html", rows=rows)


@app.post("/people/switch")
def people_switch(request: Request, person: str = Form(...), next_url: str = Form("")):
    if person == "__manage":
        return _back("/people")
    if not people.get(person):
        return _back("/people")
    target = next_url if next_url.startswith("/") and not next_url.startswith(("/job/", "/cv/", "/letter/", "/prep/")) else "/"
    return _as_person(_back(target), person)


@app.post("/people/add")
def people_add(name: str = Form(...)):
    person = people.add(name)
    return _as_person(_back("/profile"), person.id)


@app.post("/people/rename")
def people_rename(person: str = Form(...), name: str = Form(...)):
    people.rename(person, name)
    return _back("/people")


@app.post("/people/remove")
def people_remove(request: Request, person: str = Form(...)):
    if person == people.DEFAULT or not people.get(person):
        return _back("/people")
    with people.acting_as(person):
        busy = bool(db.task_active())
    if busy:
        return page(request, "error.html", message="That person still has background tasks running - wait for them to finish, then remove them.")
    people.remove(person)
    response = _back("/people")
    if request.cookies.get(people.COOKIE) == person:
        response = _as_person(response, people.DEFAULT)
    return response


# Adding jobs by link, posting checks, outreach, story bank, practice, debriefs and offers (stage_routes.py).
from .stage_routes import router as _stage_router  # noqa: E402  (it imports helpers from this module)

app.include_router(_stage_router)
from .chat_routes import router as _chat_router  # noqa: E402

app.include_router(_chat_router)
from .editor_routes import router as _editor_router  # noqa: E402

app.include_router(_editor_router)
from .extras_routes import router as _extras_router  # noqa: E402

app.include_router(_extras_router)
