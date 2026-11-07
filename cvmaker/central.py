"""The central database: accounts, sessions, email tokens, AI usage, rate limits and the background task queue.

Each person's own data (jobs, CVs, letters, ...) stays in their own SQLite file inside their folder (db.py). This
database is shared by the whole installation: SQLite by default (desktop, small servers), Postgres when DATABASE_URL
is a postgresql:// URL (needs the "server" extra). Queries are written with ? placeholders and translated.
"""

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    google_sub TEXT,
    plan TEXT NOT NULL DEFAULT 'free',
    plan_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    stripe_customer TEXT NOT NULL DEFAULT '',
    stripe_subscription TEXT NOT NULL DEFAULT '',
    created DOUBLE PRECISION NOT NULL,
    deleted DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_account ON sessions (account_id);
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires DOUBLE PRECISION NOT NULL,
    used DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    account_id TEXT NOT NULL,
    month TEXT NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    own_key_calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, month)
);
CREATE TABLE IF NOT EXISTS rate (
    key TEXT NOT NULL,
    window_start DOUBLE PRECISION NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, window_start)
);
CREATE TABLE IF NOT EXISTS queue (
    id {serial},
    account_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    runner TEXT NOT NULL,
    args TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS queue_status ON queue (status, lease_until);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_lock = threading.Lock()
_ready: set[str] = set()


def is_postgres() -> bool:
    return settings.DATABASE_URL.startswith(("postgres://", "postgresql://"))


class _Conn:
    """A thin wrapper so callers write SQLite-style SQL (? placeholders) and get dict rows from either database."""

    def __init__(self, raw, pg: bool):
        self.raw = raw
        self.pg = pg

    def execute(self, sql: str, params: tuple | list = ()):
        if self.pg:
            sql = sql.replace("?", "%s")
        cur = self.raw.cursor() if self.pg else self.raw
        result = cur.execute(sql, tuple(params))
        return cur if self.pg else result

    def all(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None


def _sqlite_path() -> Path:
    return Path(settings.DATABASE_URL.removeprefix("sqlite:///"))


def _open():
    if is_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:  # pragma: no cover - depends on the install
            raise RuntimeError("DATABASE_URL is Postgres: install the server extra (pip install 'cvmaker[server]')") from e
        return _Conn(psycopg.connect(settings.DATABASE_URL, row_factory=dict_row), True)
    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, timeout=30)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    return _Conn(raw, False)


def _migrate(c: _Conn) -> None:
    serial = "BIGSERIAL PRIMARY KEY" if c.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    if c.pg:
        # Several processes start at once (web workers, task workers): one creates the tables, the others wait.
        c.execute("SELECT pg_advisory_xact_lock(724119)")
    for statement in _SCHEMA.format(serial=serial).split(";"):
        if statement.strip():
            c.execute(statement)


@contextmanager
def connect() -> Iterator[_Conn]:
    c = _open()
    try:
        if settings.DATABASE_URL not in _ready:
            with _lock:
                if settings.DATABASE_URL not in _ready:
                    _migrate(c)
                    c.raw.commit()
                    _ready.add(settings.DATABASE_URL)
        yield c
        c.raw.commit()
    except BaseException:
        c.raw.rollback()
        raise
    finally:
        c.raw.close()


# --- rate limits -------------------------------------------------------------------------------------------------


def hit(key: str, limit: int, window_seconds: float) -> bool:
    """Count one attempt for `key` in the current window; False when the limit is already reached."""
    start = float(int(time.time() // window_seconds) * window_seconds)
    with connect() as c:
        c.execute("DELETE FROM rate WHERE window_start < ?", (start - 24 * 3600,))
        row = c.one("SELECT hits FROM rate WHERE key = ? AND window_start = ?", (key, start))
        if row and row["hits"] >= limit:
            return False
        if row:
            c.execute("UPDATE rate SET hits = hits + 1 WHERE key = ? AND window_start = ?", (key, start))
        else:
            c.execute("INSERT INTO rate (key, window_start, hits) VALUES (?, ?, 1)", (key, start))
    return True


def get_meta(key: str) -> str | None:
    with connect() as c:
        row = c.one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with connect() as c:
        if c.one("SELECT 1 AS x FROM meta WHERE key = ?", (key,)):
            c.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
        else:
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
