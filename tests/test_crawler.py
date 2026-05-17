"""Tests for the BFS crawl engine."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from icerun.cli import app
from icerun.crawler import CrawlResult, crawl
from icerun.scraper import FetchResult

# ---------------------------------------------------------------------------
# Fixtures — 3-page site
#
# page_a  -> page_b, page_c
# page_b  -> page_d  (beyond depth=2)
# page_c  -> page_a  (cycle)
# page_d  -> (unreachable at depth=2)
# ---------------------------------------------------------------------------

PAGE_A = b"""
<html><head><title>Page A</title></head>
<body>
  <a href="https://example.com/b">B</a>
  <a href="https://example.com/c">C</a>
</body></html>
"""

PAGE_B = b"""
<html><head><title>Page B</title></head>
<body>
  <a href="https://example.com/d">D</a>
</body></html>
"""

PAGE_C = b"""
<html><head><title>Page C</title></head>
<body>
  <a href="https://example.com/a">A again</a>
</body></html>
"""

PAGE_D = b"""
<html><head><title>Page D</title></head>
<body><p>Deep page</p></body></html>
"""

ROBOTS_EMPTY = b"User-agent: *\nDisallow:\n"


def _fr(url: str, content: bytes, status: int = 200) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=status,
        content_type="text/html",
        content=content,
    )


def _make_fetch_mock(pages: dict[str, bytes], robots_content: bytes = ROBOTS_EMPTY):
    """Return an AsyncMock for scraper.fetch keyed on URL."""
    async def _fetch(url, **kwargs):
        if "/robots.txt" in url:
            return _fr(url, robots_content)
        content = pages.get(url, b"<html></html>")
        return _fr(url, content)

    return AsyncMock(side_effect=_fetch)


PAGES = {
    "https://example.com/a": PAGE_A,
    "https://example.com/b": PAGE_B,
    "https://example.com/c": PAGE_C,
    "https://example.com/d": PAGE_D,
}


# ---------------------------------------------------------------------------
# Helper to collect crawl results
# ---------------------------------------------------------------------------

async def _collect(gen) -> list[CrawlResult]:
    results = []
    async for r in gen:
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crawl_basic():
    """3 pages are yielded at depth=2 (a, b, c — not d)."""
    mock_fetch = _make_fetch_mock(PAGES)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=2, limit=100,
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    urls = {r.url for r in results}
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls
    assert "https://example.com/c" in urls
    assert len(results) == 3


@pytest.mark.asyncio
async def test_crawl_depth_limit():
    """page_d should NOT be fetched at depth=2."""
    mock_fetch = _make_fetch_mock(PAGES)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=2, limit=100,
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    urls = {r.url for r in results}
    assert "https://example.com/d" not in urls


@pytest.mark.asyncio
async def test_crawl_limit():
    """limit=2 stops after 2 URLs are yielded."""
    mock_fetch = _make_fetch_mock(PAGES)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=3, limit=2,
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    assert len(results) == 2


@pytest.mark.asyncio
async def test_crawl_same_domain_filter():
    """External URLs are not followed when same_domain=True."""
    pages_with_external = dict(PAGES)
    pages_with_external["https://example.com/a"] = (
        b'<html><body>'
        b'<a href="https://example.com/b">B</a>'
        b'<a href="https://other.com/external">External</a>'
        b'</body></html>'
    )
    mock_fetch = _make_fetch_mock(pages_with_external)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=2, limit=100,
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    urls = {r.url for r in results}
    assert "https://other.com/external" not in urls


@pytest.mark.asyncio
async def test_crawl_visited_dedup():
    """page_a is not re-fetched when encountered again via page_c -> page_a cycle."""
    mock_fetch = _make_fetch_mock(PAGES)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=3, limit=100,
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    # page_a should appear exactly once despite being linked from page_c
    a_results = [r for r in results if r.url == "https://example.com/a"]
    assert len(a_results) == 1


@pytest.mark.asyncio
async def test_crawl_exclude_pattern():
    """--exclude '*/skip*' filters out URLs matching the pattern."""
    pages_with_skip = dict(PAGES)
    pages_with_skip["https://example.com/a"] = (
        b'<html><body>'
        b'<a href="https://example.com/b">B</a>'
        b'<a href="https://example.com/skip-me">Skip</a>'
        b'</body></html>'
    )
    pages_with_skip["https://example.com/skip-me"] = b"<html><body>skip content</body></html>"
    mock_fetch = _make_fetch_mock(pages_with_skip)
    with patch("icerun.scraper.fetch", new=mock_fetch):
        results = await _collect(
            crawl("https://example.com/a", depth=2, limit=100,
                  exclude=["*/skip*"],
                  same_domain=True, delay=0, concurrency=1, ignore_robots=True)
        )
    urls = {r.url for r in results}
    assert "https://example.com/skip-me" not in urls
    assert "https://example.com/b" in urls


# ---------------------------------------------------------------------------
# CLI integration test
# ---------------------------------------------------------------------------

runner = CliRunner()


def test_crawl_command_creates_output(tmp_path):
    """crawl command creates output dir and writes manifest.json."""
    out_dir = tmp_path / "crawl-out"

    crawl_results = [
        CrawlResult(
            url="https://example.com/a",
            depth=0,
            content=PAGE_A,
            links=["https://example.com/b"],
        ),
        CrawlResult(
            url="https://example.com/b",
            depth=1,
            content=PAGE_B,
            links=[],
        ),
    ]

    async def _fake_crawl(*args, **kwargs):
        for r in crawl_results:
            yield r

    with patch("icerun.crawler.crawl", new=_fake_crawl):
        result = runner.invoke(app, [
            "crawl", "https://example.com/a",
            "--output", str(out_dir),
            "--depth", "1",
            "--ignore-robots",
        ])

    assert result.exit_code == 0, result.output
    assert out_dir.exists()
    manifest_file = out_dir / "manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text())
    assert "https://example.com/a" in manifest
    assert "https://example.com/b" in manifest
