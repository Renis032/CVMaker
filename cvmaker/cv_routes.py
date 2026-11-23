"""Tailored CVs and cover letters: making, editing, layout, rating, previews and PDFs."""

import json
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError

from . import assistant, categories, claims, cvcheck, cvdiff, db, pack, render, roles, settings, tasks
from .auto import load_criteria
from .models import (
    CVComparison,
    CVContent,
    Job,
    Profile,
    RoleProfile,
    SkillGroup,
)
from .sources import ensure_description
from .web import _back, _job_label, _need_profile, _not_found, _split, page

router = APIRouter()


# --- CVs -----------------------------------------------------------------------------------------------


def _layout(one_page: bool, font: str, instructions: str = "") -> dict:
    try:
        font_pt = float(font) if font and font != "auto" else None
    except ValueError:
        font_pt = None
    return {"font_pt": font_pt, "one_page": one_page, "instructions": instructions.strip()}


@router.post("/job/{job_id:path}/tailor")
def job_tailor(
    job_id: str, instructions: str = Form(""), role_id: str = Form(""), one_page: bool = Form(False), font: str = Form("auto")
):
    _need_profile()
    job, _ = db.get_job(job_id)
    layout = _layout(one_page, font, instructions)
    tasks.submit("tailor", _job_label("Tailored CV", job), "tailor", subject=f"tailor:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#docs", args={"job_id": job_id, "role_id": role_id, "layout": layout})
    return _back(f"/job/{job_id}#docs")


@tasks.runner("tailor")
def _tailor(progress, job_id: str, role_id: str, layout: dict) -> str:
    role = roles.get_role(role_id) if role_id else None
    return f"/cv/{pack.tailored_cv(_need_profile(), ensure_description(db.get_job(job_id)[0]), role, layout, progress)}"


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


@router.get("/cv/{cv_id}", response_class=HTMLResponse)
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


@router.post("/cv/{cv_id}/edit")
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


@router.post("/cv/{cv_id}/layout")
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
    label = f"Fit CV #{cv_id} on one page" + (f" · {job.title} @ {job.company}" if job else "")
    tasks.submit("onepage", label, "onepage", subject=f"onepage:cv:{cv_id}", job_id=job.id if job else "", url=f"/cv/{cv_id}",
                 args={"cv_id": cv_id})


@tasks.runner("onepage")
def _onepage(progress, cv_id: int) -> str:
    owner_id, tailored, pdf_path = db.get_cv(cv_id)
    job, _ = _owner(owner_id)
    layout, _ = db.cv_layout(cv_id)
    pages, used = pack.render_fitting(tailored, Path(pdf_path), {**layout, "one_page": True}, job and ensure_description(job), progress)
    db.update_cv(cv_id, tailored, {**layout, "font_used": used}, pages)
    db.cache_delete(f"cvrate:{cv_id}:profile")
    return f"/cv/{cv_id}"


@router.post("/cv/{cv_id}/one-page")
def cv_one_page(cv_id: int):
    layout, _ = db.cv_layout(cv_id)
    db.update_cv(cv_id, layout={**layout, "one_page": True})
    _submit_shorten(cv_id)
    return _back(f"/cv/{cv_id}#layout")


@router.post("/cv/{cv_id}/sent")
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


@router.post("/cv/{cv_id}/rate")
def cv_rate(cv_id: int, baseline: str = Form("profile")):
    _need_profile()
    found = db.get_cv(cv_id)
    if not found:
        return _back("/")
    owner_id, _, _ = found
    job, role = _owner(owner_id)
    target = f"{job.title} @ {job.company}" if job else (role.name if role else "")
    tasks.submit("cvrate", f"Rate CV #{cv_id} · {target}", "cvrate", subject=f"rate:{baseline}:cv:{cv_id}", job_id=job.id if job else "",
                 url=f"/cv/{cv_id}?vs={baseline}#rating", args={"cv_id": cv_id, "baseline": baseline})
    return _back(f"/cv/{cv_id}#rating")


@tasks.runner("cvrate")
def _cvrate(progress, cv_id: int, baseline: str) -> str:
    profile = _need_profile()
    owner_id, _, _ = db.get_cv(cv_id)
    job, role = _owner(owner_id)
    refresh_cv_pdf(cv_id)
    _, tailored, pdf_path = db.get_cv(cv_id)
    full = ensure_description(job) if job else None
    extra = (role.key_skills + role.skill_keywords) if role else categories.expand(load_criteria()).skill_keywords
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


@router.get("/cv/{cv_id}/preview", response_class=HTMLResponse)
def cv_preview(cv_id: int):
    _, tailored, _ = db.get_cv(cv_id)
    layout, _ = db.cv_layout(cv_id)
    return HTMLResponse(render.cv_html(tailored.cv, layout.get("font_pt") or layout.get("font_used"), layout))


@router.get("/cv/{cv_id}/pdf")
def cv_pdf(cv_id: int):
    refresh_cv_pdf(cv_id)
    _, _, pdf_path = db.get_cv(cv_id)
    return FileResponse(pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)



# --- cover letters -------------------------------------------------------------------------------------------


@router.post("/job/{job_id:path}/letter")
def job_letter(
    job_id: str,
    cv_id: str = Form(""),
    language: str = Form("English"),
    tone: str = Form("professional"),
    length: str = Form("standard"),
    instructions: str = Form(""),
):
    _need_profile()
    job, _ = db.get_job(job_id)
    tasks.submit("letter", _job_label("Cover letter", job), "letter", subject=f"letter:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#letter",
                 args={"job_id": job_id, "cv_id": cv_id, "language": language, "tone": tone, "length": length, "instructions": instructions})
    return _back(f"/job/{job_id}#letter")


@tasks.runner("letter")
def _letter(progress, job_id: str, cv_id: str, language: str, tone: str, length: str, instructions: str) -> str:
    job, _ = db.get_job(job_id)
    found = db.get_cv(int(cv_id)) if cv_id else None
    cv = found[1].cv if found and found[0] == job_id else None
    letter_id = pack.letter(_need_profile(), ensure_description(job), cv, language, tone, length, instructions, progress)
    return f"/letter/{letter_id}"


@router.get("/letter/{letter_id}", response_class=HTMLResponse)
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


@router.post("/letter/{letter_id}/save")
def letter_save(letter_id: int, body: str = Form(...)):
    found = db.get_letter(letter_id)
    profile = assistant.load_profile()
    if not found or not profile:
        return _back("/")
    job, _ = db.get_job(found["job_id"])
    db.update_letter_body(letter_id, body.replace("\r\n", "\n"))
    render.letter_pdf(body.replace("\r\n", "\n"), profile, job, Path(found["pdf_path"]))
    return _back(f"/letter/{letter_id}?saved=1")


@router.post("/letter/{letter_id}/sent")
def letter_sent(letter_id: int):
    found = db.get_letter(letter_id)
    if found:
        db.save_application(found["job_id"], sent_letter_id=letter_id)
    return _back(f"/letter/{letter_id}")


@router.get("/letter/{letter_id}/preview", response_class=HTMLResponse)
def letter_preview(letter_id: int):
    found = db.get_letter(letter_id)
    job, _ = db.get_job(found["job_id"])
    return HTMLResponse(render.letter_html(found["body"], assistant.load_profile(), job))


@router.get("/letter/{letter_id}/pdf")
def letter_pdf(letter_id: int):
    found = db.get_letter(letter_id)
    return FileResponse(found["pdf_path"], media_type="application/pdf", filename=Path(found["pdf_path"]).name)
