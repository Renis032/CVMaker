"""Background tasks: searches and Claude calls run here, so a page never waits minutes for a response.

A task is a registered *runner* - a module-level function `fn(progress, **args) -> url | None` - plus JSON
arguments. Submitting one writes it to the queue in the central database (with the account and person it works
for), so it survives a restart: worker threads in the web process (CVMAKER_TASK_WORKERS) or separate worker
processes (`python -m cvmaker.worker`) take it from there. A worker holds a lease on the task and renews it while it
runs; if the worker dies, another one picks the task up again after the lease runs out (up to TASK_MAX_ATTEMPTS).

The task's state for the pages (status, progress, result link, error) lives in the person's own `tasks` table, so
every page can show what is running and link to the result.
"""

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

from . import central, db, people, settings

# Tasks that take at least this long also send a desktop notification when they finish.
NOTIFY_AFTER_SECONDS = 45
LEASE_SECONDS = 180
RENEW_EVERY_SECONDS = 60

Runner = Callable[..., str | None]
_RUNNERS: dict[str, Runner] = {}
_wake = threading.Event()
_workers: list[threading.Thread] = []
_stop = threading.Event()


def runner(name: str) -> Callable[[Runner], Runner]:
    """Register a module-level function as a task runner: `fn(progress, **args)` returning the result's URL."""

    def register(fn: Runner) -> Runner:
        if name in _RUNNERS and _RUNNERS[name] is not fn:
            raise RuntimeError(f"Two task runners are called {name!r}")
        _RUNNERS[name] = fn
        return fn

    return register


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
    fn: str,
    subject: str = "",
    job_id: str = "",
    url: str = "",
    args: dict | None = None,
) -> int:
    """Queue the runner `fn` with `args` for the current account and person; if the same kind of task for the
    same subject is already queued or running, reuse it."""
    if fn not in _RUNNERS:
        raise KeyError(f"No task runner {fn!r}")
    if subject:
        existing = db.task_active(kind, subject)
        if existing:
            return existing[0]["id"]
    task_id = db.task_create(kind, label, subject, job_id, url)
    with central.connect() as c:
        c.execute(
            "INSERT INTO queue (account_id, person_id, task_id, runner, args, created) VALUES (?, ?, ?, ?, ?, ?)",
            (people.account(), people.current(), task_id, fn, json.dumps(args or {}), time.time()),
        )
    _wake.set()
    return task_id


def run_inline(fn: str, args: dict | None = None) -> str | None:
    """Run a runner right here (cron, tests), without the queue."""
    return _RUNNERS[fn](None, **(args or {}))


# --- the queue ----------------------------------------------------------------------------------------------------


def _claim() -> dict | None:
    now = time.time()
    skip = " FOR UPDATE SKIP LOCKED" if central.is_postgres() else ""
    with central.connect() as c:
        return c.one(
            "UPDATE queue SET status = 'running', attempts = attempts + 1, lease_until = ? WHERE id = ("
            " SELECT id FROM queue WHERE status = 'queued' OR (status = 'running' AND lease_until < ?)"
            f" ORDER BY id LIMIT 1{skip}) RETURNING *",
            (now + LEASE_SECONDS, now),
        )


def _renew(queue_id: int) -> None:
    with central.connect() as c:
        c.execute("UPDATE queue SET lease_until = ? WHERE id = ?", (time.time() + LEASE_SECONDS, queue_id))


def _finish(queue_id: int) -> None:
    with central.connect() as c:
        c.execute("DELETE FROM queue WHERE id = ?", (queue_id,))


def work_one() -> bool:
    """Take one task from the queue and run it. False when the queue was empty."""
    item = _claim()
    if not item:
        return False
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(RENEW_EVERY_SECONDS):
            try:
                _renew(item["id"])
            except Exception:
                pass

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        account_id = item["account_id"]
        known = account_id == people.LOCAL or account_id in people.all_accounts()
        if known:
            with people.in_account(account_id, item["person_id"]):
                if item["attempts"] > settings.TASK_MAX_ATTEMPTS:
                    db.task_update(item["task_id"], status="error", finished=time.time(),
                                   error="Stopped after the app restarted several times while it ran. Start it again.")
                else:
                    fn = _RUNNERS.get(item["runner"])
                    if fn is None:
                        db.task_update(item["task_id"], status="error", finished=time.time(), error=f"Unknown task {item['runner']}")
                    else:
                        args = json.loads(item["args"] or "{}")
                        _run(item["task_id"], lambda progress: fn(progress, **args))
    finally:
        stop.set()
        _finish(item["id"])
    return True


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
    task = db.task_get(task_id)
    ok = task["status"] == "done"
    # Every finished task goes in the bell; long ones also pop up on the desktop.
    notify(
        f"{task['label']} {'finished' if ok else 'failed'}",
        "" if ok else (task.get("error") or "")[:300],
        url=task.get("url") or "",
        kind="task" if ok else "error",
        desktop=time.time() - started >= NOTIFY_AFTER_SECONDS,
    )


def _worker_loop() -> None:
    while not _stop.is_set():
        try:
            if work_one():
                continue
        except Exception as e:  # keep the worker alive
            print(f"cvmaker worker: {e}")
        _wake.wait(2)
        _wake.clear()


def start_workers(count: int) -> None:
    for _ in range(count - len(_workers)):
        t = threading.Thread(target=_worker_loop, name=f"cvmaker-task-{len(_workers) + 1}", daemon=True)
        t.start()
        _workers.append(t)


def queued_count() -> int:
    with central.connect() as c:
        return c.one("SELECT COUNT(*) AS n FROM queue")["n"]


# --- notifications ----------------------------------------------------------------------------------------------


def person_prefix() -> str:
    """'Anna: ' when working for someone other than the default person, so notifications say whose they are."""
    return "" if people.is_default() else f"{people.current_person().name}: "


def notify(title: str, body: str = "", url: str = "", kind: str = "info", desktop: bool = True, email: bool = False) -> None:
    """Tell the person: always in the bell (for the current person), as a desktop notification (desktop mode)
    unless desktop=False, and by email to the account's address when email=True (server mode)."""
    try:
        db.notification_add(kind, title, body, url)
    except Exception:  # a notification must never break the work that triggered it
        pass
    if email and settings.SERVER:
        try:
            from . import accounts, mailer

            account = accounts.get(people.account())
            if account and account["email"] and account["verified"]:
                link = f"\n\nOpen it: {mailer.link(url)}" if url.startswith("/") else ""
                mailer.send(account["email"], person_prefix() + title,
                            f"{person_prefix()}{title}{(chr(10) * 2 + body) if body else ''}{link}\n\n"
                            f"You get these emails for scheduled searches and follow-up reminders. Turn a search's notifications off in its settings.")
        except Exception as e:
            print(f"cvmaker: notification email failed: {e}")
    if desktop and settings.DESKTOP and shutil.which("notify-send"):
        text = person_prefix() + title + (f": {body}" if body else "") + (f" - {settings.PUBLIC_URL}{url}" if url.startswith("/") else "")
        subprocess.run(["notify-send", "CVMaker", text], check=False)


def start() -> None:
    """At start-up: tasks a previous process left behind that are not in the queue can never finish; say so.
    Queued ones carry on."""
    with central.connect() as c:
        if settings.DESKTOP:
            # One process on a desktop: whatever was running was cut off by the restart - start it again now
            # instead of waiting for its lease to run out.
            c.execute("UPDATE queue SET status = 'queued', lease_until = 0 WHERE status = 'running'")
        rows = c.all("SELECT account_id, person_id, task_id FROM queue")
    keep: dict[tuple[str, str], set[int]] = {}
    for r in rows:
        keep.setdefault((r["account_id"], r["person_id"]), set()).add(r["task_id"])
    for account_id in people.all_accounts():
        with people.in_account(account_id):
            for person in people.load():
                with people.acting_as(person.id):
                    db.tasks_interrupt_stale(keep.get((account_id, person.id), set()))
    if settings.TASK_WORKERS > 0:
        start_workers(settings.TASK_WORKERS)
