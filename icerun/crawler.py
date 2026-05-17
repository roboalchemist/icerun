"""BFS link crawler and sitemap URL discovery."""
import asyncio
import fnmatch
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from urllib.parse import urldefrag, urlparse


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
    delay: float = 1.0,
    concurrency: int = 3,
    ignore_robots: bool = False,
) -> AsyncIterator[CrawlResult]:
    """Crawl a site via BFS link following.

    Yields CrawlResult for each successfully fetched page.  Stops when
    *limit* pages have been yielded or the BFS queue is exhausted.
    """
    from icerun import scraper
    from icerun.parser import _extract_links  # reuse existing link extractor
    from icerun.scraper import DomainRateLimiter

    rps = 1.0 / delay if delay > 0 else 1000.0
    rate_limiter = DomainRateLimiter(requests_per_second=rps)

    start_parsed = urlparse(start_url)
    base_netloc = start_parsed.netloc

    # -----------------------------------------------------------------------
    # robots.txt handling
    # -----------------------------------------------------------------------
    robot_parser = None
    if not ignore_robots:
        import urllib.robotparser as urobot

        robots_url = f"{start_parsed.scheme}://{start_parsed.netloc}/robots.txt"
        robots_result = await scraper.fetch(robots_url, rate_limiter=rate_limiter, retries=1)
        if not robots_result.error and robots_result.status_code == 200:
            rp = urobot.RobotFileParser()
            lines = robots_result.content.decode("utf-8", errors="replace").splitlines()
            rp.parse(lines)
            robot_parser = rp

    # -----------------------------------------------------------------------
    # Helper: normalise a URL (strip fragment, lowercase scheme+host)
    # -----------------------------------------------------------------------
    def _normalize(url: str) -> str:
        defragged, _ = urldefrag(url)
        parsed = urlparse(defragged)
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
        )
        return normalized.geturl()

    # -----------------------------------------------------------------------
    # Helper: decide whether to follow a URL
    # -----------------------------------------------------------------------
    def _should_follow(url: str) -> bool:
        if same_domain and urlparse(url).netloc != base_netloc:
            return False
        if include and not any(fnmatch.fnmatch(url, pat) for pat in include):
            return False
        if exclude and any(fnmatch.fnmatch(url, pat) for pat in exclude):
            return False
        if robot_parser and not robot_parser.can_fetch("*", url):
            return False
        return True

    # -----------------------------------------------------------------------
    # BFS using asyncio.Queue
    # -----------------------------------------------------------------------
    visited: set[str] = set()
    queue: asyncio.Queue = asyncio.Queue()
    yielded_count = 0

    norm_start = _normalize(start_url)
    visited.add(norm_start)
    await queue.put((start_url, 0))

    # We use a shared counter and event to signal workers to stop.
    done_event = asyncio.Event()
    # Use an asyncio.Queue as a result channel from workers back to the main loop.
    result_queue: asyncio.Queue = asyncio.Queue()

    async def _worker() -> None:
        while not done_event.is_set():
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                # Brief yield so other workers / the main loop can run.
                await asyncio.sleep(0.01)
                continue

            url, current_depth = item
            try:
                fetch_result = await scraper.fetch(url, rate_limiter=rate_limiter)
                if fetch_result.error or fetch_result.status_code == 0:
                    queue.task_done()
                    continue

                # Extract links from page
                links = _extract_links(fetch_result.content, url)

                # Enqueue new links (only if we haven't reached max depth).
                # depth=N means: fetch up to N hops from start (depths 0..N-1).
                # Links discovered at depth N-1 would land at depth N, which is
                # beyond the limit, so we don't enqueue them.
                if current_depth < depth - 1:
                    for link in links:
                        norm = _normalize(link)
                        if norm not in visited and _should_follow(link):
                            visited.add(norm)
                            await queue.put((link, current_depth + 1))

                await result_queue.put(CrawlResult(
                    url=url,
                    depth=current_depth,
                    content=fetch_result.content,
                    links=links,
                ))
            except Exception:
                pass
            finally:
                queue.task_done()

    # Start worker pool
    workers = [asyncio.ensure_future(_worker()) for _ in range(concurrency)]

    try:
        while yielded_count < limit:
            # Check if there's a result ready
            try:
                result = result_queue.get_nowait()
                yield result
                yielded_count += 1
                continue
            except asyncio.QueueEmpty:
                pass

            # Check if all work is done
            if queue.empty() and result_queue.empty():
                # Give workers a moment to finish any in-flight items
                await asyncio.sleep(0.05)
                if queue.empty() and result_queue.empty():
                    break

            await asyncio.sleep(0.01)

        # Drain any remaining results up to the limit
        while yielded_count < limit and not result_queue.empty():
            try:
                result = result_queue.get_nowait()
                yield result
                yielded_count += 1
            except asyncio.QueueEmpty:
                break

    finally:
        done_event.set()
        for w in workers:
            w.cancel()
        # Suppress CancelledError from cancelled workers
        await asyncio.gather(*workers, return_exceptions=True)


async def map_site(url: str, limit: int = 1000) -> list[str]:
    """Discover all URLs on a site via sitemap.xml or link crawl."""
    raise NotImplementedError("crawler.map_site not yet implemented")
