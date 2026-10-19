import json
import sqlite3
import time
from contextlib import contextmanager

from . import settings
from .models import Job, TailoredCV

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    score INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cvs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    data TEXT NOT NULL,
    pdf_path TEXT NOT NULL
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_jobs(jobs: list[Job], scores: dict[str, int]) -> None:
    now = time.time()
    with connect() as c:
        for job in jobs:
            c.execute(
                """INSERT INTO jobs (id, data, score, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data, score=excluded.score, last_seen=excluded.last_seen""",
                (job.id, job.model_dump_json(), scores.get(job.id, 0), now, now),
            )


def get_job(job_id: str) -> tuple[Job, str] | None:
    with connect() as c:
        row = c.execute("SELECT data, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return Job.model_validate_json(row["data"]), row["status"]


def update_job(job: Job) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET data = ? WHERE id = ?", (job.model_dump_json(), job.id))


def set_status(job_id: str, status: str) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def list_jobs(ids: list[str] | None = None, status: str | None = None) -> list[dict]:
    query = "SELECT data, status, score, first_seen FROM jobs"
    args: list = []
    clauses = []
    if ids is not None:
        if not ids:
            return []
        clauses.append(f"id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    if status:
        clauses.append("status = ?")
        args.append(status)
    else:
        clauses.append("status != 'hidden'")
    query += " WHERE " + " AND ".join(clauses) + " ORDER BY score DESC, first_seen DESC"
    with connect() as c:
        rows = c.execute(query, args).fetchall()
    return [
        {"job": Job.model_validate_json(r["data"]), "status": r["status"], "score": r["score"]}
        for r in rows
    ]


def cache_get(key: str, max_age_hours: float):
    with connect() as c:
        row = c.execute("SELECT fetched_at, data FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age_hours * 3600:
        return json.loads(row["data"])
    return None


def cache_put(key: str, data) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, data) VALUES (?, ?, ?)",
            (key, time.time(), json.dumps(data)),
        )


def save_cv(job_id: str, tailored: TailoredCV, pdf_path: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO cvs (job_id, created_at, data, pdf_path) VALUES (?, ?, ?, ?)",
            (job_id, time.time(), tailored.model_dump_json(), pdf_path),
        )
        return cur.lastrowid


def get_cv(cv_id: int) -> tuple[str, TailoredCV, str] | None:
    with connect() as c:
        row = c.execute("SELECT job_id, data, pdf_path FROM cvs WHERE id = ?", (cv_id,)).fetchone()
    if row is None:
        return None
    return row["job_id"], TailoredCV.model_validate_json(row["data"]), row["pdf_path"]


def cvs_for_job(job_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, created_at FROM cvs WHERE job_id = ? ORDER BY created_at DESC", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]
