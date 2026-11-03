"""The CV editor: the built React app at /editor/, its API at /editor/api/ and uploaded images.
Included at the end of app.py (it uses app.py's page helpers)."""

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import assistant, db, editor, render, settings
from .app import _back, _not_found, page, refresh_cv_pdf

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
        return page(request, "error.html", message="Set up your profile first (My profile → Import my CV); the editor edits it.")
    return page(request, "editor.html", query=f"?cv={quote(cv)}" if cv else "")


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


@router.get("/editor/api/data")
def editor_data(cv: str = ""):
    if cv:
        target = _cv_target(cv)
        if not target:
            return _error(_not_found("CV"), 404)
        return editor.cv_document(*target)
    if not assistant.load_profile():
        return _error("Set up your profile first (My profile → Import my CV).")
    return editor.profile_document()


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
        return {"path": editor.save_upload(name, await request.body())}
    except ValueError as e:
        return _error(str(e))


@router.get("/editor/api/uploads")
def editor_uploads():
    return {"files": editor.list_uploads()}


@router.get("/editor/uploads/{name}")
def editor_uploaded(name: str):
    found = editor.image_bytes(f"/editor/uploads/{Path(name).name}")
    return Response(found[0], media_type=found[1]) if found else Response(status_code=404)


@router.get("/editor/api/photo")
def editor_photo():
    found = editor.image_bytes("/editor/api/photo")
    return Response(found[0], media_type=found[1]) if found else Response(status_code=404)


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
        return {"dir": str(Path(pdf_path).parent), "files": [Path(pdf_path).name], "pages": pages}
    profile = assistant.load_profile()
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
    return {"dir": str(_versions_dir()), "files": files, "pages": pages}


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
    if not editor.CVEDITOR_DATA.exists():
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
