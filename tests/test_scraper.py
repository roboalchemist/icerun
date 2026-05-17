"""Tests for scraper module."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from icerun.scraper import FetchResult, DomainRateLimiter, fetch, fetch_many
from icerun.browser import BrowserResult


def test_fetch_result_dataclass():
    r = FetchResult(url="http://x.com", final_url="http://x.com", status_code=200, content_type="text/html", content=b"hello")
    assert r.url == "http://x.com"
    assert r.error is None


def test_domain_rate_limiter_init():
    rl = DomainRateLimiter(requests_per_second=5.0)
    assert rl.rps == 5.0


@pytest.mark.asyncio
async def test_fetch_real_url():
    """Live test — fetches example.com to verify curl_cffi works."""
    result = await fetch("https://example.com", timeout=15)
    assert result.status_code == 200
    assert b"Example" in result.content
    assert result.error is None


@pytest.mark.asyncio
async def test_fetch_bad_url_returns_error():
    result = await fetch("https://this-domain-does-not-exist-xyz.invalid", timeout=5, retries=1)
    assert result.status_code == 0
    assert result.error is not None


@pytest.mark.asyncio
async def test_fetch_many():
    results = await fetch_many(
        ["https://example.com", "https://httpbin.org/status/200"],
        timeout=15,
        concurrency=2,
    )
    assert len(results) == 2
    ok = [r for r in results if r.status_code == 200]
    assert len(ok) >= 1


# ---------------------------------------------------------------------------
# use_browser=True path tests (mocked — no live browser)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_use_browser_returns_fetch_result():
    """fetch(use_browser=True) returns a FetchResult by wrapping BrowserResult."""
    fake_browser_result = BrowserResult(
        url="https://example.com",
        html="<html><body>JS rendered</body></html>",
        content=b"<html><body>JS rendered</body></html>",
        status_code=200,
        headers={"content-type": "text/html"},
        screenshot_bytes=None,
        error=None,
    )
    with patch("icerun.scraper.fetch.__wrapped__", create=True), \
         patch("icerun.browser.browser_fetch", new=AsyncMock(return_value=fake_browser_result)):
        # Patch browser_fetch inside icerun.scraper's dynamic import
        import icerun.browser as _browser_mod
        original = _browser_mod.browser_fetch
        _browser_mod.browser_fetch = AsyncMock(return_value=fake_browser_result)
        try:
            result = await fetch("https://example.com", use_browser=True)
            assert isinstance(result, FetchResult)
            assert result.url == "https://example.com"
            assert result.status_code == 200
            assert result.content == b"<html><body>JS rendered</body></html>"
            assert result.content_type == "text/html"
        finally:
            _browser_mod.browser_fetch = original


@pytest.mark.asyncio
async def test_fetch_use_browser_passes_params():
    """fetch(use_browser=True) passes proxy, timeout, actions, headless, screenshot to browser_fetch."""
    fake_browser_result = BrowserResult(
        url="https://example.com",
        html="<html></html>",
        content=b"<html></html>",
        status_code=200,
        headers={},
    )
    import icerun.browser as _browser_mod
    mock_bf = AsyncMock(return_value=fake_browser_result)
    original = _browser_mod.browser_fetch
    _browser_mod.browser_fetch = mock_bf
    try:
        await fetch(
            "https://example.com",
            proxy="http://proxy:3128",
            timeout=10,
            actions=["click:#btn", "scroll:bottom"],
            headless=False,
            screenshot=True,
            use_browser=True,
        )
        mock_bf.assert_called_once_with(
            "https://example.com",
            proxy="http://proxy:3128",
            timeout=10,
            actions=["click:#btn", "scroll:bottom"],
            headless=False,
            screenshot=True,
        )
    finally:
        _browser_mod.browser_fetch = original


@pytest.mark.asyncio
async def test_fetch_use_browser_no_camoufox_raises_import_error():
    """fetch(use_browser=True) raises ImportError with helpful message when camoufox not installed."""
    import sys

    # Simulate camoufox not being installed by temporarily blocking the import
    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    # Patch the icerun.browser module import inside fetch to raise ImportError
    with patch.dict(sys.modules, {"icerun.browser": None}):
        with pytest.raises(ImportError, match="camoufox"):
            await fetch("https://example.com", use_browser=True)
