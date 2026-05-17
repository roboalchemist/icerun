"""Shared pytest fixtures for icerun tests."""
import pytest


@pytest.fixture
def tmp_jobs_db(tmp_path, monkeypatch):
    """Provide a temporary jobs.db path via ICER_JOBS_DB env var."""
    db = str(tmp_path / "test_jobs.db")
    monkeypatch.setenv("ICER_JOBS_DB", db)
    return db
