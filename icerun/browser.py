"""Optional camoufox browser integration for JS-rendered sites and screenshots."""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse


def _require_camoufox() -> None:
    try:
        import camoufox  # noqa: F401
    except ImportError:
        raise ImportError(
            "camoufox is not installed. Install with: uv sync --extra browser\n"
            "Then fetch the Firefox binary: python -m camoufox fetch"
        )


def parse_proxy_for_browser(proxy_url: str) -> dict:
    """Convert proxy URL string to Playwright proxy dict."""
    p = urlparse(proxy_url)
    result: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        result["username"] = p.username
    if p.password:
        result["password"] = p.password
    return result


async def execute_action(page: object, action: str, timeout_ms: int = 30000) -> None:
    """Execute a single browser action string on a Playwright page."""
    if action.startswith("click:"):
        await page.click(action[6:], timeout=timeout_ms)
    elif action.startswith("scroll:"):
        target = action[7:]
        if target == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif target == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        else:
            px = int(target.rstrip("px"))
            await page.evaluate(f"window.scrollBy(0, {px})")
    elif action.startswith("fill:"):
        selector, _, value = action[5:].partition("=")
        await page.fill(selector.strip(), value, timeout=timeout_ms)
    elif action.startswith("wait:"):
        await asyncio.sleep(float(action[5:]))
    elif action.startswith("wait_for:"):
        await page.wait_for_selector(action[9:], timeout=timeout_ms)
    else:
        raise ValueError(f"Unknown browser action: {action!r}")


# Import FetchResult here to avoid circular imports — scraper is a core module
from icerun.scraper import FetchResult


async def browser_fetch(
    url: str,
    proxy: Optional[str] = None,
    timeout: int = 30,
    actions: Optional[list[str]] = None,
    headless: bool = True,
    screenshot: bool = False,
) -> tuple[FetchResult, Optional[bytes]]:
    """
    Fetch a URL using camoufox browser with optional proxy and page actions.

    Returns (FetchResult, screenshot_bytes) where screenshot_bytes is None
    unless screenshot=True was requested.
    """
    _require_camoufox()
    from camoufox import AsyncCamoufox

    timeout_ms = timeout * 1000
    proxy_dict = parse_proxy_for_browser(proxy) if proxy else None

    screenshot_bytes: Optional[bytes] = None

    async with AsyncCamoufox(headless=headless, proxy=proxy_dict) as browser:
        context = await browser.new_context()
        try:
            page = await context.new_page()

            # Navigate
            try:
                response = await page.goto(
                    url, wait_until="networkidle", timeout=timeout_ms
                )
            except Exception:
                # Fallback to "load" for pages that never reach networkidle
                response = await page.goto(
                    url, wait_until="load", timeout=timeout_ms
                )

            status_code = response.status if response else 0
            final_url = page.url

            # Execute pre-scrape actions
            for action in (actions or []):
                await execute_action(page, action, timeout_ms=timeout_ms)

            # Capture content
            html_str = await page.content()
            content = html_str.encode("utf-8", errors="replace")

            if screenshot:
                screenshot_bytes = await page.screenshot(full_page=True, type="png")

            result = FetchResult(
                url=url,
                final_url=final_url,
                status_code=status_code,
                content_type="text/html",
                content=content,
            )
        finally:
            await context.close()

    return result, screenshot_bytes
