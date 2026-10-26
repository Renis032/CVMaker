"""Saved searches, their schedules, follow-up reminders, and Claude's first-pass fit rating of new jobs.

Each saved search can re-run itself every few hours while the web app is open. For searches while it is
closed, run `python -m cvmaker.auto` from cron or a systemd timer.
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from . import assistant, db, settings, tasks
from .models import Criteria
from .search import run_search
from .sources import ensure_description

SEARCHES_PATH = settings.DATA_DIR / "searches.json"
PREFS_PATH = settings.DATA_DIR / "prefs.json"
# Files of the single saved search before there were several; read once to migrate.
OLD_CRITERIA_PATH = settings.DATA_DIR / "criteria.json"
OLD_AUTO_PATH = settings.DATA_DIR / "auto.json"
CHECK_EVERY_SECONDS = 60
RATE_BATCH = 5
RATE_PARALLEL = 3


class SavedSearch(BaseModel):
    id: str
    name: str
    criteria: Criteria = Field(default_factory=Criteria)
    auto: bool = False
    every_hours: float = 6
    notify: bool = True
    last_run: float = 0
    last_seconds: float = 0
    last_found: int = 0
    last_new: int = 0
    last_errors: list[str] = []
    last_counts: dict[str, int] = {}
    last_job_ids: list[str] = []
    last_new_ids: list[str] = []


class Prefs(BaseModel):
    active_search: str = "default"
    rate_after_search: bool = True
    rate_top: int = 40


class SearchList(BaseModel):
    searches: list[SavedSearch]


_lock = threading.RLock()


def load_prefs() -> Prefs:
    return Prefs.model_validate_json(PREFS_PATH.read_text()) if PREFS_PATH.exists() else Prefs()


def save_prefs(prefs: Prefs) -> None:
    PREFS_PATH.write_text(prefs.model_dump_json(indent=2))


def _migrate() -> list[SavedSearch]:
    search = SavedSearch(id="default", name="My search")
    if OLD_CRITERIA_PATH.exists():
        search.criteria = Criteria.model_validate_json(OLD_CRITERIA_PATH.read_text())
    if OLD_AUTO_PATH.exists():
        data = json.loads(OLD_AUTO_PATH.read_text())
        search.auto, search.every_hours, search.notify = data.get("enabled", False), data.get("every_hours", 6), data.get("notify", True)
        search.last_run, search.last_seconds = data.get("last_run", 0), data.get("last_seconds", 0)
        search.last_found, search.last_new = data.get("last_found", 0), data.get("last_new", 0)
    return [search]


def load_searches() -> list[SavedSearch]:
    with _lock:
        if not SEARCHES_PATH.exists():
            _write(_migrate())
        return SearchList.model_validate_json(SEARCHES_PATH.read_text()).searches


def _write(searches: list[SavedSearch]) -> None:
    SEARCHES_PATH.write_text(SearchList(searches=searches).model_dump_json(indent=2))


def get_search(search_id: str) -> SavedSearch | None:
    return next((s for s in load_searches() if s.id == search_id), None)


def active_search() -> SavedSearch:
    searches = load_searches()
    wanted = load_prefs().active_search
    return next((s for s in searches if s.id == wanted), searches[0])


def load_criteria() -> Criteria:
    """The criteria of the search you used last (its skills also count as keywords when rating CVs)."""
    return active_search().criteria


def save_search(search: SavedSearch) -> SavedSearch:
    """Insert or update by id (a new id is made from the name when empty)."""
    with _lock:
        searches = load_searches()
        if not search.id:
            base = re.sub(r"[^a-z0-9]+", "-", search.name.lower()).strip("-") or "search"
            taken = {s.id for s in searches}
            search.id = next(f"{base}-{n}" if n else base for n in range(100) if (f"{base}-{n}" if n else base) not in taken)
        for i, s in enumerate(searches):
            if s.id == search.id:
                searches[i] = search
                break
        else:
            searches.append(search)
        _write(searches)
        return search


def update_search(search_id: str, **fields) -> None:
    with _lock:
        searches = load_searches()
        for i, s in enumerate(searches):
            if s.id == search_id:
                searches[i] = s.model_copy(update=fields)
        _write(searches)


def delete_search(search_id: str) -> None:
    with _lock:
        searches = [s for s in load_searches() if s.id != search_id]
        if searches:
            _write(searches)


def next_run(search: SavedSearch) -> float:
    return search.last_run + search.every_hours * 3600


def search_running(search_id: str) -> dict | None:
    active = db.task_active("search", f"search:{search_id}")
    return active[0] if active else None


# --- running a search ------------------------------------------------------------------------------


def run_saved(
    search_id: str, progress: tasks.Progress | None = None, scheduled: bool = False, rate_inline: bool = False
) -> SavedSearch:
    """Run one saved search and remember its results. New jobs are then rated by Claude: as a separate
    background task, or right here when rate_inline (from cron, where there is no task pool to outlive us)."""
    search = get_search(search_id)
    started = time.time()
    on_progress = (lambda sources: progress.set(sources=sources)) if progress else None
    errors, found, new_ids, counts = [], [], [], {}
    try:
        result = run_search(search.criteria, assistant.load_profile(), on_progress)
        errors, found, new_ids, counts = result.errors, result.job_ids, result.new_ids, result.counts
    except Exception as e:
        errors = [f"search failed: {e}"]
    update_search(
        search_id,
        last_run=started,
        last_seconds=round(time.time() - started, 1),
        last_found=len(found),
        last_new=len(new_ids),
        last_errors=errors,
        last_counts=counts,
        last_job_ids=found,
        last_new_ids=new_ids,
    )
    if scheduled and search.notify and new_ids:
        tasks.notify(f"{search.name}: {len(new_ids)} new job{'s' if len(new_ids) != 1 else ''} - http://127.0.0.1:8000/?unseen=1")
    prefs = load_prefs()
    if prefs.rate_after_search and new_ids:
        top = _top_by_score(new_ids, prefs.rate_top)
        if rate_inline:
            rate_jobs(top)
        else:
            submit_rating(top, f"Rate new jobs from '{search.name}'")
    return get_search(search_id)


def submit_search(search_id: str, scheduled: bool = False) -> int:
    search = get_search(search_id)
    return tasks.submit(
        "search",
        f"{'Scheduled search' if scheduled else 'Search'}: {search.name}",
        lambda progress: (run_saved(search_id, progress, scheduled), f"/?search={search_id}&only_last=1")[1],
        subject=f"search:{search_id}",
        url=f"/?search={search_id}&only_last=1",
    )


# --- Claude's first-pass fit rating --------------------------------------------------------------------


def _top_by_score(job_ids: list[str], limit: int) -> list[str]:
    rows = db.list_jobs(ids=job_ids, sort="score")
    return db.unrated([r["job"].id for r in rows])[:limit]


def rate_jobs(job_ids: list[str], progress: tasks.Progress | None = None) -> int:
    """Rate these jobs in batches, several batches at once. Returns how many got a rating."""
    profile = assistant.load_profile()
    if not profile or not job_ids:
        return 0
    batches = [job_ids[i : i + RATE_BATCH] for i in range(0, len(job_ids), RATE_BATCH)]
    done = 0
    errors: list[str] = []
    if progress:
        progress.set(done=0, total=len(job_ids))

    def rate_batch(ids: list[str]) -> int:
        jobs = []
        for job_id in ids:
            found = db.get_job(job_id)
            if not found:
                continue
            try:
                jobs.append(ensure_description(found[0]))
            except Exception:
                jobs.append(found[0])
        by_id = {j.id: j for j in jobs}
        rated = 0
        for fit in assistant.quick_fit(profile, jobs).fits:
            if fit.job_id in by_id:
                rating = max(1, min(10, fit.rating))
                db.set_fit(fit.job_id, rating, {"strong": fit.strong, "missing": fit.missing, "verdict": fit.verdict, "quick": True})
                rated += 1
        return rated

    with ThreadPoolExecutor(max_workers=RATE_PARALLEL) as pool:
        for future in [pool.submit(rate_batch, b) for b in batches]:
            try:
                done += future.result()
            except Exception as e:
                errors.append(str(e)[:300])
            if progress:
                progress.set(done=done, total=len(job_ids), errors=errors)
    if errors and not done:
        raise RuntimeError(errors[0])
    return done


def submit_rating(job_ids: list[str], label: str) -> int | None:
    if not job_ids:
        return None
    return tasks.submit(
        "rate",
        f"{label} ({len(job_ids)} job{'s' if len(job_ids) != 1 else ''})",
        lambda progress: (rate_jobs(job_ids, progress), "/?sort=fit")[1],
        subject=f"rate:{','.join(sorted(job_ids))[:200]}",
        url="/?sort=fit",
    )


# --- follow-up reminders and the scheduler ----------------------------------------------------------------


def remind_follow_ups() -> None:
    due = db.due_follow_ups(only_unreminded=True)
    if not due:
        return
    names = ", ".join(f"{d['job'].company}" for d in due[:3]) + (" …" if len(due) > 3 else "")
    tasks.notify(f"Time to follow up on {len(due)} application{'s' if len(due) != 1 else ''}: {names} - http://127.0.0.1:8000/pipeline")
    db.mark_reminded([d["job"].id for d in due])


def _loop() -> None:
    while True:
        time.sleep(CHECK_EVERY_SECONDS)
        try:
            for s in load_searches():
                if s.auto and time.time() >= next_run(s) and not search_running(s.id):
                    submit_search(s.id, scheduled=True)
            remind_follow_ups()
        except Exception as e:  # keep the scheduler alive; the next tick tries again
            print(f"cvmaker scheduler: {e}")


_thread: threading.Thread | None = None


def start() -> None:
    """Start the background scheduler (once per process)."""
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="cvmaker-auto-search", daemon=True)
        _thread.start()


if __name__ == "__main__":
    # From cron: run every search that has its schedule on (or the active one if none has).
    for chosen in [s for s in load_searches() if s.auto] or [active_search()]:
        done = run_saved(chosen.id, scheduled=True, rate_inline=True)
        print(f"{done.name}: {done.last_found} matching jobs, {done.last_new} new, in {done.last_seconds}s")
        for e in done.last_errors:
            print("error:", e)
    remind_follow_ups()
