import json
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import assistant, db, github, render, roles, settings
from .llm import LLMError
from .models import Criteria, FitAssessment, GithubReview, Profile, Project, RoleProfile
from .search import load_companies, run_search
from .sources import BOARD_SOURCES, COMPANY_SOURCES, ensure_description

app = FastAPI(title="CVMaker")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["md_bold"] = render.env.filters["md_bold"]

CRITERIA_PATH = settings.DATA_DIR / "criteria.json"
last_search: dict = {}


def _split(value: str) -> list[str]:
    return [x.strip() for x in value.replace("\n", ",").split(",") if x.strip()]


def load_criteria() -> Criteria:
    if CRITERIA_PATH.exists():
        return Criteria.model_validate_json(CRITERIA_PATH.read_text())
    return Criteria()


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"profile_exists": settings.PROFILE_PATH.exists(), **ctx})


@app.exception_handler(LLMError)
async def llm_error(request: Request, exc: LLMError):
    return page(request, "error.html", message=str(exc))


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "", only_last: int = 0, role: str = ""):
    ids = last_search.get("job_ids") if only_last else None
    criteria = load_criteria()
    chosen = roles.get_role(role) if role else None
    if chosen:
        criteria.role_keywords = chosen.role_keywords or criteria.role_keywords
        criteria.skill_keywords = chosen.skill_keywords or criteria.skill_keywords
    return page(
        request,
        "index.html",
        criteria=criteria,
        roles=roles.load_roles(),
        chosen_role=chosen,
        rows=db.list_jobs(ids=ids, status=status or None),
        status=status,
        only_last=only_last,
        last=last_search,
        all_sources=list(COMPANY_SOURCES) + list(BOARD_SOURCES),
        countries=[c for c in load_companies() if c != "REMOTE"],
        rapidapi=bool(settings.RAPIDAPI_KEY),
    )


@app.post("/search")
def search(
    role_keywords: str = Form(""),
    skill_keywords: str = Form(""),
    exclude: str = Form(""),
    countries: str = Form(""),
    include_remote: bool = Form(False),
    remote_regions: str = Form(""),
    sources: list[str] = Form([]),
    max_age_days: int = Form(30),
):
    criteria = Criteria(
        role_keywords=_split(role_keywords),
        skill_keywords=_split(skill_keywords),
        exclude=_split(exclude),
        countries=[c.upper() for c in _split(countries)],
        include_remote=include_remote,
        remote_regions=_split(remote_regions) or Criteria().remote_regions,
        sources=sources,
        max_age_days=max_age_days,
    )
    CRITERIA_PATH.write_text(criteria.model_dump_json(indent=2))
    started = time.time()
    result = run_search(criteria, assistant.load_profile())
    last_search.clear()
    last_search.update(
        job_ids=result.job_ids,
        errors=result.errors,
        counts=result.counts,
        seconds=round(time.time() - started, 1),
    )
    return RedirectResponse("/?only_last=1", status_code=303)


@app.get("/job/{job_id:path}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    found = db.get_job(job_id)
    if not found:
        return page(request, "error.html", message="Job not found")
    job, status = found
    fetch_error = ""
    try:
        job = ensure_description(job)
    except Exception as e:
        fetch_error = f"Could not load the full posting: {e}"
    fit = None
    cached_fit = db.cache_get(f"fit2:{job_id}", 24 * 365)
    if cached_fit:
        try:
            fit = FitAssessment.model_validate(cached_fit)
        except ValidationError:
            fit = None
    return page(
        request,
        "job.html",
        job=job,
        status=status,
        fit=fit,
        gh=_cached_review(job_id),
        profile_links=_profile_project_links(),
        cvs=db.cvs_for_job(job_id),
        fetch_error=fetch_error,
        roles=roles.load_roles(),
    )


@app.post("/job/{job_id:path}/status")
def job_status(job_id: str, status: str = Form(...), back: str = Form("")):
    db.set_status(job_id, status)
    return RedirectResponse(back or f"/job/{job_id}", status_code=303)


@app.post("/job/{job_id:path}/fit")
def job_fit(request: Request, job_id: str):
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job, _ = db.get_job(job_id)
    fit = assistant.assess_fit(profile, ensure_description(job))
    db.cache_put(f"fit2:{job_id}", fit.model_dump())
    return RedirectResponse(f"/job/{job_id}", status_code=303)


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
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job, _ = db.get_job(job_id)
    try:
        user = github.username(profile)
        repos = _refetch(user) if refresh else github.fetch_repos(user)
    except github.GithubError as e:
        raise LLMError(str(e)) from e
    review = assistant.github_review(profile, ensure_description(job), github.repos_for_prompt(repos))
    db.cache_put(f"ghreview:{job_id}", review.model_dump())
    return RedirectResponse(f"/job/{job_id}#github", status_code=303)


def _refetch(user: str) -> list[dict]:
    with db.connect() as c:
        c.execute("DELETE FROM cache WHERE key = ?", (f"github:{user}",))
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
    return RedirectResponse(f"/job/{job_id}#github", status_code=303)


@app.post("/job/{job_id:path}/tailor")
def job_tailor(job_id: str, instructions: str = Form(""), role_id: str = Form("")):
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job, _ = db.get_job(job_id)
    role = roles.get_role(role_id) if role_id else None
    tailored = assistant.tailor(profile, ensure_description(job), role, instructions)
    cv_id = _save_cv(profile, job_id, tailored, f"{job.company}_{job.title}")
    db.set_status(job_id, "liked")
    return RedirectResponse(f"/cv/{cv_id}", status_code=303)


def _save_cv(profile: Profile, owner_id: str, tailored, label: str) -> int:
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    pdf_path = settings.OUTPUT_DIR / f"{profile.name.replace(' ', '_')}_{safe}_{int(time.time())}.pdf"
    render.cv_pdf(tailored.cv, pdf_path)
    return db.save_cv(owner_id, tailored, str(pdf_path))


@app.get("/cv/{cv_id}", response_class=HTMLResponse)
def cv_page(request: Request, cv_id: int):
    found = db.get_cv(cv_id)
    if not found:
        return page(request, "error.html", message="CV not found")
    owner_id, tailored, pdf_path = found
    job = role = None
    if owner_id.startswith("role:"):
        role = roles.get_role(owner_id[5:])
    else:
        job, _ = db.get_job(owner_id)
    return page(
        request, "cv_result.html", cv_id=cv_id, job=job, role=role, t=tailored, pdf_name=Path(pdf_path).name
    )


@app.get("/cv/{cv_id}/preview", response_class=HTMLResponse)
def cv_preview(cv_id: int):
    _, tailored, _ = db.get_cv(cv_id)
    return HTMLResponse(render.cv_html(tailored.cv))


@app.get("/cv/{cv_id}/pdf")
def cv_pdf(cv_id: int):
    _, _, pdf_path = db.get_cv(cv_id)
    return FileResponse(pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, error: str = ""):
    profile = assistant.load_profile()
    return page(
        request,
        "profile.html",
        profile=profile,
        profile_json=profile.model_dump_json(indent=2) if profile else "",
        has_photo=settings.PHOTO_PATH.exists(),
        error=error,
    )


@app.get("/profile/preview", response_class=HTMLResponse)
def profile_preview():
    profile = assistant.load_profile()
    return HTMLResponse(render.cv_html(profile) if profile else "No profile yet")


@app.post("/profile/import")
async def profile_import(cv: UploadFile = File(...)):
    data = await cv.read()
    profile = assistant.import_cv(data)
    assistant.save_profile(profile)
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/photo")
async def profile_photo(photo: UploadFile = File(...)):
    settings.PHOTO_PATH.write_bytes(await photo.read())
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/save")
def profile_save(profile_json: str = Form(...)):
    try:
        profile = Profile.model_validate(json.loads(profile_json))
    except (json.JSONDecodeError, ValidationError) as e:
        return RedirectResponse(f"/profile?error={str(e)[:300]}", status_code=303)
    assistant.save_profile(profile)
    return RedirectResponse("/profile", status_code=303)


@app.get("/interview", response_class=HTMLResponse)
def interview_page(request: Request, job_id: str = ""):
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job = None
    if job_id:
        job, _ = db.get_job(job_id)
        job = ensure_description(job)
    questions = assistant.interview_questions(profile, job)
    return page(request, "interview.html", questions=questions.questions, job=job, job_id=job_id)


@app.post("/interview")
async def interview_submit(request: Request):
    form = await request.form()
    job_id = str(form.get("job_id", ""))
    qa = []
    for key in form:
        if key.startswith("q_"):
            qid = key[2:]
            qa.append((str(form[key]), str(form.get(f"a_{qid}", ""))))
    profile = assistant.merge_answers(assistant.load_profile(), qa)
    assistant.save_profile(profile)
    return RedirectResponse(f"/job/{job_id}" if job_id else "/profile", status_code=303)


@app.get("/roles", response_class=HTMLResponse)
def roles_page(request: Request, edit: str = ""):
    all_roles = roles.load_roles()
    editing = roles.get_role(edit) if edit else None
    cvs = {r.id: db.cvs_for_job(f"role:{r.id}") for r in all_roles}
    return page(request, "roles.html", roles=all_roles, editing=editing or RoleProfile(id="", name=""), cvs=cvs)


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
    return RedirectResponse("/roles", status_code=303)


@app.post("/roles/{role_id}/delete")
def roles_delete(role_id: str):
    roles.delete_role(role_id)
    return RedirectResponse("/roles", status_code=303)


@app.post("/roles/{role_id}/cv")
def roles_cv(role_id: str, instructions: str = Form("")):
    profile = assistant.load_profile()
    role = roles.get_role(role_id)
    if not profile or not role:
        return RedirectResponse("/profile" if not profile else "/roles", status_code=303)
    tailored = assistant.tailor(profile, None, role, instructions)
    cv_id = _save_cv(profile, f"role:{role_id}", tailored, role.name)
    return RedirectResponse(f"/cv/{cv_id}", status_code=303)
