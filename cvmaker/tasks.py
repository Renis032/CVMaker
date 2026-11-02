"""Background tasks: searches and Claude calls run here, so a page never waits minutes for a response.

A task is a function `fn(progress) -> url | None`. It runs on a small thread pool; its state lives in the
`tasks` table so every page can show what is running and link to the result. The URL it returns is
where the result is shown. Tasks do not survive a restart of the app (they are marked interrupted).
"""

import contextvars
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from . import db, people, settings

_pool = ThreadPoolExecutor(max_workers=settings.TASK_WORKERS, thread_name_prefix="cvmaker-task")

# Tasks that take at least this long also send a desktop notification when they finish.
NOTIFY_AFTER_SECONDS = 45


class Progress:
    """Handed to a task so it can report progress shown live on the page."""

    def __init__(self, task_id: int):
        self.task_id = task_id
        self.data: dict = {}

    def set(self, **values) -> None:
        self.data.update(values)
        db.task_update(self.task_id, progress=self.data)


def submit(
    kind: str,
    label: str,
    fn: Callable[[Progress], str | None],
    subject: str = "",
    job_id: str = "",
    url: str = "",
) -> int:
    """Queue a task; if the same kind of task for the same subject is already queued or running, reuse it."""
    if subject:
        existing = db.task_active(kind, subject)
        if existing:
            return existing[0]["id"]
    task_id = db.task_create(kind, label, subject, job_id, url)
    _pool.submit(contextvars.copy_context().run, _run, task_id, fn)
    return task_id


def _run(task_id: int, fn: Callable[[Progress], str | None]) -> None:
    started = time.time()
    db.task_update(task_id, status="running", started=started)
    try:
        url = fn(Progress(task_id))
        fields = {"status": "done", "finished": time.time()}
        if url:
            fields["url"] = url
        db.task_update(task_id, **fields)
    except Exception as e:  # shown on the page; the task list is where errors surface
        db.task_update(task_id, status="error", error=f"{e}"[:1500] or type(e).__name__, finished=time.time())
    if time.time() - started >= NOTIFY_AFTER_SECONDS:
        task = db.task_get(task_id)
        verb = "finished" if task["status"] == "done" else "failed"
        notify(f"{person_prefix()}{task['label']} {verb}")


def person_prefix() -> str:
    """'Anna: ' when working for someone other than the default person, so notifications say whose they are."""
    return "" if people.is_default() else f"{people.current_person().name}: "


def notify(message: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "CVMaker", message], check=False)


def start() -> None:
    for person in people.load():
        with people.acting_as(person.id):
            db.tasks_interrupt_stale()
