"""Tests for scraper module."""
import asyncio
import pytest
from icerun.scraper import FetchResult, DomainRateLimiter, fetch, fetch_many


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
