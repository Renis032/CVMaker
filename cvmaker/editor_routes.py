"""The CV editor: the built React app at /editor/, its API at /editor/api/ and uploaded images.
Included at the end of app.py (it uses app.py's page helpers)."""

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import assistant, cvcheck, db, editor, llm, render, settings
from .models import RewriteResult
from .cv_routes import refresh_cv_pdf
from .web import _back, _not_found, page

router = APIRouter()

BUILT = {"editor.js": "text/javascript", "editor.css": "text/css"}


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _cv_target(cv: str):
    """(cv id, label, back link) for a tailored CV, or None when the id is not one of this person's CVs."""
    try:
        cv_id = int(cv)
    except ValueError:
        return None
    found = db.get_cv(cv_id)
    if not found:
        return None
    owner_id, _, _ = found
    job = db.get_job(owner_id)
    label = f"CV #{cv_id} · {job[0].title} @ {job[0].company}" if job else f"CV #{cv_id}"
    return cv_id, label, f"/cv/{cv_id}"


@router.get("/editor", response_class=HTMLResponse)
@router.get("/editor/", response_class=HTMLResponse)
def editor_page(request: Request, cv: str = ""):
    """The editor as a CVMaker page: the site's header and nav, the editor app filling the rest of the window."""
    if not (editor.STATIC / "index.html").exists():
        return page(request, "error.html", message="The CV editor is not built yet: run `npm install && npm run build` in the editor/ folder.")
    if not cv and not assistant.load_profile():
        return _back("/profile#start")  # pick a job category (or import a CV) first
    return page(request, "editor.html", query=f"?cv={quote(cv)}" if cv else "")


@router.post("/editor/start")
def editor_start(request: Request, starter: str = Form("developer")):
    """A new CV for a job category: its labels and sections, nothing written for you."""
    try:
        editor.start_blank(starter)
    except ValueError as e:
        return page(request, "error.html", message=str(e))
    return _back("/editor")


@router.get("/editor/app", response_class=HTMLResponse)
def editor_app():
    """The editor app itself (shown inside the /editor page)."""
    index = editor.STATIC / "index.html"
    if not index.exists():
        return Response(status_code=404)
    return HTMLResponse(index.read_text(), headers={"Cache-Control": "no-store"})


@router.get("/editor/{name}")
def editor_asset(name: str):
    if name not in BUILT or not (editor.STATIC / name).exists():
        return Response(status_code=404)
    return FileResponse(editor.STATIC / name, media_type=BUILT[name], headers={"Cache-Control": "no-cache"})


def _cv_job(cv_id: int):
    found = db.get_cv(cv_id)
    job = db.get_job(found[0]) if found else None
    return job[0] if job else None


@router.get("/editor/api/data")
def editor_data(cv: str = ""):
    if cv:
        target = _cv_target(cv)
        if not target:
            return _error(_not_found("CV"), 404)
        doc = editor.cv_document(*target)
        job = _cv_job(target[0])
        profile = assistant.load_profile()
        if job and profile:
            # What the posting asks for, so the editor can show which of it this CV covers.
            doc["job"] = {"title": job.title, "company": job.company, "url": f"/job/{job.id}",
                          "keywords": cvcheck.job_keywords(job, profile)}
        return doc
    return editor.profile_document()  # without a profile yet: a blank, labelled CV to fill in


@router.post("/editor/api/data")
async def editor_save(request: Request, cv: str = ""):
    try:
        data = json.loads(await request.body())
    except json.JSONDecodeError:
        return _error("Not valid JSON")
    try:
        if cv:
            target = _cv_target(cv)
            if not target:
                return _error(_not_found("CV"), 404)
            rev = editor.save_cv_document(target[0], data)
        else:
            rev = editor.save_profile_document(data)
    except editor.Conflict as e:
        return _error(str(e), 409)
    except (KeyError, TypeError, ValueError) as e:
        return _error(f"Could not save: {e}")
    return {"ok": True, "rev": rev}


@router.post("/editor/api/upload")
async def editor_upload(request: Request, name: str = "image.png"):
    try:
        return {"path": editor.save_upload(Path(name).name, await request.body())}
    except ValueError as e:
        return _error(str(e))


@router.get("/editor/api/uploads")
def editor_uploads():
    return {"files": editor.list_uploads()}


@router.delete("/editor/api/uploads")
def editor_upload_delete(path: str = ""):
    """Delete an image from your images, unless the CV still uses it (as the photo, a logo or on the page)."""
    name = Path(path).name
    file = editor.uploads_dir() / name
    if not name or not file.is_file():
        return _error("Not found", 404)
    if f"/editor/uploads/{name}" in json.dumps(editor.load_layout()):
        return _error("This image is still used on your CV (photo, a logo or on the page). Remove it there first.", 409)
    file.unlink()
    return {"ok": True}


# Uploaded files are served so that nothing in them can run as this site (an SVG opened on its own, say).
SAFE_FILE_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"}


@router.get("/editor/uploads/{name}")
def editor_uploaded(name: str):
    found = editor.image_bytes(f"/editor/uploads/{Path(name).name}")
    return Response(found[0], media_type=found[1], headers=SAFE_FILE_HEADERS) if found else Response(status_code=404)


@router.get("/editor/api/photo")
def editor_photo():
    found = editor.image_bytes("/editor/api/photo")
    return Response(found[0], media_type=found[1], headers=SAFE_FILE_HEADERS) if found else Response(status_code=404)


def _versions_dir() -> Path:
    path = settings.OUTPUT_DIR / "versions"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/editor/api/export")
async def editor_export(request: Request, cv: str = ""):
    body = json.loads((await request.body()) or b"{}")
    if cv:
        target = _cv_target(cv)
        if not target:
            return _error(_not_found("CV"), 404)
        cv_id = target[0]
        layout, _ = db.cv_layout(cv_id)
        db.update_cv(cv_id, layout={**layout, "pdf_stale": True})
        refresh_cv_pdf(cv_id)
        _, _, pdf_path = db.get_cv(cv_id)
        _, pages = db.cv_layout(cv_id)
        return {"dir": str(Path(pdf_path).parent) if settings.DESKTOP else "", "files": [Path(pdf_path).name], "pages": pages}
    profile = assistant.load_profile() or editor.blank_profile()
    layout = editor.load_layout()
    ids = body.get("ids") or [v["id"] for v in layout["variants"]]
    files, pages = [], 0
    for variant in layout["variants"]:
        if variant["id"] not in ids:
            continue
        name = (editor.safe_file_name(variant.get("fileName") or variant["name"])) + ".pdf"
        out = _versions_dir() / name
        render._print_pdf(editor.print_html(profile, variant), out)
        pages = max(pages, render.page_count(out))
        files.append(name)
    return {"dir": str(_versions_dir()) if settings.DESKTOP else "", "files": files, "pages": pages}


@router.get("/editor/api/file")
def editor_file(name: str = "", cv: str = ""):
    if cv:
        target = _cv_target(cv)
        if not target:
            return _error(_not_found("CV"), 404)
        return _back(f"/cv/{target[0]}/pdf")
    path = _versions_dir() / Path(name).name
    if not name or not path.is_file():
        return _error("Not found", 404)
    return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")


@router.post("/editor/import")
def editor_import(request: Request):
    if settings.SERVER or not editor.CVEDITOR_DATA.exists():
        return page(request, "error.html", message=f"No CVEditor data found at {editor.CVEDITOR_DATA}.")
    try:
        report = editor.import_cveditor()
    except ValueError as e:
        return page(request, "error.html", message=str(e))
    db.cache_put("editor:import-report", report)
    return _back("/profile#editor")


@router.post("/editor/import-report/dismiss")
def editor_import_dismiss():
    db.cache_delete("editor:import-report")
    return _back("/profile#editor")


# --- rewrite a piece of text with Claude ---------------------------------------------------------------------------

REWRITE_MODES = {
    "shorter": "Make it shorter and tighter (about 30% fewer words) without losing the facts or the main achievement.",
    "numbers": "Make the impact concrete with numbers - but only numbers that appear in the candidate's profile. If the "
               "profile has no number for this, keep the wording honest and say in the note which number the candidate "
               "could add (e.g. 'how many users', 'by what % it got faster').",
    "verbs": "Start with a strong, specific action verb and remove filler (\"responsible for\", \"worked on\", \"helped with\").",
    "job": "Reword it to speak to the job below: use the posting's vocabulary where it honestly describes the same work, "
           "and put first what this job cares about most.",
}


def _rewrite_system(mode: str, custom: str) -> str:
    goal = REWRITE_MODES.get(mode) or f"Follow the candidate's instruction: {custom}"
    return f"""You improve one piece of text from a candidate's CV (a bullet, the summary, a title or a skill list).
Goal: {goal}
Return 2 or 3 alternative versions in `options`, best first. Keep the same kind of text (a bullet stays one bullet;
a skill list stays a comma-separated list). Keep **bold** markup for the key phrases, as the original does.
`note`: one short sentence when something important could not be done honestly; otherwise empty.
{assistant.TRUTH_RULES}"""


@router.post("/editor/api/rewrite")
async def editor_rewrite(request: Request, cv: str = ""):
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError:
        return _error("Not valid JSON")
    text, mode, custom = (body.get("text") or "").strip(), body.get("mode", ""), (body.get("custom") or "").strip()[:500]
    if not text:
        return _error("There is no text to rewrite.")
    if mode not in REWRITE_MODES and not custom:
        return _error("Pick how to rewrite it.")
    profile = assistant.load_profile()
    if not profile:
        return _error("Set up your profile first.")
    parts = [f"Text to rewrite:\n{text}", f"Candidate profile (the only source of facts):\n{profile.model_dump_json()}"]
    if cv:
        target = _cv_target(cv)
        job = _cv_job(target[0]) if target else None
        if job:
            parts.append(f"Job posting:\n{assistant._job_text(job)}")
    elif mode == "job":
        return _error("“Fit this job” works on a CV tailored for a job (open one from its CV page).")
    try:
        result = await run_in_threadpool(llm.ask, _rewrite_system(mode, custom), "\n\n".join(parts), RewriteResult, "medium")
    except llm.LLMError as e:
        return _error(str(e), 502)
    return {"options": [o.strip() for o in result.options if o.strip()][:3], "note": result.note}
