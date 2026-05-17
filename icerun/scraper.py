"""HTTP fetching layer — curl_cffi async client with TLS impersonation and proxy support."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import chardet
from curl_cffi.requests import AsyncSession, RequestsError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    content: bytes
    headers: dict = field(default_factory=dict)
    error: Optional[str] = None


class DomainRateLimiter:
    """Per-domain asyncio rate limiter."""

    def __init__(self, requests_per_second: float = 2.0) -> None:
        self.rps = requests_per_second
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, url: str) -> None:
        domain = urlparse(url).netloc
        async with self._lock(domain):
            wait = (1.0 / self.rps) - (time.monotonic() - self._last[domain])
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()


_default_rate_limiter = DomainRateLimiter(requests_per_second=2.0)

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


async def fetch(
    url: str,
    proxy: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: int = 30,
    retries: int = 3,
    impersonate: str = "chrome",
    rate_limiter: Optional[DomainRateLimiter] = None,
    use_browser: bool = False,
    headless: bool = True,
    screenshot: bool = False,
    actions: Optional[list] = None,
) -> FetchResult:
    """Fetch a URL using curl_cffi with TLS impersonation and optional proxy.

    When use_browser=True, delegates to browser_fetch (requires camoufox to be
    installed via ``uv sync --extra browser``).
    """
    if use_browser:
        try:
            from icerun.browser import browser_fetch
        except ImportError:
            raise ImportError(
                "camoufox is not installed. Install with: uv sync --extra browser\n"
                "Then fetch the Firefox binary: python -m camoufox fetch"
            )
        browser_result = await browser_fetch(
            url,
            proxy=proxy,
            timeout=timeout,
            actions=actions,
            headless=headless,
            screenshot=screenshot,
        )
        return FetchResult(
            url=browser_result.url,
            final_url=browser_result.url,
            status_code=browser_result.status_code,
            content_type="text/html",
            content=browser_result.content,
            headers=browser_result.headers,
            error=browser_result.error,
        )

    rl = rate_limiter or _default_rate_limiter
    merged_headers = {**_DEFAULT_HEADERS, **(headers or {})}

    async with AsyncSession(
        impersonate=impersonate,
        default_encoding=lambda content: chardet.detect(content)["encoding"] or "utf-8",
    ) as session:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(retries),
                wait=wait_exponential(multiplier=1, min=1, max=30),
                retry=retry_if_exception_type((RequestsError, OSError)),
                reraise=True,
            ):
                with attempt:
                    await rl.acquire(url)
                    r = await session.get(
                        url,
                        proxy=proxy,
                        headers=merged_headers,
                        timeout=timeout,
                        allow_redirects=True,
                    )
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise RequestsError(f"HTTP {r.status_code}", 0, r)

            content_type = r.headers.get("content-type", "")
            return FetchResult(
                url=url,
                final_url=str(r.url),
                status_code=r.status_code,
                content_type=content_type,
                content=r.content,
                headers=dict(r.headers),
            )
        except Exception as e:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                content=b"",
                error=str(e),
            )


async def fetch_many(
    urls: list[str],
    proxy: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: int = 30,
    retries: int = 3,
    concurrency: int = 5,
    rate_limiter: Optional[DomainRateLimiter] = None,
) -> list[FetchResult]:
    """Fetch multiple URLs concurrently with a semaphore cap."""
    sem = asyncio.Semaphore(concurrency)
    rl = rate_limiter or DomainRateLimiter()

    async def _fetch_one(url: str) -> FetchResult:
        async with sem:
            return await fetch(url, proxy=proxy, headers=headers, timeout=timeout, retries=retries, rate_limiter=rl)

    return list(await asyncio.gather(*[_fetch_one(u) for u in urls]))
