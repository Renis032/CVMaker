"""The per-person database: a fresh one, one from before versioning and one from an older version all end up
at the current schema, and a step that fails leaves no half-applied change."""

import sqlite3

import pytest
from conftest import new_user

from cvmaker import db, people, settings


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}


def test_fresh_database(tmp_path):
    conn = _open(tmp_path / "new.db")
    db.migrate(conn)
    assert db._version(conn) == db.SCHEMA_VERSION
    assert {column for table, column, _ in db.MIGRATIONS if table == "jobs"} <= _columns(conn, "jobs")
    assert "jobs_status" in _indexes(conn)
    db.migrate(conn)       # again: nothing to do, nothing breaks
    assert db._version(conn) == db.SCHEMA_VERSION


def test_database_from_before_versioning_keeps_its_rows(tmp_path):
    conn = _open(tmp_path / "old.db")
    # The very first schema: no added columns, no version.
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',"
                 " score INTEGER NOT NULL DEFAULT 0, first_seen REAL NOT NULL, last_seen REAL NOT NULL)")
    conn.execute("INSERT INTO jobs (id, data, first_seen, last_seen) VALUES ('a', '{}', 1, 1)")
    conn.commit()
    assert db._version(conn) == 0
    db.migrate(conn)
    assert db._version(conn) == db.SCHEMA_VERSION
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert (row["id"], row["seen"], row["taste"], row["closed_at"]) == ("a", 1, 0, 0)
    assert conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0     # the other tables exist now


def test_steps_run_once_in_order_and_roll_back_on_failure(tmp_path, monkeypatch):
    conn = _open(tmp_path / "steps.db")
    db.migrate(conn)
    ran = []

    def rename(c):
        ran.append("rename")
        c.execute("ALTER TABLE practice RENAME COLUMN category TO topic")

    def broken(c):
        c.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("step failed")

    monkeypatch.setattr(db, "STEPS", [*db.STEPS, rename, broken, ["CREATE TABLE never (x)"]])
    with pytest.raises(RuntimeError, match="step failed"):
        db.migrate(conn)
    # the step before the broken one stuck; the broken one left nothing; the one after never ran
    assert db._version(conn) == db.SCHEMA_VERSION + 1
    assert "topic" in _columns(conn, "practice")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"half_done", "never"} & tables
    with pytest.raises(RuntimeError):
        db.migrate(conn)
    assert ran == ["rename"]                                                          # not run a second time


def test_connect_migrates_a_file_swapped_in_while_running():
    """Restoring a backup made by an older version replaces the file under a running app."""
    _, account = new_user()
    with people.in_account(account["id"]):
        with db.connect() as c:
            assert db._version(c) == db.SCHEMA_VERSION
        path = settings.DB_PATH
        path.unlink()
        old = _open(path)
        old.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',"
                    " score INTEGER NOT NULL DEFAULT 0, first_seen REAL NOT NULL, last_seen REAL NOT NULL)")
        old.commit()
        old.close()
        with db.connect() as c:
            assert db._version(c) == db.SCHEMA_VERSION and "fit" in _columns(c, "jobs")
