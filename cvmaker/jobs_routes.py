"""The jobs list: saved searches, the filters, Claude rating of a view, feedback suggestions and export."""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import QueryParams

from . import assistant, auto, categories, db, jobfilter, pack, roles, taste
from .auto import SavedSearch
from .countries import COUNTRY_TERMS, country_name
from .models import (
    Criteria,
)
from .search import SENIORITY_LEVELS, load_companies
from .sources import BOARD_INFO, COMPANY_SOURCES, board_has_key
from .web import _back, _split, _until, page

router = APIRouter()


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


def _list_rows(search: SavedSearch, f: jobfilter.Filters) -> list[dict]:
    """The rows of the tab you are on (all, not seen, liked, hidden, closed), before the filters."""
    ids = search.last_job_ids if f.only_last else None
    closed = f.status == "closed"
    return db.list_jobs(
        ids=ids, status=None if closed else f.status or None, unseen=bool(f.unseen), with_salary=bool(f.salary),
        sort=f.sql_sort, direction=f.dir, closed=True if closed else False,
    )


def _view(search: SavedSearch, params) -> tuple[jobfilter.Filters, jobfilter.Result]:
    """The filtered job list an address (its query parameters) asks for."""
    f = jobfilter.Filters.from_params(params)
    return f, jobfilter.apply(_list_rows(search, f), f)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, search: str = "", role: str = "", new: int = 0):
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
    f, result = _view(current, request.query_params)
    stats = db.stats()
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
        f=f,
        result=result,
        rows=result.rows,
        jobs_total=sum(n for stage, n in stats["by_stage"].items() if stage != "hidden"),
        fopt=jobfilter,
        settings_open=bool(new or role),
        companies_seen=db.company_summaries(),
        stats=stats,
        company_sources=list(COMPANY_SOURCES),
        boards=[(name, info["label"], board_has_key(name), info["key"]) for name, info in BOARD_INFO.items()],
        seniority_levels=SENIORITY_LEVELS,
        job_categories=categories.CATEGORIES,
        suggest=_suggestions(),
        closed_total=db.closed_count(),
        check_tasks=db.task_active("check"),
    )


@router.get("/api/suggest")
def api_suggest(q: str = ""):
    """Quick search (top bar): the best matching saved jobs and companies while you type."""
    if len(q.strip()) < 2:
        return {"jobs": [], "companies": [], "total": 0}
    return jobfilter.suggest(db.list_jobs(sort="match"), q[:200])


@router.post("/search")
def search(
    search_id: str = Form(""),
    name: str = Form(""),
    action: str = Form("run"),
    categories_picked: list[str] = Form([], alias="categories"),
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
        categories=[c for c in categories_picked if c in categories.CATEGORIES],
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


@router.post("/searches/{search_id}/run")
def search_run(search_id: str):
    if auto.get_search(search_id):
        auto.submit_search(search_id)
    return _back(f"/?search={search_id}")


@router.post("/searches/{search_id}/delete")
def search_delete(search_id: str):
    auto.delete_search(search_id)
    prefs = auto.load_prefs()
    prefs.active_search = auto.load_searches()[0].id
    auto.save_prefs(prefs)
    return _back("/")


@router.post("/prefs")
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


@router.post("/rate")
def rate_view(query: str = Form(""), back: str = Form("/")):
    """Rate the not-yet-rated jobs in the list you are looking at (filters included), best keyword match first."""
    f = jobfilter.Filters.from_params(QueryParams(query))
    f.sort, f.dir, f.limit = "score", "", 5000
    rows = jobfilter.apply(_list_rows(auto.active_search(), f), f).rows
    auto.submit_rating(db.unrated([r["job"].id for r in rows])[: auto.load_prefs().rate_top], "Rate jobs in this list")
    return _back(back)


@router.post("/seen")
def mark_all_seen(back: str = Form("/")):
    db.mark_seen()
    return _back(back)


@router.post("/feedback/apply")
def feedback_apply(kind: str = Form(...), value: str = Form(""), suggestion_id: str = Form("")):
    """Apply a filter suggestion learned from your feedback to the search you're using."""
    search = auto.active_search()
    search.criteria = taste.apply_suggestion(search.criteria, kind, value)
    auto.save_search(search)
    taste.dismiss(suggestion_id)
    return _back(f"/?search={search.id}#feedback")


@router.post("/feedback/dismiss")
def feedback_dismiss(suggestion_id: str = Form(...)):
    taste.dismiss(suggestion_id)
    return _back("/#feedback")


@router.post("/feedback/unblock")
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


@router.get("/export.{fmt}")
def export(request: Request, fmt: str, pipeline: int = 0):
    """The pipeline, or the job list as you see it: same tab, filters and sort, every match (not just one page)."""
    if pipeline:
        rows = db.list_jobs(statuses=db.STAGES, sort="newest", closed=None)
    else:
        f = jobfilter.Filters.from_params(request.query_params)
        f.limit = 100000
        rows = jobfilter.apply(_list_rows(auto.active_search(), f), f).rows
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
