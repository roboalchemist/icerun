"""Tests for icerun/search.py and the search CLI command — all mocked, no live network."""
from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from icerun.cli import app
from icerun.scraper import FetchResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_serper_response(items: list[dict], status_code: int = 200):
    """Build a fake httpx.Response-like object for Serper."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"organic": items}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


_SERPER_ORGANIC = [
    {"title": "T", "link": "https://x.com", "snippet": "S", "position": 1}
]


# ---------------------------------------------------------------------------
# Unit tests for search module
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_serper_success():
    """_serper_search maps organic results to common schema."""
    from icerun.search import _serper_search

    mock_resp = _make_serper_response(_SERPER_ORGANIC)
    mock_post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient.post", mock_post):
        results = await _serper_search("test query", 10, "fake-key")

    assert len(results) == 1
    r = results[0]
    assert r["url"] == "https://x.com"
    assert r["title"] == "T"
    assert r["description"] == "S"
    assert r["rank"] == 1


@pytest.mark.asyncio
async def test_serper_429_falls_back_to_ddg():
    """search() falls back to DDG when Serper returns 429."""
    from icerun import search as search_mod

    mock_resp_429 = _make_serper_response([], status_code=429)
    mock_post = AsyncMock(return_value=mock_resp_429)

    ddg_items = [{"title": "DDG Result", "href": "https://ddg.com", "body": "DDG snippet"}]

    with patch("httpx.AsyncClient.post", mock_post):
        with patch("icerun.search._ddg_search", return_value=[
            {"url": "https://ddg.com", "title": "DDG Result", "description": "DDG snippet", "rank": 1}
        ]) as mock_ddg:
            results = await search_mod.search("test query", limit=5, api_key="quota-exceeded-key")

    mock_ddg.assert_called_once()
    assert len(results) == 1
    assert results[0]["url"] == "https://ddg.com"


@pytest.mark.asyncio
async def test_serper_401_raises():
    """_serper_search raises ValueError with helpful message on 401."""
    from icerun.search import _serper_search

    mock_resp = _make_serper_response([], status_code=401)
    mock_post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient.post", mock_post):
        with pytest.raises(ValueError, match="invalid API key"):
            await _serper_search("test query", 10, "bad-key")


def test_ddg_no_ddgs_package():
    """_ddg_search raises ImportError with install hint when ddgs not installed."""
    from icerun.search import _ddg_search

    with patch.dict(sys.modules, {"duckduckgo_search": None}):
        with pytest.raises(ImportError, match="uv sync --extra search"):
            _ddg_search("test query", 10)


@pytest.mark.asyncio
async def test_search_no_key_uses_ddg():
    """search() uses DDG when api_key is None."""
    from icerun import search as search_mod

    ddg_result = [{"url": "https://ddg.com", "title": "DDG", "description": "desc", "rank": 1}]
    with patch("icerun.search._ddg_search", return_value=ddg_result) as mock_ddg:
        results = await search_mod.search("test query", api_key=None)

    mock_ddg.assert_called_once_with("test query", 10)
    assert results == ddg_result


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def _mock_search_results(n: int = 2) -> list[dict]:
    return [
        {
            "url": f"https://example.com/{i}",
            "title": f"Title {i}",
            "description": f"Description {i}",
            "rank": i,
        }
        for i in range(1, n + 1)
    ]


def test_search_cli_json():
    """search --format json returns valid JSON list with expected fields."""
    fake_results = _mock_search_results(2)

    with patch("icerun.search.search", new=AsyncMock(return_value=fake_results)):
        result = runner.invoke(app, ["search", "test query", "--format", "json"])

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["url"] == "https://example.com/1"
    assert data[0]["title"] == "Title 1"
    assert data[0]["description"] == "Description 1"
    assert data[0]["rank"] == 1


def test_search_cli_lines():
    """search --format lines returns one URL per line."""
    fake_results = _mock_search_results(3)

    with patch("icerun.search.search", new=AsyncMock(return_value=fake_results)):
        result = runner.invoke(app, ["search", "test query", "--format", "lines"])

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    lines = [l for l in result.output.strip().splitlines() if l]
    assert len(lines) == 3
    assert lines[0] == "https://example.com/1"
    assert lines[1] == "https://example.com/2"
    assert lines[2] == "https://example.com/3"


def test_search_cli_markdown():
    """search --format markdown returns ## headers with URL and description."""
    fake_results = _mock_search_results(1)

    with patch("icerun.search.search", new=AsyncMock(return_value=fake_results)):
        result = runner.invoke(app, ["search", "test query", "--format", "markdown"])

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    assert "## Title 1" in result.output
    assert "https://example.com/1" in result.output
    assert "Description 1" in result.output


def test_search_cli_scrape():
    """search --scrape fetches each URL and adds markdown field to results."""
    fake_results = _mock_search_results(2)

    html_content = b"<html><body><p>Scraped content</p></body></html>"
    fake_fetch_result = FetchResult(
        url="https://example.com/1",
        final_url="https://example.com/1",
        status_code=200,
        content_type="text/html",
        content=html_content,
        error=None,
    )
    fake_fetch_results = [fake_fetch_result, fake_fetch_result]

    with patch("icerun.search.search", new=AsyncMock(return_value=fake_results)):
        with patch("icerun.scraper.fetch_many", new=AsyncMock(return_value=fake_fetch_results)):
            result = runner.invoke(
                app, ["search", "test query", "--scrape", "--format", "json"]
            )

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    # Both results should have a markdown field from scraping
    for item in data:
        assert "markdown" in item


def test_search_cli_output_file(tmp_path):
    """search --output writes to file instead of stdout."""
    fake_results = _mock_search_results(1)
    out_file = tmp_path / "results.json"

    with patch("icerun.search.search", new=AsyncMock(return_value=fake_results)):
        result = runner.invoke(
            app, ["search", "test query", "--format", "json", "--output", str(out_file)]
        )

    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception}"
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(data) == 1
