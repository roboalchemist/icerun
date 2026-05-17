"""BFS link crawler and sitemap URL discovery."""
from dataclasses import dataclass
from typing import AsyncIterator, Optional


@dataclass
class CrawlResult:
    url: str
    depth: int
    content: bytes
    links: list[str]


async def crawl(
    start_url: str,
    depth: int = 3,
    limit: int = 100,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    same_domain: bool = True,
) -> AsyncIterator[CrawlResult]:
    """Crawl a site via BFS link following."""
    raise NotImplementedError("crawler.crawl not yet implemented")
    yield  # noqa: unreachable — makes this a generator


async def map_site(url: str, limit: int = 1000) -> list[str]:
    """Discover all URLs on a site via sitemap.xml or link crawl."""
    raise NotImplementedError("crawler.map_site not yet implemented")
