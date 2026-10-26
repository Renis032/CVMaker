import json
import sqlite3
import statistics
import time
from contextlib import contextmanager

from . import settings
from .models import CoverLetter, Job, TailoredCV
from .salary import monthly_eur

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
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    at REAL NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    started REAL NOT NULL DEFAULT 0,
    finished REAL NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 0
);
"""

# Jobs that existed before the 'seen' flag count as seen; newly found jobs are inserted unseen.
MIGRATIONS = [
    ("jobs", "seen", "ALTER TABLE jobs ADD COLUMN seen INTEGER NOT NULL DEFAULT 1"),
    ("jobs", "status_at", "ALTER TABLE jobs ADD COLUMN status_at REAL NOT NULL DEFAULT 0"),
    ("jobs", "notes", "ALTER TABLE jobs ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
    ("jobs", "contact", "ALTER TABLE jobs ADD COLUMN contact TEXT NOT NULL DEFAULT ''"),
    ("jobs", "next_date", "ALTER TABLE jobs ADD COLUMN next_date TEXT NOT NULL DEFAULT ''"),
    ("jobs", "follow_up_at", "ALTER TABLE jobs ADD COLUMN follow_up_at REAL NOT NULL DEFAULT 0"),
    ("jobs", "reminded", "ALTER TABLE jobs ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "sent_cv_id", "ALTER TABLE jobs ADD COLUMN sent_cv_id INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "sent_letter_id", "ALTER TABLE jobs ADD COLUMN sent_letter_id INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "fit", "ALTER TABLE jobs ADD COLUMN fit INTEGER"),
    ("jobs", "fit_data", "ALTER TABLE jobs ADD COLUMN fit_data TEXT NOT NULL DEFAULT ''"),
    ("jobs", "salary_month", "ALTER TABLE jobs ADD COLUMN salary_month REAL"),
    ("jobs", "feedback", "ALTER TABLE jobs ADD COLUMN feedback TEXT NOT NULL DEFAULT ''"),
    ("jobs", "taste", "ALTER TABLE jobs ADD COLUMN taste INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "taste_data", "ALTER TABLE jobs ADD COLUMN taste_data TEXT NOT NULL DEFAULT ''"),
    ("cvs", "layout", "ALTER TABLE cvs ADD COLUMN layout TEXT NOT NULL DEFAULT '{}'"),
    ("cvs", "pages", "ALTER TABLE cvs ADD COLUMN pages INTEGER NOT NULL DEFAULT 0"),
]

# Application stages after "new", in pipeline order. "hidden" is not a stage.
STAGES = ["liked", "applied", "screening", "interview", "offer", "rejected", "ghosted"]
APPLIED_STAGES = ["applied", "screening", "interview", "offer", "rejected", "ghosted"]
# The employer answered: anything past "applied" except silence.
RESPONDED = ["screening", "interview", "offer", "rejected"]
STAGE_LABELS = {
    "new": "New", "liked": "To apply", "applied": "Applied", "screening": "Screening", "interview": "Interview",
    "offer": "Offer", "rejected": "Rejected", "ghosted": "Ghosted", "hidden": "Hidden",
}

# Sort key -> (SQL expression, default direction). Rows without a value always go last.
SORTS = {
    "score": ("score", "desc"),
    "fit": ("fit", "desc"),
    "title": ("lower(json_extract(data, '$.title'))", "asc"),
    "newest": ("first_seen", "desc"),
    "posted": ("NULLIF(json_extract(data, '$.posted_at'), '')", "desc"),
    "salary": ("salary_month", "desc"),
    "company": ("lower(json_extract(data, '$.company'))", "asc"),
    # Claude's fit (unrated counts as 5/10), keyword score and what your feedback taught, combined.
    "match": ("(COALESCE(fit, 5) * 10 + score * 0.3 + taste)", "desc"),
}


def sort_direction(sort: str, direction: str = "") -> str:
    """The direction to use: the one asked for, or the sort's natural one (highest first, A-Z for text)."""
    return direction if direction in ("asc", "desc") else SORTS.get(sort, SORTS["score"])[1]


def _order_by(sort: str, direction: str = "") -> str:
    expr, _ = SORTS.get(sort, SORTS["score"])
    order = f"{expr} IS NULL, {expr} {sort_direction(sort, direction).upper()}"
    if sort == "salary":  # salaries that could not be converted still beat no salary at all
        order += ", json_extract(data, '$.salary') = ''"
    return order + ", score DESC, first_seen DESC"

_migrated = False


@contextmanager
def connect():
    global _migrated
    conn = sqlite3.connect(settings.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if not _migrated:
            conn.executescript(SCHEMA)
            for table, column, sql in MIGRATIONS:
                if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(sql)
            conn.commit()
            _migrated = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def backfill() -> None:
    """Fill values added by later versions for rows saved before them."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, json_extract(data, '$.salary') AS s FROM jobs WHERE salary_month IS NULL AND s != ''"
        ).fetchall()
        for r in rows:
            c.execute("UPDATE jobs SET salary_month = ? WHERE id = ?", (monthly_eur(r["s"]), r["id"]))
        c.execute("UPDATE jobs SET status_at = first_seen WHERE status_at = 0 AND status != 'new'")


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
                    "INSERT INTO jobs (id, data, score, first_seen, last_seen, seen, salary_month)"
                    " VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (job.id, job.model_dump_json(), scores.get(job.id, 0), now, now, monthly_eur(job.salary)),
                )
                continue
            # Keep the full description fetched earlier for sources that only list titles.
            old = Job.model_validate_json(row["data"])
            if len(old.description) > len(job.description):
                job.description = old.description
            job.salary = job.salary or old.salary
            c.execute(
                "UPDATE jobs SET data = ?, score = ?, last_seen = ?, salary_month = ? WHERE id = ?",
                (job.model_dump_json(), scores.get(job.id, 0), now, monthly_eur(job.salary), job.id),
            )
    return new_ids


def get_job(job_id: str) -> tuple[Job, str] | None:
    with connect() as c:
        row = c.execute("SELECT data, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return Job.model_validate_json(row["data"]), row["status"]


def get_application(job_id: str) -> dict:
    """The tracking fields of one job: stage, notes, contact, dates, what was sent, quick fit."""
    with connect() as c:
        row = c.execute(
            "SELECT status, status_at, notes, contact, next_date, follow_up_at, sent_cv_id, sent_letter_id, fit, fit_data,"
            " salary_month"
            " FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        events = c.execute("SELECT at, status, note FROM events WHERE job_id = ? ORDER BY at", (job_id,)).fetchall()
    app = dict(row) if row else {}
    app["fit_data"] = json.loads(app["fit_data"]) if app.get("fit_data") else None
    app["events"] = [dict(e) for e in events]
    return app


def update_job(job: Job) -> None:
    with connect() as c:
        c.execute(
            "UPDATE jobs SET data = ?, salary_month = ? WHERE id = ?",
            (job.model_dump_json(), monthly_eur(job.salary), job.id),
        )


def set_status(job_id: str, status: str, at: float | None = None, note: str = "") -> None:
    """Move a job to a stage and record it on the job's timeline."""
    at = at or time.time()
    with connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or (row["status"] == status and not note):
            return
        c.execute("UPDATE jobs SET status = ?, status_at = ? WHERE id = ?", (status, at, job_id))
        c.execute("INSERT INTO events (job_id, at, status, note) VALUES (?, ?, ?, ?)", (job_id, at, status, note))
        if status == "applied":
            due = at + settings.FOLLOW_UP_DAYS * 86400
            c.execute("UPDATE jobs SET follow_up_at = ?, reminded = 0 WHERE id = ?", (due, job_id))
        elif status != "hidden":
            c.execute("UPDATE jobs SET follow_up_at = 0 WHERE id = ?", (job_id,))


def set_feedback(job_id: str, reason: str) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET feedback = ? WHERE id = ?", (reason, job_id))


def hide_company(company: str, except_id: str = "") -> int:
    """Hide every other open job from this company (marked 'blocked' so they don't count as separate feedback)."""
    key = _company_key(company)
    with connect() as c:
        rows = c.execute(
            "SELECT id, json_extract(data, '$.company') AS company FROM jobs WHERE status IN ('new', 'liked') AND id != ?",
            (except_id,),
        ).fetchall()
        ids = [r["id"] for r in rows if _company_key(r["company"] or "") == key]
        c.executemany("UPDATE jobs SET status = 'hidden', feedback = 'blocked' WHERE id = ?", [(i,) for i in ids])
    return len(ids)


def unhide_company(company: str) -> None:
    """Undo hide_company: show the jobs that were hidden only because the company was blocked."""
    key = _company_key(company)
    with connect() as c:
        rows = c.execute(
            "SELECT id, json_extract(data, '$.company') AS company FROM jobs WHERE status = 'hidden' AND feedback IN ('blocked', 'company')"
        ).fetchall()
        ids = [r["id"] for r in rows if _company_key(r["company"] or "") == key]
        c.executemany("UPDATE jobs SET status = 'new', feedback = '' WHERE id = ?", [(i,) for i in ids])


def feedback_rows() -> list[dict]:
    """Every job you reacted to: hidden (with its reason) or moved into the pipeline."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, data, status, feedback, salary_month FROM jobs WHERE status != 'new'"
            " AND NOT (status = 'hidden' AND feedback = 'blocked')"
        ).fetchall()
    return [
        {"job": Job.model_validate_json(r["data"]), "status": r["status"], "feedback": r["feedback"], "salary_month": r["salary_month"]}
        for r in rows
    ]


def open_jobs() -> list[tuple[Job, float | None]]:
    with connect() as c:
        rows = c.execute("SELECT data, salary_month FROM jobs WHERE status != 'hidden'").fetchall()
    return [(Job.model_validate_json(r["data"]), r["salary_month"]) for r in rows]


def market_rows() -> list[dict]:
    """Every collected job with what the insights page needs, hidden ones included."""
    with connect() as c:
        rows = c.execute("SELECT data, status, first_seen, salary_month, fit, fit_data FROM jobs").fetchall()
    return [
        {
            "job": Job.model_validate_json(r["data"]),
            "status": r["status"],
            "first_seen": r["first_seen"],
            "salary_month": r["salary_month"],
            "fit": r["fit"],
            "fit_data": json.loads(r["fit_data"]) if r["fit_data"] else None,
        }
        for r in rows
    ]


def set_tastes(values: list[tuple[int, list, str]]) -> None:
    """(taste, reasons, job_id) for many jobs."""
    with connect() as c:
        c.executemany("UPDATE jobs SET taste = ?, taste_data = ? WHERE id = ?", [(t, json.dumps(d), i) for t, d, i in values])


def promote(job_id: str, status: str) -> None:
    """Set a stage only if the job has none yet (e.g. generating a CV likes a job, but never un-applies it)."""
    found = get_job(job_id)
    if found and found[1] == "new":
        set_status(job_id, status)


def save_application(job_id: str, **fields) -> None:
    allowed = {"notes", "contact", "next_date", "sent_cv_id", "sent_letter_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with connect() as c:
        c.execute(
            f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), job_id)
        )


def snooze_follow_up(job_id: str, days: float) -> None:
    with connect() as c:
        c.execute(
            "UPDATE jobs SET follow_up_at = ?, reminded = 0 WHERE id = ?", (time.time() + days * 86400, job_id)
        )
        c.execute(
            "INSERT INTO events (job_id, at, status, note) VALUES (?, ?, 'applied', 'Followed up')",
            (job_id, time.time()),
        )


def due_follow_ups(only_unreminded: bool = False) -> list[dict]:
    query = "SELECT id, data, status_at, follow_up_at FROM jobs WHERE status = 'applied' AND follow_up_at > 0 AND follow_up_at <= ?"
    if only_unreminded:
        query += " AND reminded = 0"
    with connect() as c:
        rows = c.execute(query + " ORDER BY follow_up_at", (time.time(),)).fetchall()
    return [{"job": Job.model_validate_json(r["data"]), "status_at": r["status_at"]} for r in rows]


def mark_reminded(job_ids: list[str]) -> None:
    with connect() as c:
        c.executemany("UPDATE jobs SET reminded = 1 WHERE id = ?", [(i,) for i in job_ids])


def set_fit(job_id: str, rating: int, data: dict) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET fit = ?, fit_data = ? WHERE id = ?", (rating, json.dumps(data), job_id))


def unrated(job_ids: list[str]) -> list[str]:
    """These jobs, in the given order, minus the ones that already have a fit rating."""
    if not job_ids:
        return []
    with connect() as c:
        rated = {
            r["id"]
            for r in c.execute(
                f"SELECT id FROM jobs WHERE fit IS NOT NULL AND id IN ({','.join('?' * len(job_ids))})", job_ids
            )
        }
    return [i for i in job_ids if i not in rated]


def _row(r) -> dict:
    return {
        "job": Job.model_validate_json(r["data"]),
        "status": r["status"],
        "status_at": r["status_at"],
        "score": r["score"],
        "first_seen": r["first_seen"],
        "seen": bool(r["seen"]),
        "fit": r["fit"],
        "fit_data": json.loads(r["fit_data"]) if r["fit_data"] else None,
        "salary_month": r["salary_month"],
        "notes": r["notes"],
        "contact": r["contact"],
        "next_date": r["next_date"],
        "follow_up_at": r["follow_up_at"],
        "sent_cv_id": r["sent_cv_id"],
        "feedback": r["feedback"],
        "taste": r["taste"],
        "taste_data": json.loads(r["taste_data"]) if r["taste_data"] else [],
    }


def list_jobs(
    ids: list[str] | None = None,
    status: str | None = None,
    unseen: bool = False,
    with_salary: bool = False,
    sort: str = "score",
    statuses: list[str] | None = None,
    direction: str = "",
) -> list[dict]:
    query = "SELECT * FROM jobs"
    args: list = []
    clauses = []
    if ids is not None:
        if not ids:
            return []
        clauses.append(f"id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    if statuses:
        clauses.append(f"status IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
    elif status:
        clauses.append("status = ?")
        args.append(status)
    else:
        clauses.append("status != 'hidden'")
    if unseen:
        clauses.append("seen = 0")
    if with_salary:
        clauses.append("json_extract(data, '$.salary') != ''")
    query += " WHERE " + " AND ".join(clauses) + " ORDER BY " + _order_by(sort, direction)
    with connect() as c:
        rows = c.execute(query, args).fetchall()
    return [_row(r) for r in rows]


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


def _company_key(name: str) -> str:
    return " ".join(name.lower().split())


def company_history(company: str, exclude_id: str = "") -> list[dict]:
    """Earlier applications to this company (anything past 'liked'), newest first."""
    with connect() as c:
        rows = c.execute(
            f"SELECT id, data, status, status_at FROM jobs WHERE id != ? AND status IN ({','.join('?' * len(APPLIED_STAGES))})"
            " ORDER BY status_at DESC",
            (exclude_id, *APPLIED_STAGES),
        ).fetchall()
    key = _company_key(company)
    out = []
    for r in rows:
        job = Job.model_validate_json(r["data"])
        if _company_key(job.company) == key:
            out.append({"id": r["id"], "title": job.title, "status": r["status"], "status_at": r["status_at"]})
    return out


def company_summaries() -> dict[str, str]:
    """'applied 3× · last: rejected' per company you have applied to (keyed by normalised company name)."""
    with connect() as c:
        rows = c.execute(
            f"SELECT json_extract(data, '$.company') AS company, status FROM jobs"
            f" WHERE status IN ({','.join('?' * len(APPLIED_STAGES))}) ORDER BY status_at",
            APPLIED_STAGES,
        ).fetchall()
    counts: dict[str, list[str]] = {}
    for r in rows:
        counts.setdefault(_company_key(r["company"] or ""), []).append(r["status"])
    return {k: f"applied {len(v)}× · last: {STAGE_LABELS[v[-1]].lower()}" for k, v in counts.items()}


def stats() -> dict:
    """Numbers for the dashboard strip and the pipeline page."""
    now = time.time()
    week = now - 7 * 86400
    with connect() as c:
        new_week = c.execute("SELECT COUNT(*) FROM jobs WHERE first_seen >= ? AND status != 'hidden'", (week,)).fetchone()[0]
        apps = c.execute(
            f"SELECT id, status, sent_cv_id FROM jobs WHERE status IN ({','.join('?' * len(APPLIED_STAGES))})",
            APPLIED_STAGES,
        ).fetchall()
        responded_ids = {
            r["job_id"]
            for r in c.execute(f"SELECT DISTINCT job_id FROM events WHERE status IN ({','.join('?' * len(RESPONDED))})", RESPONDED)
        }
        applied_week = c.execute(
            "SELECT COUNT(DISTINCT job_id) FROM events WHERE status = 'applied' AND at >= ?", (week,)
        ).fetchone()[0]
        salaries = [
            r[0]
            for r in c.execute(
                "SELECT salary_month FROM jobs WHERE salary_month IS NOT NULL AND status != 'hidden' AND first_seen >= ?",
                (now - 60 * 86400,),
            )
        ]
        follow_ups = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'applied' AND follow_up_at > 0 AND follow_up_at <= ?", (now,)
        ).fetchone()[0]
        by_stage = dict(c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())

    def responded(r) -> bool:
        return r["status"] in RESPONDED or r["id"] in responded_ids

    def rate(rows) -> dict:
        n = len(rows)
        hits = sum(1 for r in rows if responded(r))
        return {"n": n, "responded": hits, "pct": round(100 * hits / n) if n else None}

    return {
        "new_week": new_week,
        "applications": len(apps),
        "applied_week": applied_week,
        "response": rate(apps),
        "response_tailored": rate([r for r in apps if r["sent_cv_id"]]),
        "response_untailored": rate([r for r in apps if not r["sent_cv_id"]]),
        "median_salary": statistics.median(salaries) if salaries else None,
        "salary_n": len(salaries),
        "follow_ups": follow_ups,
        "by_stage": by_stage,
    }


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


def cache_delete(key: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM cache WHERE key = ?", (key,))


def save_cv(job_id: str, tailored: TailoredCV, pdf_path: str, layout: dict | None = None, pages: int = 0) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO cvs (job_id, created_at, data, pdf_path, layout, pages) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, time.time(), tailored.model_dump_json(), pdf_path, json.dumps(layout or {}), pages),
        )
        return cur.lastrowid


def update_cv(cv_id: int, tailored: TailoredCV | None = None, layout: dict | None = None, pages: int | None = None) -> None:
    with connect() as c:
        if tailored is not None:
            c.execute("UPDATE cvs SET data = ? WHERE id = ?", (tailored.model_dump_json(), cv_id))
        if layout is not None:
            c.execute("UPDATE cvs SET layout = ? WHERE id = ?", (json.dumps(layout), cv_id))
        if pages is not None:
            c.execute("UPDATE cvs SET pages = ? WHERE id = ?", (pages, cv_id))


def get_cv(cv_id: int) -> tuple[str, TailoredCV, str] | None:
    with connect() as c:
        row = c.execute("SELECT job_id, data, pdf_path FROM cvs WHERE id = ?", (cv_id,)).fetchone()
    if row is None:
        return None
    return row["job_id"], TailoredCV.model_validate_json(row["data"]), row["pdf_path"]


def cv_layout(cv_id: int) -> tuple[dict, int]:
    with connect() as c:
        row = c.execute("SELECT layout, pages FROM cvs WHERE id = ?", (cv_id,)).fetchone()
    return (json.loads(row["layout"] or "{}"), row["pages"]) if row else ({}, 0)


def cvs_for_job(job_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, created_at FROM cvs WHERE job_id = ? ORDER BY created_at DESC", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def all_documents() -> list[dict]:
    """Every generated CV and cover letter, newest first, with the job (or role) it was made for."""
    with connect() as c:
        rows = c.execute(
            "SELECT 'cv' AS kind, cvs.id, cvs.job_id, cvs.created_at, jobs.data AS job_data, jobs.status,"
            "  json_extract(cvs.data, '$.match_score') AS match_score, (jobs.sent_cv_id = cvs.id) AS sent"
            " FROM cvs LEFT JOIN jobs ON jobs.id = cvs.job_id"
            " UNION ALL "
            "SELECT 'letter', letters.id, letters.job_id, letters.created_at, jobs.data, jobs.status, NULL,"
            "  (jobs.sent_letter_id = letters.id)"
            " FROM letters LEFT JOIN jobs ON jobs.id = letters.job_id"
            " ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["job"] = Job.model_validate_json(d.pop("job_data")) if r["job_data"] else None
        out.append(d)
    return out


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


# --- background tasks -------------------------------------------------------------------------------

TASK_FIELDS = {"status", "progress", "error", "started", "finished", "url", "seen", "label"}


def task_create(kind: str, label: str, subject: str = "", job_id: str = "", url: str = "") -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO tasks (kind, label, subject, job_id, url, created) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, label, subject, job_id, url, time.time()),
        )
        return cur.lastrowid


def task_update(task_id: int, **fields) -> None:
    sets = {k: (json.dumps(v) if k == "progress" else v) for k, v in fields.items() if k in TASK_FIELDS}
    with connect() as c:
        c.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), task_id))


def _task(r) -> dict:
    d = dict(r)
    d["progress"] = json.loads(d["progress"] or "{}")
    return d


def task_get(task_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task(row) if row else None


def task_active(kind: str | None = None, subject: str | None = None) -> list[dict]:
    query, args = "SELECT * FROM tasks WHERE status IN ('queued', 'running')", []
    if kind:
        query, args = query + " AND kind = ?", [*args, kind]
    if subject is not None:
        query, args = query + " AND subject = ?", [*args, subject]
    with connect() as c:
        return [_task(r) for r in c.execute(query + " ORDER BY created", args)]


def tasks_for_job(job_id: str) -> dict[str, dict]:
    """The running or queued task of each kind for one job."""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE job_id = ? AND status IN ('queued', 'running') ORDER BY created", (job_id,)
        ).fetchall()
    return {r["kind"]: _task(r) for r in rows}


def tasks_recent(limit: int = 50) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = _task(r)
        found = get_job(d["job_id"]) if d["job_id"] else None
        d["job"] = found[0] if found else None
        out.append(d)
    return out


def tasks_unseen_finished() -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE status IN ('done', 'error') AND seen = 0 ORDER BY finished"
        ).fetchall()
    return [_task(r) for r in rows]


def tasks_interrupt_stale() -> None:
    """Tasks left queued or running by a previous process never finish; say so."""
    with connect() as c:
        c.execute(
            "UPDATE tasks SET status = 'error', error = 'Interrupted - the app was restarted. Start it again.',"
            " finished = ?, seen = 1 WHERE status IN ('queued', 'running')",
            (time.time(),),
        )
