"""AI usage limits, and background tasks surviving a restart."""

import time

import pytest
from conftest import new_user

from cvmaker import central, db, llm, people, settings, tasks, usage


def test_free_allowance_is_enforced(monkeypatch):
    _, account = new_user()
    monkeypatch.setitem(settings.PLAN_TOKENS, "free", 1000)
    with people.in_account(account["id"]):
        usage.check()  # nothing used yet
        usage.record(800, 300)
        with pytest.raises(usage.LimitReached):
            usage.check()
        assert usage.this_month()["used"] == 1100


def test_own_key_is_not_limited(monkeypatch):
    from cvmaker import keys

    _, account = new_user()
    monkeypatch.setitem(settings.PLAN_TOKENS, "free", 10)
    with people.in_account(account["id"]):
        keys.set_many({"anthropic": "sk-ant-own-key-123456789"})
        usage.record(5000, 5000)
        usage.check()


def test_rate_limit_per_minute(monkeypatch):
    _, account = new_user()
    monkeypatch.setattr(settings, "AI_CALLS_PER_MINUTE", 2)
    with people.in_account(account["id"]):
        usage.check()
        usage.check()
        with pytest.raises(usage.LimitReached):
            usage.check()


def test_no_key_gives_a_clear_message(monkeypatch):
    _, account = new_user()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    from pydantic import BaseModel

    class Out(BaseModel):
        x: str

    with people.in_account(account["id"]):
        with pytest.raises(llm.LLMError, match="not configured|API key"):
            llm.ask("s", "t", Out)


ran: list[tuple[str, str, int]] = []


@tasks.runner("test-echo")
def _echo(progress, n: int) -> str:
    ran.append((people.account(), people.current(), n))
    return f"/done/{n}"


def _queued(task_id: int) -> dict | None:
    with central.connect() as c:
        return c.one("SELECT * FROM queue WHERE task_id = ? AND status IN ('queued', 'running') ORDER BY id DESC", (task_id,))


def test_task_survives_restart_and_runs_for_the_right_account():
    _, account = new_user()
    with people.in_account(account["id"]):
        task_id = tasks.submit("test", "Echo", "test-echo", args={"n": 7})
    # "restart": the start-up pass must not mark a queued task as interrupted
    tasks.start()
    with people.in_account(account["id"]):
        assert db.task_get(task_id)["status"] == "queued"
    while tasks.work_one():
        pass
    with people.in_account(account["id"]):
        task = db.task_get(task_id)
    assert task["status"] == "done" and task["url"] == "/done/7"
    assert (account["id"], people.DEFAULT, 7) in ran


def test_task_of_a_dead_worker_is_picked_up_again():
    _, account = new_user()
    with people.in_account(account["id"]):
        task_id = tasks.submit("test", "Echo", "test-echo", args={"n": 8})
    # a worker claims it, then dies (its lease runs out)
    item = tasks._claim()
    assert item["task_id"] == task_id
    with central.connect() as c:
        c.execute("UPDATE queue SET lease_until = ? WHERE id = ?", (time.time() - 1, item["id"]))
    while tasks.work_one():
        pass
    with people.in_account(account["id"]):
        assert db.task_get(task_id)["status"] == "done"
    assert _queued(task_id) is None


def test_unknown_runner_is_refused():
    _, account = new_user()
    with people.in_account(account["id"]), pytest.raises(KeyError):
        tasks.submit("x", "x", "no-such-runner")


def test_every_submitted_runner_exists():
    """Every tasks.submit(...) in the code names a registered runner."""
    import re
    from pathlib import Path

    import cvmaker.app  # noqa: F401 - registers the runners

    names = set()
    for path in Path(cvmaker.app.__file__).parent.glob("*.py"):
        text = path.read_text()
        for m in re.finditer(r"tasks\.submit\(\s*[^,]+,\s*[^,]+?,\s*\"([\w-]+)\"", text, re.S):
            names.add(m.group(1))
    assert names, "found no task submissions"
    missing = names - set(tasks._RUNNERS)
    assert not missing, missing


def test_reminders_are_emailed_in_server_mode():
    from conftest import outbox

    _, account = new_user()
    with people.in_account(account["id"]):
        tasks.notify("Time to follow up on 2 applications", "Acme, Globex", url="/pipeline#follow-ups", email=True)
    mails = [m for m in outbox() if m["to"] == account["email"] and "follow up" in m["subject"]]
    assert mails and "http://testserver/pipeline#follow-ups" in mails[-1]["text"]


def test_the_worker_process_knows_every_runner(tmp_path):
    """`python -m cvmaker.worker` runs the queue in server deployments, in a process that never serves a page: it
    must still register every runner, or the tasks the web process queues fail there as unknown."""
    import os
    import re
    import subprocess
    import sys
    from pathlib import Path

    import cvmaker

    submitted = set()
    for path in Path(cvmaker.__file__).parent.glob("*.py"):
        submitted |= set(re.findall(r"tasks\.submit\(\s*[^,]+,\s*[^,]+?,\s*\"([\w-]+)\"", path.read_text(), re.S))
    env = {**os.environ, "CVMAKER_DATA": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path / 'central.db'}"}
    out = subprocess.run(
        [sys.executable, "-c", "import cvmaker.worker, cvmaker.tasks as t; print(' '.join(sorted(t._RUNNERS)))"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    known = set(out.stdout.split())
    assert submitted and not submitted - known, submitted - known
