"""The chat page: ask Claude about your profile or a job, and apply profile changes it proposes.
Included by app.py; the page helpers come from web.py."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.datastructures import UploadFile as StarletteUpload

from . import assistant, chat, db, tasks
from .web import _back, page

router = APIRouter()

# Messages loaded at a time: the page opens with the latest ones, earlier ones load when you scroll up.
PAGE_MESSAGES = 20

STARTERS_GENERAL = [
    "What are the weakest parts of my CV, and how would you fix them?",
    "Rewrite my summary so it's shorter and more concrete.",
    "Add a skill to my profile: ",
    "Which kinds of roles am I the strongest fit for right now?",
]
STARTERS_JOB = [
    "Should I apply for this job? Be honest.",
    "What will they most likely ask me in the interview?",
    "Which of my projects should I talk about for this role?",
    "How do I explain the gaps for this job in my cover letter?",
]


def _job_options() -> list[dict]:
    """Jobs worth chatting about: everything you liked or applied to, newest activity first."""
    rows = db.list_jobs(statuses=db.STAGES, closed=None)
    return sorted(rows, key=lambda r: r["status_at"] or r["first_seen"], reverse=True)[:80]


async def _read(request: Request) -> list[tuple[str, bytes]]:
    """The attached files. Read from the form directly: a browser sends an empty file field as a nameless part,
    which FastAPI's typed File() parameter rejects."""
    form = await request.form()
    return [(f.filename, await f.read()) for f in form.getlist("files") if isinstance(f, StarletteUpload) and f.filename]


def _send(thread_id: int, text: str, uploads: list[tuple[str, bytes]]) -> None:
    text = text.strip() or "Have a look at the attached file" + ("s." if len(uploads) > 1 else ".")
    message_id = db.chat_add(thread_id, "user", text)
    if uploads:
        chat.save_files(thread_id, message_id, uploads)
    tasks.submit("chat", "Chat · " + chat.title_from(text), "chat",
                 subject=f"chat:{thread_id}", url=f"/chat/{thread_id}", args={"thread_id": thread_id})


@tasks.runner("chat")
def _run(progress, thread_id: int) -> str:
    progress.set(step="Claude is thinking")
    chat.answer(thread_id)
    return f"/chat/{thread_id}"


@router.get("/chat", response_class=HTMLResponse)
def chat_home(request: Request, job: str = "", error: str = ""):
    return _page(request, None, job, error)


@router.get("/chat/{thread_id:int}", response_class=HTMLResponse)
def chat_thread(request: Request, thread_id: int, error: str = "", show: int = 0):
    thread = db.chat_thread_get(thread_id)
    if not thread:
        return page(request, "error.html", message="Chat not found")
    return _page(request, thread, thread["job_id"], error, show)


@router.get("/chat/{thread_id:int}/older", response_class=HTMLResponse)
def chat_older(request: Request, thread_id: int, before: int):
    """The page of messages before `before`, as an HTML fragment for the chat page to insert."""
    messages, more = db.chat_messages_page(thread_id, PAGE_MESSAGES, before=before)
    return page(request, "_chat_msgs.html", messages=_for_page(messages), more=more)


def _for_page(messages: list[dict]) -> list[dict]:
    for m in messages:
        m["html"] = chat.to_html(m["text"])
        m["diff"] = chat.diff(m) if m["proposal"] else []
    return messages


def _page(request: Request, thread: dict | None, job_id: str, error: str = "", show: int = 0):
    found = db.get_job(job_id) if job_id else None
    job = found[0] if found else None
    messages, more = db.chat_messages_page(thread["id"], PAGE_MESSAGES, include=show) if thread else ([], False)
    running = db.task_active("chat", f"chat:{thread['id']}") if thread else []
    return page(
        request, "chat.html", thread=thread, job=job, messages=_for_page(messages), more=more, running=running[0] if running else None,
        threads=db.chat_threads(), job_options=_job_options(), error=error,
        starters=STARTERS_JOB if job else STARTERS_GENERAL,
        file_types=sorted(chat.FILE_TYPES), max_files=chat.MAX_FILES_PER_MESSAGE, max_mb=chat.MAX_FILE_MB, has_profile=assistant.load_profile() is not None,
    )


@router.post("/chat/new")
async def chat_new(request: Request, text: str = Form(""), job_id: str = Form("")):
    uploads = await _read(request)
    back = "/chat" + (f"?job={quote(job_id)}" if job_id else "")
    if not text.strip() and not uploads:
        return _back(back)
    try:
        chat.check_files(uploads)
    except chat.AttachmentError as e:
        return _back(back + ("&" if "?" in back else "?") + "error=" + quote(str(e)))
    job_id = job_id if job_id and db.get_job(job_id) else ""
    thread_id = db.chat_thread_add(chat.title_from(text or uploads[0][0]), job_id)
    _send(thread_id, text, uploads)
    return _back(f"/chat/{thread_id}")


@router.post("/chat/{thread_id:int}/send")
async def chat_send(request: Request, thread_id: int, text: str = Form("")):
    uploads = await _read(request)
    if not db.chat_thread_get(thread_id):
        return _back("/chat")
    if db.task_active("chat", f"chat:{thread_id}"):
        return _back(f"/chat/{thread_id}?error=" + quote("Wait for Claude's answer before sending the next message."))
    try:
        chat.check_files(uploads)
    except chat.AttachmentError as e:
        return _back(f"/chat/{thread_id}?error=" + quote(str(e)))
    if text.strip() or uploads:
        _send(thread_id, text, uploads)
    return _back(f"/chat/{thread_id}#end")


@router.get("/chat/file/{message_id:int}/{index:int}")
def chat_file(request: Request, message_id: int, index: int):
    message = db.chat_message(message_id)
    path = chat.file_path(message, index) if message else None
    if not path:
        return page(request, "error.html", message="File not found")
    name = message["files"][index]["name"]
    # Only pictures, PDFs and plain text open in the browser; anything else downloads. Nothing runs as this site.
    inline = Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md"}
    media = "text/plain; charset=utf-8" if Path(name).suffix.lower() in {".txt", ".md"} else None
    headers = {"X-Content-Type-Options": "nosniff"}
    if Path(name).suffix.lower() != ".pdf":  # Chrome's PDF viewer doesn't open in a sandboxed page
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return FileResponse(path, filename=name, media_type=media, content_disposition_type="inline" if inline else "attachment",
                        headers=headers)


@router.post("/chat/{thread_id:int}/job")
def chat_set_job(thread_id: int, job_id: str = Form("")):
    if db.chat_thread_get(thread_id):
        db.chat_thread_update(thread_id, job_id=job_id if job_id and db.get_job(job_id) else "")
    return _back(f"/chat/{thread_id}")


@router.post("/chat/{thread_id:int}/delete")
def chat_delete(thread_id: int):
    db.chat_thread_delete(thread_id)
    chat.delete_files(thread_id)
    return _back("/chat")


def _change(message_id: int, action) -> object:
    message = db.chat_message(message_id)
    if not message:
        return _back("/chat")
    problem = action(message)
    back = f"/chat/{message['thread_id']}?show={message_id}"
    return _back(back + (f"&error={quote(problem)}" if problem else "") + f"#m{message_id}")


@router.post("/chat/msg/{message_id:int}/apply")
def chat_apply(message_id: int):
    return _change(message_id, chat.apply)


@router.post("/chat/msg/{message_id:int}/undo")
def chat_undo(message_id: int):
    return _change(message_id, chat.undo)


@router.post("/chat/msg/{message_id:int}/dismiss")
def chat_dismiss(message_id: int):
    def dismiss(message):
        if message["state"] == "proposed":
            db.chat_set_state(message["id"], "dismissed")
        return ""
    return _change(message_id, dismiss)
