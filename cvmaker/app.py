import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import assistant, auto, companies, cvcheck, db, github, render, roles, settings
from .auto import CRITERIA_PATH, load_criteria
from .llm import LLMError
from .models import (
    CVComparison,
    CVContent,
    Criteria,
    FitAssessment,
    GithubReview,
    InterviewPrep,
    Profile,
    Project,
    RoleProfile,
)
from .search import SENIORITY_LEVELS, load_companies, run_search
from .sources import BOARD_INFO, COMPANY_SOURCES, board_has_key, ensure_description


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto.start()
    yield


app = FastAPI(title="CVMaker", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["md_bold"] = render.env.filters["md_bold"]
templates.env.filters["ago"] = lambda ts: _ago(ts)

last_search: dict = {}


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
        {"profile_exists": settings.PROFILE_PATH.exists(), "unseen_total": db.unseen_count(), **ctx},
    )


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


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    status: str = "",
    only_last: int = 0,
    role: str = "",
    unseen: int = 0,
    salary: int = 0,
    sort: str = "score",
):
    ids = last_search.get("job_ids") if only_last else None
    criteria = load_criteria()
    chosen = roles.get_role(role) if role else None
    if chosen:
        criteria.role_keywords = chosen.role_keywords or criteria.role_keywords
        criteria.skill_keywords = chosen.skill_keywords or criteria.skill_keywords
    state = auto.load_state()
    return page(
        request,
        "index.html",
        criteria=criteria,
        roles=roles.load_roles(),
        chosen_role=chosen,
        rows=db.list_jobs(ids=ids, status=status or None, unseen=bool(unseen), with_salary=bool(salary), sort=sort),
        status=status,
        only_last=only_last,
        unseen=unseen,
        salary=salary,
        sort=sort,
        last=last_search,
        company_sources=list(COMPANY_SOURCES),
        boards=[(name, info["label"], board_has_key(name), info["key"]) for name, info in BOARD_INFO.items()],
        seniority_levels=SENIORITY_LEVELS,
        countries=[c for c in load_companies() if c != "REMOTE"],
        auto_state=state,
        auto_next=_until(auto.next_run(state)) if state.enabled else "",
    )


def _query(**params) -> str:
    """The current list's query string with some parameters changed (for tabs and sort links)."""
    return "?" + "&".join(f"{k}={v}" for k, v in params.items() if v not in ("", 0, None))


templates.env.globals["query"] = _query


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
    remote_only: bool = Form(False),
    seniority: list[str] = Form([]),
    must_mention: str = Form(""),
    salary_only: bool = Form(False),
    min_score: int = Form(0),
):
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
    CRITERIA_PATH.write_text(criteria.model_dump_json(indent=2))
    started = time.time()
    result = run_search(criteria, assistant.load_profile())
    last_search.clear()
    last_search.update(
        job_ids=result.job_ids,
        new_count=len(result.new_ids),
        errors=result.errors,
        counts=result.counts,
        seconds=round(time.time() - started, 1),
    )
    return RedirectResponse("/?only_last=1", status_code=303)


@app.post("/seen")
def mark_all_seen(back: str = Form("/")):
    db.mark_seen()
    return RedirectResponse(back, status_code=303)


@app.post("/auto")
def auto_settings(enabled: bool = Form(False), every_hours: float = Form(6), notify: bool = Form(False)):
    state = auto.load_state()
    state.enabled, state.every_hours, state.notify = enabled, max(0.5, every_hours), notify
    auto.save_state(state)
    return RedirectResponse("/#auto", status_code=303)


@app.post("/auto/run")
def auto_run_now():
    auto.run_once()
    return RedirectResponse("/?unseen=1", status_code=303)


@app.get("/job/{job_id:path}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    found = db.get_job(job_id)
    if not found:
        return page(request, "error.html", message="Job not found")
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
        fit=_cached_fit(job_id),
        gh=_cached_review(job_id),
        profile_links=_profile_project_links(),
        cvs=_cvs_with_scores(job_id),
        letters=db.letters_for_job(job_id),
        has_prep=_cached_prep(job_id) is not None,
        fetch_error=fetch_error,
        roles=roles.load_roles(),
    )


def _cached_fit(job_id: str) -> FitAssessment | None:
    cached = db.cache_get(f"fit2:{job_id}", 24 * 365)
    try:
        return FitAssessment.model_validate(cached) if cached else None
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
    try:
        repos_text = github.repos_for_prompt(github.fetch_repos(github.username(profile)), compact=True)
    except Exception:
        repos_text = ""
    fit = assistant.assess_fit(profile, ensure_description(job), repos_text)
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
    )


def _baseline(owner_id: str, baseline: str, profile: Profile) -> tuple[CVContent, Path, str]:
    """The CV to compare against: the original profile, or another CV generated for the same job."""
    if baseline != "profile":
        found = db.get_cv(int(baseline))
        if found and found[0] == owner_id:
            return found[1].cv, Path(found[2]), f"CV #{baseline}"
    original = CVContent.model_validate(profile.model_dump(include=set(CVContent.model_fields)))
    pdf = render.cv_pdf(original, settings.OUTPUT_DIR / "_original_profile.pdf")
    return original, pdf, "Original (profile)"


@app.post("/cv/{cv_id}/rate")
def cv_rate(cv_id: int, baseline: str = Form("profile")):
    profile = assistant.load_profile()
    found = db.get_cv(cv_id)
    if not profile or not found:
        return RedirectResponse("/profile" if not profile else "/", status_code=303)
    owner_id, tailored, pdf_path = found
    job = role = None
    if owner_id.startswith("role:"):
        role = roles.get_role(owner_id[5:])
        extra = (role.key_skills + role.skill_keywords) if role else []
    else:
        job, _ = db.get_job(owner_id)
        job = ensure_description(job)
        extra = load_criteria().skill_keywords
    base_cv, base_pdf, label = _baseline(owner_id, baseline, profile)
    keywords = cvcheck.job_keywords(job, profile, extra)
    base_cov = cvcheck.coverage(keywords, cvcheck.cv_text(base_cv))
    new_cov = cvcheck.coverage(keywords, cvcheck.cv_text(tailored.cv))
    report = "\n".join(
        f"- {k}: baseline {'yes' if base_cov[k] else 'no'}, tailored {'yes' if new_cov[k] else 'no'}" for k in keywords
    ) or "(no keywords found)"
    comparison = assistant.compare_cvs(base_cv, tailored.cv, job, role, report)
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
    return RedirectResponse(f"/cv/{cv_id}?vs={baseline}#rating", status_code=303)


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


@app.post("/job/{job_id:path}/letter")
def job_letter(
    job_id: str,
    cv_id: str = Form(""),
    language: str = Form("English"),
    tone: str = Form("professional"),
    length: str = Form("standard"),
    instructions: str = Form(""),
):
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job, _ = db.get_job(job_id)
    found = db.get_cv(int(cv_id)) if cv_id else None
    cv = found[1].cv if found and found[0] == job_id else None
    letter = assistant.cover_letter(profile, ensure_description(job), cv, language, tone, length, instructions)
    body = render.letter_body(letter, profile.name)
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{job.company}_{job.title}")[:80]
    pdf_path = settings.OUTPUT_DIR / f"{profile.name.replace(' ', '_')}_CoverLetter_{safe}_{int(time.time())}.pdf"
    render.letter_pdf(body, profile, job, pdf_path)
    letter_id = db.save_letter(job_id, letter, body, str(pdf_path))
    return RedirectResponse(f"/letter/{letter_id}", status_code=303)


@app.get("/letter/{letter_id}", response_class=HTMLResponse)
def letter_page(request: Request, letter_id: int, saved: int = 0):
    found = db.get_letter(letter_id)
    if not found:
        return page(request, "error.html", message="Cover letter not found")
    job, _ = db.get_job(found["job_id"])
    return page(
        request,
        "letter_result.html",
        l=found,
        job=job,
        saved=saved,
        others=[x for x in db.letters_for_job(found["job_id"]) if x["id"] != letter_id],
        pdf_name=Path(found["pdf_path"]).name,
    )


@app.post("/letter/{letter_id}/save")
def letter_save(letter_id: int, body: str = Form(...)):
    found = db.get_letter(letter_id)
    profile = assistant.load_profile()
    if not found or not profile:
        return RedirectResponse("/", status_code=303)
    job, _ = db.get_job(found["job_id"])
    db.update_letter_body(letter_id, body.replace("\r\n", "\n"))
    render.letter_pdf(body.replace("\r\n", "\n"), profile, job, Path(found["pdf_path"]))
    return RedirectResponse(f"/letter/{letter_id}?saved=1", status_code=303)


@app.get("/letter/{letter_id}/preview", response_class=HTMLResponse)
def letter_preview(letter_id: int):
    found = db.get_letter(letter_id)
    job, _ = db.get_job(found["job_id"])
    return HTMLResponse(render.letter_html(found["body"], assistant.load_profile(), job))


@app.get("/letter/{letter_id}/pdf")
def letter_pdf(letter_id: int):
    found = db.get_letter(letter_id)
    return FileResponse(found["pdf_path"], media_type="application/pdf", filename=Path(found["pdf_path"]).name)


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
        return page(request, "error.html", message="Job not found")
    return page(request, "prep.html", job=found[0], prep=_cached_prep(job_id), has_fit=_cached_fit(job_id) is not None)


@app.post("/prep/{job_id:path}")
def prep_generate(job_id: str):
    profile = assistant.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    job, _ = db.get_job(job_id)
    prep = assistant.interview_prep(profile, ensure_description(job), _cached_fit(job_id))
    db.cache_put(f"prep:{job_id}", prep.model_dump())
    return RedirectResponse(f"/prep/{job_id}", status_code=303)


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
    return RedirectResponse("/companies", status_code=303)


@app.post("/companies/remove")
def companies_remove(group: str = Form(...), name: str = Form(...)):
    companies.remove(group, name)
    return RedirectResponse("/companies", status_code=303)


@app.post("/companies/test", response_class=HTMLResponse)
def companies_test(request: Request, group: str = Form(...), name: str = Form(...)):
    company = next((c for c in companies.load().get(group, []) if c["name"] == name), None)
    tested = {"name": name, "error": "", "postings": []}
    try:
        tested["postings"] = companies.test(company)
    except Exception as e:
        tested["error"] = f"{type(e).__name__}: {e}"
    return page(request, "companies.html", groups=companies.load(), types=list(COMPANY_SOURCES), found=None, tested=tested)
