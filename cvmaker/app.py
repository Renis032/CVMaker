"""The web app: start-up, the request guard, and the routes, which live in the *_routes.py modules.

Importing this module registers every route and every background-task runner (the worker imports it for that)."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response

from . import auto, db, people, security, settings, taste, tasks, telegram
from .llm import LLMError
from .web import page


@asynccontextmanager
async def lifespan(app: FastAPI):
    for account_id in people.all_accounts():
        with people.in_account(account_id):
            for person in people.load():
                with people.acting_as(person.id):
                    db.backfill()
                    taste.recompute()
    tasks.start()
    auto.start()
    telegram.start()
    yield


app = FastAPI(title="CVMaker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Host and CSRF checks, login (server mode), then everything a request does - and the background tasks it
    starts - works on the logged-in account's and the selected person's data (see security.py)."""
    return await security.guard(request, call_next)


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


# --- background tasks API (polled by every page) ----------------------------------------------------


def _task_json(t: dict) -> dict:
    keys = ("id", "kind", "label", "subject", "job_id", "url", "status", "progress", "error", "created", "started", "finished")
    return {k: t[k] for k in keys}


@app.get("/api/tasks")
def api_tasks():
    return {
        "now": time.time(),
        "active": [_task_json(t) for t in db.task_active()],
        "finished": [_task_json(t) for t in db.tasks_unseen_finished()],
        "unread": db.notifications_unread(),
    }


@app.post("/api/tasks/{task_id}/seen")
def api_task_seen(task_id: int):
    db.task_update(task_id, seen=1)
    return {"ok": True}


# Routes, in the order requests are matched.
from .jobs_routes import router as _jobs_router  # noqa: E402
from .job_routes import router as _job_router  # noqa: E402
from .cv_routes import router as _cv_router  # noqa: E402
from .profile_routes import router as _profile_router  # noqa: E402
from .companies_routes import router as _companies_router  # noqa: E402
from .stage_routes import router as _stage_router  # noqa: E402
from .chat_routes import router as _chat_router  # noqa: E402
from .editor_routes import router as _editor_router  # noqa: E402
from .extras_routes import router as _extras_router  # noqa: E402
from .auth_routes import router as _auth_router  # noqa: E402

for _router in (_jobs_router, _job_router, _cv_router, _profile_router, _companies_router, _stage_router, _chat_router,
                _editor_router, _extras_router, _auth_router):
    app.include_router(_router)
if settings.DESKTOP:
    from .telegram_routes import router as _telegram_router  # noqa: E402

    app.include_router(_telegram_router)
