"""Tests for the async job store (icerun/jobs.py) and job CLI commands."""
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from icerun.cli import app
from icerun import jobs as jobs_mod

runner = CliRunner()


# ---------------------------------------------------------------------------
# Unit tests: jobs module
# ---------------------------------------------------------------------------


def test_create_and_get_job(tmp_jobs_db):
    """create_job → get_job returns a dict with expected fields."""
    params = {"urls": ["https://example.com"], "format": "markdown"}
    job_id = jobs_mod.create_job(params=params, total=1, db_path=tmp_jobs_db)

    assert isinstance(job_id, str)
    assert len(job_id) == 32  # uuid4 hex

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == "queued"
    assert job["total"] == 1
    assert job["done"] == 0
    assert job["failed"] == 0
    assert job["skipped"] == 0
    assert job["type"] == "batch_scrape"
    assert job["params"] == json.dumps(params)


def test_get_job_not_found(tmp_jobs_db):
    """get_job returns None for unknown job ID."""
    result = jobs_mod.get_job("nonexistent_id_abc123", db_path=tmp_jobs_db)
    assert result is None


def test_update_job(tmp_jobs_db):
    """update_job changes fields and updates updated_at."""
    job_id = jobs_mod.create_job(params={}, total=5, db_path=tmp_jobs_db)
    jobs_mod.update_job(job_id, db_path=tmp_jobs_db, status="running", pid=12345)

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job["status"] == "running"
    assert job["pid"] == 12345


def test_add_result_ok(tmp_jobs_db):
    """add_result increments done counter and stores row."""
    job_id = jobs_mod.create_job(params={}, total=2, db_path=tmp_jobs_db)
    jobs_mod.add_result(
        job_id, "https://example.com/page1", "ok", "/tmp/out/page1.md", None,
        db_path=tmp_jobs_db,
    )

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job["done"] == 1
    assert job["failed"] == 0

    results = jobs_mod.get_results(job_id, db_path=tmp_jobs_db)
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/page1"
    assert results[0]["status"] == "ok"
    assert results[0]["output_path"] == "/tmp/out/page1.md"


def test_add_result_fail(tmp_jobs_db):
    """add_result with fail status increments failed counter."""
    job_id = jobs_mod.create_job(params={}, total=2, db_path=tmp_jobs_db)
    jobs_mod.add_result(
        job_id, "https://example.com/fail", "fail", None, "connection timeout",
        db_path=tmp_jobs_db,
    )

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job["failed"] == 1
    assert job["done"] == 0

    results = jobs_mod.get_results(job_id, db_path=tmp_jobs_db)
    assert results[0]["error"] == "connection timeout"


def test_add_result_skip(tmp_jobs_db):
    """add_result with skip status increments skipped counter."""
    job_id = jobs_mod.create_job(params={}, total=3, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://example.com/skip", "skip", "/existing.md", None, db_path=tmp_jobs_db)

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job["skipped"] == 1


def test_list_jobs_all(tmp_jobs_db):
    """list_jobs returns all jobs sorted by created_at DESC."""
    id1 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    id2 = jobs_mod.create_job(params={}, total=2, db_path=tmp_jobs_db)

    job_list = jobs_mod.list_jobs(db_path=tmp_jobs_db)
    assert len(job_list) == 2
    # Most recent first
    assert job_list[0]["id"] == id2
    assert job_list[1]["id"] == id1


def test_list_jobs_filter_by_status(tmp_jobs_db):
    """list_jobs with status filter returns only matching jobs."""
    id1 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    id2 = jobs_mod.create_job(params={}, total=2, db_path=tmp_jobs_db)
    id3 = jobs_mod.create_job(params={}, total=3, db_path=tmp_jobs_db)

    jobs_mod.update_job(id1, db_path=tmp_jobs_db, status="running")
    jobs_mod.update_job(id2, db_path=tmp_jobs_db, status="completed")
    # id3 stays queued

    running = jobs_mod.list_jobs(status="running", db_path=tmp_jobs_db)
    assert len(running) == 1
    assert running[0]["id"] == id1

    completed = jobs_mod.list_jobs(status="completed", db_path=tmp_jobs_db)
    assert len(completed) == 1
    assert completed[0]["id"] == id2

    queued = jobs_mod.list_jobs(status="queued", db_path=tmp_jobs_db)
    assert len(queued) == 1
    assert queued[0]["id"] == id3


def test_delete_old_jobs(tmp_jobs_db):
    """delete_old_jobs removes terminal jobs older than N days."""
    import sqlite3

    # Create two jobs and mark them completed
    id1 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    id2 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    id3 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)  # stays queued

    jobs_mod.update_job(id1, db_path=tmp_jobs_db, status="completed")
    jobs_mod.update_job(id2, db_path=tmp_jobs_db, status="failed")

    # Back-date their created_at so they appear old
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn = sqlite3.connect(tmp_jobs_db)
    conn.execute("UPDATE jobs SET created_at = ? WHERE id IN (?, ?)", (old_ts, id1, id2))
    conn.commit()
    conn.close()

    count = jobs_mod.delete_old_jobs(older_than_days=7, db_path=tmp_jobs_db)
    assert count == 2

    # id3 (queued) must still exist
    assert jobs_mod.get_job(id3, db_path=tmp_jobs_db) is not None
    # id1 and id2 should be gone
    assert jobs_mod.get_job(id1, db_path=tmp_jobs_db) is None
    assert jobs_mod.get_job(id2, db_path=tmp_jobs_db) is None


def test_delete_old_jobs_returns_zero_when_none_old(tmp_jobs_db):
    """delete_old_jobs returns 0 if no qualifying jobs exist."""
    id1 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    jobs_mod.update_job(id1, db_path=tmp_jobs_db, status="completed")
    # created_at is now, so it's NOT older than 7 days
    count = jobs_mod.delete_old_jobs(older_than_days=7, db_path=tmp_jobs_db)
    assert count == 0


def test_get_results_after_id(tmp_jobs_db):
    """get_results with after_id returns only rows with id > after_id."""
    job_id = jobs_mod.create_job(params={}, total=3, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://a.com/1", "ok", None, None, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://a.com/2", "ok", None, None, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://a.com/3", "ok", None, None, db_path=tmp_jobs_db)

    all_results = jobs_mod.get_results(job_id, after_id=0, db_path=tmp_jobs_db)
    assert len(all_results) == 3

    first_id = all_results[0]["id"]
    later = jobs_mod.get_results(job_id, after_id=first_id, db_path=tmp_jobs_db)
    assert len(later) == 2
    assert later[0]["url"] == "https://a.com/2"


# ---------------------------------------------------------------------------
# CLI tests: job subcommands
# ---------------------------------------------------------------------------


def test_job_status_cli(tmp_jobs_db):
    """job status shows job info in a table."""
    job_id = jobs_mod.create_job(params={"urls": []}, total=10, db_path=tmp_jobs_db)
    jobs_mod.update_job(job_id, db_path=tmp_jobs_db, status="running", done=3)

    result = runner.invoke(app, ["job", "status", job_id])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    assert "running" in result.output
    assert "3" in result.output  # done count


def test_job_status_not_found_cli(tmp_jobs_db):
    """job status exits 1 for unknown job ID."""
    result = runner.invoke(app, ["job", "status", "nonexistent_job_id"])
    assert result.exit_code == 1


def test_job_list_cli(tmp_jobs_db):
    """job list shows all jobs in a table."""
    jobs_mod.create_job(params={}, total=5, db_path=tmp_jobs_db)
    jobs_mod.create_job(params={}, total=3, db_path=tmp_jobs_db)

    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    assert "queued" in result.output


def test_job_list_filter_cli(tmp_jobs_db):
    """job list --status filters correctly."""
    id1 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    id2 = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    jobs_mod.update_job(id2, db_path=tmp_jobs_db, status="running")

    result = runner.invoke(app, ["job", "list", "--status", "running"])
    assert result.exit_code == 0
    assert "running" in result.output


def test_job_list_empty_cli(tmp_jobs_db):
    """job list with no jobs prints 'No jobs found' message."""
    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 0
    assert "No jobs" in result.output


def test_job_cancel_cli(tmp_jobs_db):
    """job cancel transitions a queued job to cancelled."""
    job_id = jobs_mod.create_job(params={}, total=5, db_path=tmp_jobs_db)

    result = runner.invoke(app, ["job", "cancel", job_id])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"

    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job["status"] == "cancelled"


def test_job_cancel_already_terminal_cli(tmp_jobs_db):
    """job cancel exits 1 if job is already in a terminal state."""
    job_id = jobs_mod.create_job(params={}, total=5, db_path=tmp_jobs_db)
    jobs_mod.update_job(job_id, db_path=tmp_jobs_db, status="completed")

    result = runner.invoke(app, ["job", "cancel", job_id])
    assert result.exit_code == 1


def test_job_cancel_not_found_cli(tmp_jobs_db):
    """job cancel exits 1 for unknown job."""
    result = runner.invoke(app, ["job", "cancel", "no_such_job"])
    assert result.exit_code == 1


def test_job_clean_cli(tmp_jobs_db):
    """job clean deletes old terminal jobs and reports count."""
    import sqlite3

    job_id = jobs_mod.create_job(params={}, total=1, db_path=tmp_jobs_db)
    jobs_mod.update_job(job_id, db_path=tmp_jobs_db, status="completed")

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn = sqlite3.connect(tmp_jobs_db)
    conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (old_ts, job_id))
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["job", "clean", "--older-than", "7"])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    assert "1" in result.output


def test_job_watch_exits_on_completed_job(tmp_jobs_db):
    """job watch exits immediately when job is already in a terminal state."""
    job_id = jobs_mod.create_job(params={}, total=2, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://x.com/1", "ok", "/out/1.md", None, db_path=tmp_jobs_db)
    jobs_mod.add_result(job_id, "https://x.com/2", "fail", None, "timeout", db_path=tmp_jobs_db)
    jobs_mod.update_job(job_id, db_path=tmp_jobs_db, status="completed")

    result = runner.invoke(app, ["job", "watch", job_id])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"

    # Each result row should be emitted as a JSON line
    lines = [l for l in result.output.strip().splitlines() if l.startswith("{")]
    assert len(lines) == 2

    parsed = [json.loads(l) for l in lines]
    urls = [r["url"] for r in parsed]
    assert "https://x.com/1" in urls
    assert "https://x.com/2" in urls


def test_job_watch_not_found_cli(tmp_jobs_db):
    """job watch exits 1 for unknown job."""
    result = runner.invoke(app, ["job", "watch", "no_such_job"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# CLI tests: batch --async mode
# ---------------------------------------------------------------------------


def test_batch_async_mode(tmp_path, tmp_jobs_db):
    """batch --async creates a job record, launches Popen, echoes job_id."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://example.com/page1\nhttps://example.com/page2\n")
    out_dir = tmp_path / "output"

    mock_popen = MagicMock()

    with patch("subprocess.Popen", return_value=mock_popen) as popen_mock:
        result = runner.invoke(
            app,
            ["batch", str(url_file), "--output", str(out_dir), "--async"],
        )

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"

    # The output should be a job ID (32-char hex)
    job_id = result.output.strip()
    assert len(job_id) == 32
    assert all(c in "0123456789abcdef" for c in job_id)

    # Popen must have been called with the worker module
    popen_mock.assert_called_once()
    cmd = popen_mock.call_args[0][0]
    assert "-m" in cmd
    assert "icerun.job_worker" in cmd
    assert job_id in cmd

    # A job record must exist in the DB
    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    assert job is not None
    assert job["status"] == "queued"
    assert job["total"] == 2

    # params must contain the URLs
    params = json.loads(job["params"])
    assert "https://example.com/page1" in params["urls"]


def test_batch_async_job_params_stored(tmp_path, tmp_jobs_db):
    """batch --async stores all relevant params in the job record."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://a.com\nhttps://b.com\nhttps://c.com\n")
    out_dir = tmp_path / "output"

    with patch("subprocess.Popen"):
        result = runner.invoke(
            app,
            [
                "batch", str(url_file),
                "--output", str(out_dir),
                "--async",
                "--format", "json",
                "--concurrency", "3",
                "--parser", "readability",
            ],
        )

    assert result.exit_code == 0
    job_id = result.output.strip()
    job = jobs_mod.get_job(job_id, db_path=tmp_jobs_db)
    params = json.loads(job["params"])
    assert params["format"] == "json"
    assert params["concurrency"] == 3
    assert params["parser"] == "readability"
    assert len(params["urls"]) == 3
