"""Web search via Serper API (primary) with DuckDuckGo fallback."""
from __future__ import annotations

from typing import Optional

import httpx


RESULT_SCHEMA = ["url", "title", "description", "rank"]  # guaranteed keys in output


async def _serper_search(query: str, limit: int, api_key: str) -> list[dict]:
    """POST https://google.serper.dev/search, map organic results to common format.

    Raises ValueError on 401 (bad key).
    Returns [] on 429 (quota exceeded — triggers fallback in caller).
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": limit},
            timeout=30,
        )

    if response.status_code == 401:
        raise ValueError("Serper API returned 401: invalid API key")
    if response.status_code == 429:
        # Quota exceeded — caller should fall back to DDG
        return []

    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("organic", []):
        results.append({
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "description": item.get("snippet", ""),
            "rank": item.get("position", len(results) + 1),
        })
    return results[:limit]


def _ddg_search(query: str, limit: int) -> list[dict]:
    """DuckDuckGo via ddgs library (soft optional dep). Synchronous call."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise ImportError("ddgs not installed. Install with: uv sync --extra search")

    results = []
    for i, item in enumerate(DDGS().text(query, max_results=limit), start=1):
        results.append({
            "url": item.get("href", ""),
            "title": item.get("title", ""),
            "description": item.get("body", ""),
            "rank": i,
        })
    return results[:limit]


async def search(
    query: str,
    limit: int = 10,
    api_key: str | None = None,
) -> list[dict]:
    """Search the web and return a list of results.

    Returns list of {"url", "title", "description", "rank"} dicts.
    Uses Serper if api_key is set, falls back to DDG on 429 or if no key.
    """
    if api_key:
        results = await _serper_search(query, limit, api_key)
        if results:
            return results
        # Empty list from _serper_search means 429 quota exceeded — fall through to DDG

    # No key or quota exceeded — use DDG
    return _ddg_search(query, limit)
