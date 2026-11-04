"""Smaller site-wide features: company logos, the notification bell, backups and the history of your CV.
Included at the end of app.py (it uses app.py's page helpers)."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import UploadFile as StarletteUpload

import shutil

from . import backup, db, history, logos, people, settings
from .app import _back, page

router = APIRouter()


# --- company logos ---------------------------------------------------------------------------------------------


@router.get("/logo")
def company_logo(c: str = "", u: str = "", l: str = ""):  # noqa: E741 - short query names keep the img URLs short
    found = logos.logo(c, u, l) if c else None
    if not found:
        return Response(status_code=404, headers={"Cache-Control": "max-age=86400"})
    return Response(found[0], media_type=found[1], headers={"Cache-Control": "max-age=604800"})


# --- notifications -----------------------------------------------------------------------------------------------


@router.get("/api/notifications")
def api_notifications():
    return {"unread": db.notifications_unread(), "items": db.notifications(20)}


@router.get("/notifications/{notification_id:int}/open")
def notification_open(notification_id: int):
    n = db.notification_get(notification_id)
    if not n:
        return _back("/")
    db.notifications_mark_read(notification_id)
    return _back(n["url"] if n["url"].startswith("/") else "/")


@router.post("/notifications/read")
def notifications_read(request: Request):
    db.notifications_mark_read()
    ref = request.headers.get("referer", "/")
    path = "/" + ref.split("/", 3)[3] if ref.count("/") >= 3 else "/"
    return _back(path)


# --- backups -------------------------------------------------------------------------------------------------------


@router.post("/backup")
def backup_make():
    path = backup.make()
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/backup/file/{name}")
def backup_file(request: Request, name: str):
    path = backup.backups_dir() / Path(name).name
    if not path.is_file() or path.suffix != ".zip":
        return page(request, "error.html", message="Backup not found")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/backup/restore")
async def backup_restore(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, StarletteUpload) or not upload.filename:
        return _back("/profile?backup_error=" + quote("Choose a backup .zip file first.") + "#backup")
    try:
        report = backup.restore(await upload.read())
    except ValueError as e:
        return _back("/profile?backup_error=" + quote(str(e)) + "#backup")
    db.cache_put("backup:report", report)
    return _back("/profile#backup")


# --- history of the CV (also shown in the CV editor) -------------------------------------------------------------


def _history_items() -> list[dict]:
    return [{**h, **history.summary(h["id"])} for h in history.list_snapshots()]


@router.get("/editor/api/history")
def history_list():
    return {"items": _history_items()}


@router.post("/editor/api/history/{snap_id}/restore")
def history_restore_api(snap_id: str):
    try:
        history.restore(snap_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"ok": True}


@router.post("/history/{snap_id}/restore")
def history_restore(snap_id: str):
    try:
        history.restore(snap_id)
    except ValueError as e:
        return _back("/profile?backup_error=" + quote(str(e)) + "#history")
    return _back("/profile?restored=1#history")


# --- debug: start over -----------------------------------------------------------------------------------------

# What "clear my profile" removes: the CV and what is built from it. Jobs, applications, made CVs/letters, searches,
# role profiles and history stay. A backup zip of everything is written first.
CLEAR_FILES = ["profile.json", "photo.png", "editor.json", "stories.json"]
CLEAR_DIRS = ["editor_uploads"]


@router.post("/profile/clear")
def profile_clear():
    history.snapshot("Before clearing my profile", force=True)
    made = backup.make("before-clear")
    root = settings.DATA_DIR
    for name in CLEAR_FILES:
        (root / name).unlink(missing_ok=True)
    for name in CLEAR_DIRS:
        shutil.rmtree(root / name, ignore_errors=True)
    db.notification_add("task", f"Profile cleared{' for ' + people.current_person().name if not people.is_default() else ''}",
                        f"A backup was saved first: {made.name} (My profile → History & backup to restore it).", "/profile#safety")
    return _back("/profile#start")
