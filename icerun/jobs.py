"""SQLite-backed async job store for icerun batch --async."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    """Return ~/.local/share/icerun/jobs.db or ICER_JOBS_DB env override."""
    override = os.environ.get("ICER_JOBS_DB")
    if override:
        return Path(override)
    data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "icerun"
    return data_dir / "jobs.db"


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and row_factory."""
    path = Path(db_path) if db_path else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    """Create tables if they do not exist."""
    conn = _connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL DEFAULT 'batch_scrape',
                status      TEXT NOT NULL DEFAULT 'queued',
                params      TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                total       INTEGER NOT NULL DEFAULT 0,
                done        INTEGER NOT NULL DEFAULT 0,
                failed      INTEGER NOT NULL DEFAULT 0,
                skipped     INTEGER NOT NULL DEFAULT 0,
                pid         INTEGER,
                error       TEXT
            );

            CREATE TABLE IF NOT EXISTS job_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL REFERENCES jobs(id),
                url         TEXT NOT NULL,
                status      TEXT NOT NULL,
                output_path TEXT,
                error       TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_job_results_job_id
                ON job_results(job_id);

            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status);

            CREATE INDEX IF NOT EXISTS idx_jobs_created_at
                ON jobs(created_at);
        """)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def create_job(
    params: dict,
    total: int,
    job_type: str = "batch_scrape",
    db_path: str | Path | None = None,
) -> str:
    """Create a job record and return the new job_id (uuid4 hex)."""
    init_db(db_path)
    job_id = uuid.uuid4().hex
    now = _now()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, type, status, params, created_at, updated_at, total)
            VALUES (?, ?, 'queued', ?, ?, ?, ?)
            """,
            (job_id, job_type, json.dumps(params), now, now, total),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def get_job(job_id: str, db_path: str | Path | None = None) -> dict | None:
    """Return the job row as a dict, or None if not found."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def update_job(job_id: str, db_path: str | Path | None = None, **fields) -> None:
    """Update arbitrary fields on a job row. Always sets updated_at."""
    if not fields:
        return
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def add_result(
    job_id: str,
    url: str,
    status: str,
    output_path: str | None,
    error: str | None,
    db_path: str | Path | None = None,
) -> None:
    """Append a result row for a single URL."""
    init_db(db_path)
    now = _now()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO job_results (job_id, url, status, output_path, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, url, status, output_path, error, now),
        )
        # Increment appropriate counter atomically
        if status == "ok":
            conn.execute(
                "UPDATE jobs SET done = done + 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
        elif status == "fail":
            conn.execute(
                "UPDATE jobs SET failed = failed + 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
        elif status == "skip":
            conn.execute(
                "UPDATE jobs SET skipped = skipped + 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
        conn.commit()
    finally:
        conn.close()


def list_jobs(
    status: str | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Return jobs sorted by created_at DESC, optionally filtered by status."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_results(
    job_id: str,
    after_id: int = 0,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Return result rows for a job with id > after_id (for polling)."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM job_results WHERE job_id = ? AND id > ? ORDER BY id ASC",
            (job_id, after_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_old_jobs(older_than_days: int, db_path: str | Path | None = None) -> int:
    """Delete jobs in terminal states older than N days. Returns count deleted."""
    init_db(db_path)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cutoff_str = cutoff.isoformat()
    terminal_states = ("done", "completed", "failed", "cancelled")
    placeholders = ",".join("?" for _ in terminal_states)

    conn = _connect(db_path)
    try:
        # Delete results first (FK constraint)
        conn.execute(
            f"""
            DELETE FROM job_results
            WHERE job_id IN (
                SELECT id FROM jobs
                WHERE status IN ({placeholders})
                AND created_at < ?
            )
            """,
            (*terminal_states, cutoff_str),
        )
        result = conn.execute(
            f"""
            DELETE FROM jobs
            WHERE status IN ({placeholders})
            AND created_at < ?
            """,
            (*terminal_states, cutoff_str),
        )
        count = result.rowcount
        conn.commit()
        return count
    finally:
        conn.close()
