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


def test_batch_stub_exits_1():
    result = runner.invoke(app, ["batch", "/nonexistent-file.txt"])
    assert result.exit_code == 1


def test_map_stub_exits_1():
    result = runner.invoke(app, ["map", "https://example.com"])
    assert result.exit_code == 1


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
