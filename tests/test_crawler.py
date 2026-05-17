"""Tests for the BFS crawl engine."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from icerun.cli import app
from icerun.crawler import CrawlResult, DiscoveredURL, crawl, map_site
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


# ===========================================================================
# map_site() tests
# ===========================================================================

SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/page2</loc></url>
  <url><loc>https://example.com/page3</loc><lastmod>2024-03-15</lastmod></url>
</urlset>"""

SITEMAP_INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_CHILD_1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a1</loc></url>
  <url><loc>https://example.com/a2</loc></url>
</urlset>"""

SITEMAP_CHILD_2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/b1</loc></url>
</urlset>"""

ROBOTS_WITH_SITEMAP = b"""User-agent: *
Disallow: /private/
Sitemap: https://example.com/sitemap.xml
"""


def _map_fr(url: str, content: bytes, status: int = 200) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=status,
        content_type="text/xml",
        content=content,
    )


def _map_404(url: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=404,
        content_type="text/html",
        content=b"Not Found",
    )


@pytest.mark.asyncio
async def test_map_site_sitemap_xml():
    """Direct /sitemap.xml with 3 <url> entries returns 3 DiscoveredURLs source=sitemap."""

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_fr(url, SITEMAP_XML)
        return _map_404(url)

    with patch("icerun.scraper.fetch", side_effect=_fetch):
        results = await map_site("https://example.com")

    assert len(results) == 3
    urls = {r.url for r in results}
    assert "https://example.com/page1" in urls
    assert "https://example.com/page2" in urls
    assert "https://example.com/page3" in urls
    for r in results:
        assert r.source == "sitemap"
    # Check lastmod populated for page1
    page1 = next(r for r in results if r.url == "https://example.com/page1")
    assert page1.last_modified == "2024-01-01"
    page2 = next(r for r in results if r.url == "https://example.com/page2")
    assert page2.last_modified is None


@pytest.mark.asyncio
async def test_map_site_sitemap_index():
    """sitemapindex root causes recursive child sitemap fetching."""

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_fr(url, SITEMAP_INDEX_XML)
        if url == "https://example.com/sitemap1.xml":
            return _map_fr(url, SITEMAP_CHILD_1)
        if url == "https://example.com/sitemap2.xml":
            return _map_fr(url, SITEMAP_CHILD_2)
        return _map_404(url)

    with patch("icerun.scraper.fetch", side_effect=_fetch):
        results = await map_site("https://example.com")

    urls = {r.url for r in results}
    assert "https://example.com/a1" in urls
    assert "https://example.com/a2" in urls
    assert "https://example.com/b1" in urls
    assert len(results) == 3
    for r in results:
        assert r.source == "sitemap"


@pytest.mark.asyncio
async def test_map_site_robots_txt_fallback():
    """/sitemap.xml 404 but /robots.txt has Sitemap: directive -> finds URLs."""

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_404(url)
        if url == "https://example.com/robots.txt":
            return _map_fr(url, ROBOTS_WITH_SITEMAP)
        # robots.txt references https://example.com/sitemap.xml (same URL as above)
        # but after the 404 branch won't recurse — provide the XML via the directive URL
        return _map_404(url)

    # The robots.txt Sitemap: directive points to /sitemap.xml which 404s too
    # We need a distinct URL for the directive to verify the robots.txt code path
    ROBOTS_CUSTOM = b"Sitemap: https://example.com/custom-sitemap.xml\n"
    CUSTOM_SITEMAP = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/via-robots</loc></url>
</urlset>"""

    async def _fetch2(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_404(url)
        if url == "https://example.com/robots.txt":
            return _map_fr(url, ROBOTS_CUSTOM)
        if url == "https://example.com/custom-sitemap.xml":
            return _map_fr(url, CUSTOM_SITEMAP)
        return _map_404(url)

    with patch("icerun.scraper.fetch", side_effect=_fetch2):
        results = await map_site("https://example.com")

    assert len(results) == 1
    assert results[0].url == "https://example.com/via-robots"
    assert results[0].source == "sitemap"


@pytest.mark.asyncio
async def test_map_site_filter_pattern():
    """filter_pattern='*/page1*' keeps only matching URLs."""

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_fr(url, SITEMAP_XML)
        return _map_404(url)

    with patch("icerun.scraper.fetch", side_effect=_fetch):
        results = await map_site("https://example.com", filter_pattern="*/page1*")

    assert len(results) == 1
    assert results[0].url == "https://example.com/page1"


@pytest.mark.asyncio
async def test_map_site_limit():
    """limit=2 stops collection after 2 URLs even if sitemap has more."""

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_fr(url, SITEMAP_XML)
        return _map_404(url)

    with patch("icerun.scraper.fetch", side_effect=_fetch):
        results = await map_site("https://example.com", limit=2)

    assert len(results) == 2


@pytest.mark.asyncio
async def test_map_site_crawl_fallback():
    """When sitemap 404 and crawl_fallback=True, BFS runs and returns URLs."""

    start_html = b"""<html><body>
    <a href="https://example.com/alpha">Alpha</a>
    <a href="https://example.com/beta">Beta</a>
    </body></html>"""

    page_html = b"<html><body><p>leaf page</p></body></html>"

    async def _fetch(url, **kwargs):
        if url == "https://example.com/sitemap.xml":
            return _map_404(url)
        if url == "https://example.com/robots.txt":
            return _map_404(url)
        # HTML pages for BFS
        return FetchResult(
            url=url, final_url=url, status_code=200,
            content_type="text/html",
            content=start_html if url == "https://example.com" else page_html,
        )

    with patch("icerun.scraper.fetch", side_effect=_fetch):
        results = await map_site(
            "https://example.com", crawl_fallback=True, depth=2, limit=10
        )

    assert len(results) >= 1
    for r in results:
        assert r.source == "crawl"
    urls = {r.url for r in results}
    assert "https://example.com" in urls
