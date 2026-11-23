"""Who you are: the profile (import, photo, save), the profile interview, role profiles and the people of an account."""

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from . import assistant, db, editor, pack, people, render, roles, settings, tasks, uploads
from .llm import LLMError
from .models import (
    InterviewQuestions,
    Profile,
    RoleProfile,
)
from .sources import ensure_description
from .web import _back, _job_label, _need_profile, _split, page
from .cv_routes import _layout

router = APIRouter()


# --- profile -------------------------------------------------------------------------------------------


@router.get("/profile", response_class=HTMLResponse)
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
        starters=[(k, v["name"], v["skills"] + v.get("sections", [])) for k, v in editor.STARTERS.items()],
    )


@router.get("/profile/preview", response_class=HTMLResponse)
def profile_preview():
    profile = assistant.load_profile()
    return HTMLResponse(render.cv_html(profile) if profile else "No profile yet")


@router.post("/profile/import")
async def profile_import(cv: UploadFile = File(...)):
    name = Path(cv.filename or "cv.pdf").name
    if Path(name).suffix.lower() not in assistant.IMPORT_TYPES:
        return _back("/profile?error=" + quote("Use a PDF, Word (.docx), text file or a picture of your CV."))
    try:
        data = await uploads.read(cv, kinds=uploads.CV_KINDS)
    except uploads.UploadError as e:
        return _back("/profile?error=" + quote(str(e)))
    stored = uploads.stash(data, name)
    tasks.submit("import", f"Import CV · {name}", "import-cv", subject="import", url="/profile", args={"stored": stored, "name": name})
    return _back("/profile")


@tasks.runner("import-cv")
def _import_cv(progress, stored: str, name: str) -> str:
    data = uploads.unstash(stored)
    progress.set(step="Claude is reading your CV")
    assistant.save_profile(assistant.import_cv(data, name))
    uploads.drop(stored)
    return "/profile"


@router.post("/profile/photo")
async def profile_photo(photo: UploadFile = File(...)):
    try:
        data, _ = uploads.clean_image(await uploads.read(photo, kinds={"image"}), keep_format=False)
    except uploads.UploadError as e:
        return _back("/profile?error=" + quote(str(e)))
    settings.PHOTO_PATH.write_bytes(data)
    editor.use_profile_photo()
    return _back("/profile")


@router.post("/profile/photo/remove")
def profile_photo_remove():
    """No photo on any CV: the profile photo is deleted and the editor shows none."""
    settings.PHOTO_PATH.unlink(missing_ok=True)
    if editor.exists():
        layout = editor.load_layout()
        layout["photo"] = editor.NO_PHOTO
        editor.save_layout(layout)
    return _back("/profile")


@router.post("/profile/save")
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


@router.get("/interview", response_class=HTMLResponse)
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


@router.post("/interview/questions")
def interview_questions(job_id: str = Form("")):
    _need_profile()
    job = db.get_job(job_id)[0] if job_id else None
    url = f"/interview?job_id={job_id}" if job_id else "/interview"

    label = _job_label("Interview questions", job) if job else "Interview questions · general"
    tasks.submit("questions", label, "questions", subject=f"questions:{job_id or 'general'}", job_id=job_id, url=url,
                 args={"job_id": job_id, "url": url})
    return _back(url)


@tasks.runner("questions")
def _questions(progress, job_id: str, url: str) -> str:
    job = db.get_job(job_id)[0] if job_id else None
    progress.set(step="Claude is finding the gaps in your profile")
    questions = assistant.interview_questions(_need_profile(), ensure_description(job) if job else None)
    db.cache_put(_questions_key(job_id), questions.model_dump())
    return url


@router.post("/interview")
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

    tasks.submit("answers", "Add interview answers to my profile", "answers", subject="answers", job_id=job_id, url="/profile",
                 args={"job_id": job_id, "qa": qa, "url": url})
    return _back(url)


@tasks.runner("answers")
def _answers(progress, job_id: str, qa: list, url: str) -> str:
    progress.set(step="Claude is adding your answers to your profile")
    assistant.save_profile(assistant.merge_answers(assistant.load_profile(), [tuple(x) for x in qa]))
    db.cache_delete(_questions_key(job_id))
    return url


# --- role profiles -----------------------------------------------------------------------------------------


@router.get("/roles", response_class=HTMLResponse)
def roles_page(request: Request, edit: str = ""):
    all_roles = roles.load_roles()
    editing = roles.get_role(edit) if edit else None
    cvs = {r.id: db.cvs_for_job(f"role:{r.id}") for r in all_roles}
    active = {t["subject"][len("tailor:role:"):]: t for t in db.task_active("tailor") if t["subject"].startswith("tailor:role:")}
    return page(
        request, "roles.html", roles=all_roles, editing=editing or RoleProfile(id="", name=""), cvs=cvs, active=active,
        font_choices=render.font_choices(),
    )


@router.post("/roles/save")
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


@router.post("/roles/{role_id}/delete")
def roles_delete(role_id: str):
    roles.delete_role(role_id)
    return _back("/roles")


@router.post("/roles/{role_id}/cv")
def roles_cv(role_id: str, instructions: str = Form(""), one_page: bool = Form(False), font: str = Form("auto")):
    _need_profile()
    role = roles.get_role(role_id)
    if not role:
        return _back("/roles")
    layout = _layout(one_page, font, instructions)
    tasks.submit("tailor", f"General CV · {role.name}", "role-cv", subject=f"tailor:role:{role_id}", url="/roles",
                 args={"role_id": role_id, "layout": layout, "instructions": instructions})
    return _back("/roles")


@tasks.runner("role-cv")
def _role_cv(progress, role_id: str, layout: dict, instructions: str) -> str:
    profile = _need_profile()
    role = roles.get_role(role_id)
    if not role:
        raise LLMError("That role profile no longer exists.")
    progress.set(step="Claude is writing your CV")
    tailored = assistant.tailor(profile, None, role, instructions)
    cv_id = pack.save_cv(profile, f"role:{role_id}", tailored, role.name, layout, None, progress)
    return f"/cv/{cv_id}"



# --- people ----------------------------------------------------------------------------------------------


def _as_person(response: RedirectResponse, person_id: str) -> RedirectResponse:
    response.set_cookie(people.COOKIE, person_id, max_age=10 * 365 * 24 * 3600, samesite="lax")
    return response


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request):
    rows = []
    for p in people.load():
        with people.acting_as(p.id):
            profile = assistant.load_profile()
            rows.append(
                {
                    "person": p,
                    "folder": str(people.data_dir(p.id)) if settings.DESKTOP else "",
                    "headline": profile.headline if profile else "",
                    "totals": db.overview(),
                    "has_profile": profile is not None,
                }
            )
    return page(request, "people.html", rows=rows)


@router.post("/people/switch")
def people_switch(request: Request, person: str = Form(...), next_url: str = Form("")):
    if person == "__manage":
        return _back("/people")
    if not people.get(person):
        return _back("/people")
    target = next_url if next_url.startswith("/") and not next_url.startswith(("/job/", "/cv/", "/letter/", "/prep/")) else "/"
    return _as_person(_back(target), person)


@router.post("/people/add")
def people_add(name: str = Form(...)):
    person = people.add(name)
    return _as_person(_back("/profile"), person.id)


@router.post("/people/rename")
def people_rename(person: str = Form(...), name: str = Form(...)):
    people.rename(person, name)
    return _back("/people")


@router.post("/people/remove")
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
