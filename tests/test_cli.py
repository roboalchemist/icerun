"""Basic CLI invocation smoke tests."""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from icerun.cli import app
from icerun.scraper import FetchResult

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HTML_CONTENT = b"""
<html>
  <head><title>Test Page</title></head>
  <body>
    <h1>Hello World</h1>
    <p>Some text here.</p>
    <a href="https://example.com/link1">Link 1</a>
  </body>
</html>
"""

def _make_fetch_result(**kwargs) -> FetchResult:
    defaults = dict(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        content_type="text/html",
        content=HTML_CONTENT,
        headers={},
        error=None,
        screenshot_bytes=None,
    )
    defaults.update(kwargs)
    return FetchResult(**defaults)


# ---------------------------------------------------------------------------
# Help & misc
# ---------------------------------------------------------------------------

def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "scrape" in output
    assert "batch" in output
    assert "crawl" in output


# ---------------------------------------------------------------------------
# Scrape command tests
# ---------------------------------------------------------------------------

def test_scrape_markdown_default():
    mock_result = _make_fetch_result()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com"])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_scrape_html_format():
    mock_result = _make_fetch_result()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com", "--format", "html"])
    assert result.exit_code == 0
    # html format returns html or markdown fallback
    assert len(result.output) > 0


def test_scrape_json_format():
    mock_result = _make_fetch_result()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "url" in data
    assert "title" in data
    assert "markdown" in data
    assert "links" in data
    assert "metadata" in data


def test_scrape_links_format():
    mock_result = _make_fetch_result()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com", "--format", "links"])
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().splitlines() if l]
    assert len(lines) >= 1
    for line in lines:
        assert line.startswith("http")


def test_scrape_fetch_error():
    mock_result = _make_fetch_result(
        status_code=0,
        content=b"",
        error="connection refused",
    )
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com"])
    assert result.exit_code == 1


def test_scrape_output_file(tmp_path):
    out_file = tmp_path / "out.md"
    mock_result = _make_fetch_result()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        result = runner.invoke(app, ["scrape", "https://example.com", "--output", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()
    assert len(out_file.read_text()) > 0


def test_batch_missing_file():
    """Non-existent file → exit 1."""
    result = runner.invoke(app, ["batch", "/nonexistent-file.txt"])
    assert result.exit_code == 1


def test_batch_empty_file(tmp_path):
    """Empty URL file → exit 1."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text("# just a comment\n\n", encoding="utf-8")
    out_dir = tmp_path / "output"
    result = runner.invoke(app, ["batch", str(url_file), "--output", str(out_dir)])
    assert result.exit_code == 1


def test_batch_processes_urls(tmp_path):
    """Mock fetch for 2 URLs; verify 2 output files are created."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        "https://example.com/page1\nhttps://example.com/page2\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"

    mock_result1 = _make_fetch_result(url="https://example.com/page1", final_url="https://example.com/page1")
    mock_result2 = _make_fetch_result(url="https://example.com/page2", final_url="https://example.com/page2")

    call_count = 0

    async def mock_fetch(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "page1" in url:
            return mock_result1
        return mock_result2

    with patch("icerun.scraper.fetch", side_effect=mock_fetch):
        result = runner.invoke(app, ["batch", str(url_file), "--output", str(out_dir)])

    assert result.exit_code == 0, f"stdout: {result.output}\nexc: {result.exception}"
    output_files = list(out_dir.glob("*.md"))
    assert len(output_files) == 2
    assert call_count == 2


def test_batch_resume_skips_existing(tmp_path):
    """With --resume, skip URLs whose output file already exists."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        "https://example.com/page1\nhttps://example.com/page2\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    # Pre-create the output file for page1 (hash naming)
    import hashlib
    hash1 = hashlib.sha256("https://example.com/page1".encode()).hexdigest()[:16]
    (out_dir / f"{hash1}.md").write_text("already scraped", encoding="utf-8")

    call_count = 0

    async def mock_fetch(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_fetch_result(url=url, final_url=url)

    with patch("icerun.scraper.fetch", side_effect=mock_fetch):
        result = runner.invoke(
            app, ["batch", str(url_file), "--output", str(out_dir), "--resume"]
        )

    assert result.exit_code == 0, f"stdout: {result.output}\nexc: {result.exception}"
    # Only page2 should have been fetched
    assert call_count == 1


def test_batch_error_handling(tmp_path):
    """One URL fails → written to errors.txt; other URL still processes."""
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        "https://example.com/ok\nhttps://example.com/fail\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    errors_path = tmp_path / "errors.txt"

    async def mock_fetch(url, **kwargs):
        if "fail" in url:
            return _make_fetch_result(url=url, final_url=url, status_code=0, content=b"", error="connection error")
        return _make_fetch_result(url=url, final_url=url)

    with patch("icerun.scraper.fetch", side_effect=mock_fetch):
        result = runner.invoke(
            app,
            ["batch", str(url_file), "--output", str(out_dir), "--errors-file", str(errors_path)],
        )

    # exit 0 because at least 1 succeeded
    assert result.exit_code == 0, f"stdout: {result.output}\nexc: {result.exception}"
    # errors.txt should contain the failed URL
    assert errors_path.exists()
    errors_content = errors_path.read_text(encoding="utf-8")
    assert "https://example.com/fail" in errors_content
    # Successful URL should produce an output file
    output_files = list(out_dir.glob("*.md"))
    assert len(output_files) == 1


def test_map_cmd_lines_format():
    """map command with mocked map_site returns one URL per line."""
    from icerun.crawler import DiscoveredURL

    fake_results = [
        DiscoveredURL(url="https://example.com/page1", source="sitemap", depth=0, parent="https://example.com/sitemap.xml", last_modified="2024-01-01"),
        DiscoveredURL(url="https://example.com/page2", source="sitemap", depth=0, parent="https://example.com/sitemap.xml", last_modified=None),
    ]

    with patch("icerun.crawler.map_site", new=AsyncMock(return_value=fake_results)):
        result = runner.invoke(app, ["map", "https://example.com"])

    assert result.exit_code == 0, f"output: {result.output}, exc: {result.exception}"
    # Filter to only URL lines (typer CliRunner mixes stdout+stderr in result.output)
    url_lines = [l for l in result.output.strip().splitlines() if l.startswith("https://")]
    assert "https://example.com/page1" in url_lines
    assert "https://example.com/page2" in url_lines
    assert len(url_lines) == 2


def test_search_stub_exits_1():
    result = runner.invoke(app, ["search", "test query"])
    assert result.exit_code == 1


def test_job_help():
    result = runner.invoke(app, ["job", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output


def test_config_help():
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "show" in result.output


def test_config_show_runs():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "trafilatura" in result.output  # default parser


def test_config_set_invalid_key():
    result = runner.invoke(app, ["config", "set", "badkey", "value"])
    assert result.exit_code == 1
