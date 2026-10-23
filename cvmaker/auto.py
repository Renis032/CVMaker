"""Automatic searches: re-run the saved search every few hours and remember what was new.

Runs inside the web app while it is open. For searches while it is closed, run `python -m cvmaker.auto`
from cron or a systemd timer.
"""

import shutil
import subprocess
import threading
import time

from pydantic import BaseModel

from . import assistant, settings
from .models import Criteria
from .search import run_search

AUTO_PATH = settings.DATA_DIR / "auto.json"
CRITERIA_PATH = settings.DATA_DIR / "criteria.json"
CHECK_EVERY_SECONDS = 60


class AutoState(BaseModel):
    enabled: bool = False
    every_hours: float = 6
    notify: bool = True
    last_run: float = 0
    last_seconds: float = 0
    last_found: int = 0
    last_new: int = 0
    last_errors: list[str] = []
    running: bool = False


def load_state() -> AutoState:
    if AUTO_PATH.exists():
        return AutoState.model_validate_json(AUTO_PATH.read_text())
    return AutoState()


def save_state(state: AutoState) -> None:
    AUTO_PATH.write_text(state.model_dump_json(indent=2))


def load_criteria() -> Criteria:
    if CRITERIA_PATH.exists():
        return Criteria.model_validate_json(CRITERIA_PATH.read_text())
    return Criteria()


def next_run(state: AutoState) -> float:
    return state.last_run + state.every_hours * 3600


def _notify(new: int) -> None:
    if new and shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "CVMaker", f"{new} new job{'s' if new != 1 else ''} found - open http://127.0.0.1:8000/?unseen=1"],
            check=False,
        )


def run_once() -> AutoState:
    state = load_state()
    state.running = True
    save_state(state)
    started = time.time()
    try:
        result = run_search(load_criteria(), assistant.load_profile())
        state.last_found, state.last_new, state.last_errors = len(result.job_ids), len(result.new_ids), result.errors
    except Exception as e:
        state.last_errors = [f"search failed: {e}"]
    state = load_state().model_copy(
        update={
            "last_run": started,
            "last_seconds": round(time.time() - started, 1),
            "last_found": state.last_found,
            "last_new": state.last_new,
            "last_errors": state.last_errors,
            "running": False,
        }
    )
    save_state(state)
    if state.notify:
        _notify(state.last_new)
    return state


def _loop() -> None:
    while True:
        time.sleep(CHECK_EVERY_SECONDS)
        state = load_state()
        if state.enabled and not state.running and time.time() >= next_run(state):
            run_once()


_thread: threading.Thread | None = None


def start() -> None:
    """Start the background scheduler (once per process)."""
    global _thread
    state = load_state()
    if state.running:  # left over from a run the app was stopped in the middle of
        save_state(state.model_copy(update={"running": False}))
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="cvmaker-auto-search", daemon=True)
        _thread.start()


if __name__ == "__main__":
    s = run_once()
    print(f"{s.last_found} matching jobs, {s.last_new} new, in {s.last_seconds}s")
    for e in s.last_errors:
        print("error:", e)
