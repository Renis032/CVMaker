import json
import sqlite3
import time
from contextlib import contextmanager

from . import settings
from .models import CoverLetter, Job, TailoredCV

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
CREATE TABLE IF NOT EXISTS letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    data TEXT NOT NULL,
    body TEXT NOT NULL,
    pdf_path TEXT NOT NULL
);
"""

# Jobs that existed before the 'seen' flag count as seen; newly found jobs are inserted unseen.
MIGRATIONS = [("jobs", "seen", "ALTER TABLE jobs ADD COLUMN seen INTEGER NOT NULL DEFAULT 1")]

SORTS = {
    "score": "score DESC, first_seen DESC",
    "newest": "first_seen DESC, score DESC",
    "posted": "json_extract(data, '$.posted_at') DESC, score DESC",
    "salary": "json_extract(data, '$.salary') = '', score DESC",
    "company": "lower(json_extract(data, '$.company')), score DESC",
}


@contextmanager
def connect():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        for table, column, sql in MIGRATIONS:
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(sql)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_jobs(jobs: list[Job], scores: dict[str, int]) -> list[str]:
    """Save search results; returns the ids of jobs never seen before."""
    now = time.time()
    new_ids = []
    with connect() as c:
        for job in jobs:
            row = c.execute("SELECT data FROM jobs WHERE id = ?", (job.id,)).fetchone()
            if row is None:
                new_ids.append(job.id)
                c.execute(
                    "INSERT INTO jobs (id, data, score, first_seen, last_seen, seen) VALUES (?, ?, ?, ?, ?, 0)",
                    (job.id, job.model_dump_json(), scores.get(job.id, 0), now, now),
                )
                continue
            # Keep the full description fetched earlier for sources that only list titles.
            old = Job.model_validate_json(row["data"])
            if len(old.description) > len(job.description):
                job.description = old.description
            job.salary = job.salary or old.salary
            c.execute(
                "UPDATE jobs SET data = ?, score = ?, last_seen = ? WHERE id = ?",
                (job.model_dump_json(), scores.get(job.id, 0), now, job.id),
            )
    return new_ids


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


def list_jobs(
    ids: list[str] | None = None,
    status: str | None = None,
    unseen: bool = False,
    with_salary: bool = False,
    sort: str = "score",
) -> list[dict]:
    query = "SELECT data, status, score, first_seen, seen FROM jobs"
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
    if unseen:
        clauses.append("seen = 0")
    if with_salary:
        clauses.append("json_extract(data, '$.salary') != ''")
    query += " WHERE " + " AND ".join(clauses) + " ORDER BY " + SORTS.get(sort, SORTS["score"])
    with connect() as c:
        rows = c.execute(query, args).fetchall()
    return [
        {
            "job": Job.model_validate_json(r["data"]),
            "status": r["status"],
            "score": r["score"],
            "first_seen": r["first_seen"],
            "seen": bool(r["seen"]),
        }
        for r in rows
    ]


def mark_seen(job_ids: list[str] | None = None) -> None:
    """Mark these jobs (or every job) as seen."""
    with connect() as c:
        if job_ids is None:
            c.execute("UPDATE jobs SET seen = 1 WHERE seen = 0")
        else:
            c.executemany("UPDATE jobs SET seen = 1 WHERE id = ?", [(i,) for i in job_ids])


def unseen_count() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM jobs WHERE seen = 0 AND status != 'hidden'").fetchone()[0]


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


def save_letter(job_id: str, letter: CoverLetter, body: str, pdf_path: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO letters (job_id, created_at, data, body, pdf_path) VALUES (?, ?, ?, ?, ?)",
            (job_id, time.time(), letter.model_dump_json(), body, pdf_path),
        )
        return cur.lastrowid


def get_letter(letter_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM letters WHERE id = ?", (letter_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "letter": CoverLetter.model_validate_json(row["data"])}


def update_letter_body(letter_id: int, body: str) -> None:
    with connect() as c:
        c.execute("UPDATE letters SET body = ? WHERE id = ?", (body, letter_id))


def letters_for_job(job_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, created_at FROM letters WHERE job_id = ? ORDER BY created_at DESC", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]
