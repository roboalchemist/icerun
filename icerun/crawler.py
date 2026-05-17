"""BFS link crawler and sitemap URL discovery."""
import asyncio
import fnmatch
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from urllib.parse import urldefrag, urlparse


_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


@dataclass
class DiscoveredURL:
    url: str
    source: str  # "sitemap" or "crawl"
    depth: int
    parent: Optional[str]
    last_modified: Optional[str] = None


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
    # Track items dequeued but not yet yielded (in-flight fetches).
    in_flight = 0

    norm_start = _normalize(start_url)
    visited.add(norm_start)
    await queue.put((start_url, 0))

    # We use a shared counter and event to signal workers to stop.
    done_event = asyncio.Event()
    # Use an asyncio.Queue as a result channel from workers back to the main loop.
    result_queue: asyncio.Queue = asyncio.Queue()
    # Lock guards in_flight counter mutations.
    in_flight_lock = asyncio.Lock()

    async def _worker() -> None:
        nonlocal in_flight
        while not done_event.is_set():
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                # Brief yield so other workers / the main loop can run.
                await asyncio.sleep(0.01)
                continue

            async with in_flight_lock:
                in_flight += 1

            url, current_depth = item
            try:
                fetch_result = await scraper.fetch(url, rate_limiter=rate_limiter)
                if fetch_result.error or fetch_result.status_code == 0:
                    return_item = None
                else:
                    # Extract links from page
                    links = _extract_links(fetch_result.content, url)

                    # Enqueue new links only if we haven't hit the depth cap.
                    # depth=1 means "seed + direct links", depth=N means N hops from seed.
                    if current_depth < depth:
                        for link in links:
                            norm = _normalize(link)
                            if norm not in visited and _should_follow(link):
                                visited.add(norm)
                                await queue.put((link, current_depth + 1))

                    return_item = CrawlResult(
                        url=url,
                        depth=current_depth,
                        content=fetch_result.content,
                        links=links,
                    )
            except Exception:
                return_item = None
            finally:
                queue.task_done()
                async with in_flight_lock:
                    in_flight -= 1
                if return_item is not None:
                    await result_queue.put(return_item)

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

            # All work is done when both queues are empty AND no fetch is in-flight.
            async with in_flight_lock:
                all_idle = queue.empty() and result_queue.empty() and in_flight == 0
            if all_idle:
                break

            await asyncio.sleep(0.05)

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


def _parse_sitemap_xml(
    content: bytes,
    sitemap_url: str,
) -> tuple[list[str], list[tuple[str, Optional[str]]]]:
    """Parse sitemap XML content.

    Returns:
        (child_sitemap_urls, [(page_url, last_modified), ...])
        child_sitemap_urls — if root is <sitemapindex>, these need recursive fetching
        page_url entries   — if root is <urlset>
    """
    # Decompress gzip if needed
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], []

    ns = _SITEMAP_NS
    tag = root.tag  # may be {ns}sitemapindex or {ns}urlset (or without ns)

    child_sitemaps: list[str] = []
    page_entries: list[tuple[str, Optional[str]]] = []

    if "sitemapindex" in tag:
        # Index: collect child sitemap URLs
        for sitemap_el in root.iter(f"{{{ns}}}sitemap"):
            loc_el = sitemap_el.find(f"{{{ns}}}loc")
            if loc_el is not None and loc_el.text:
                child_sitemaps.append(loc_el.text.strip())
    else:
        # urlset: collect page URLs
        for url_el in root.iter(f"{{{ns}}}url"):
            loc_el = url_el.find(f"{{{ns}}}loc")
            if loc_el is None or not loc_el.text:
                continue
            lastmod_el = url_el.find(f"{{{ns}}}lastmod")
            lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
            page_entries.append((loc_el.text.strip(), lastmod))

    return child_sitemaps, page_entries


async def _fetch_sitemap_urls(
    sitemap_url: str,
    base_netloc: str,
    limit: int,
    filter_pattern: Optional[str],
    results: list[DiscoveredURL],
    fetched_sitemaps: set[str],
) -> None:
    """Recursively fetch and parse a sitemap URL, appending to results."""
    from icerun import scraper

    if sitemap_url in fetched_sitemaps:
        return
    fetched_sitemaps.add(sitemap_url)

    fetch_result = await scraper.fetch(sitemap_url, retries=1)
    if fetch_result.error or fetch_result.status_code != 200:
        return

    child_sitemaps, page_entries = _parse_sitemap_xml(fetch_result.content, sitemap_url)

    # Recurse into child sitemaps first
    for child_url in child_sitemaps:
        if len(results) >= limit:
            return
        await _fetch_sitemap_urls(
            child_url, base_netloc, limit, filter_pattern, results, fetched_sitemaps
        )

    # Add page URLs from this sitemap
    for page_url, lastmod in page_entries:
        if len(results) >= limit:
            return
        # Same-domain filter
        parsed = urlparse(page_url)
        if parsed.netloc and parsed.netloc != base_netloc:
            continue
        # Filter pattern
        if filter_pattern and not fnmatch.fnmatch(page_url, filter_pattern):
            continue
        results.append(DiscoveredURL(
            url=page_url,
            source="sitemap",
            depth=0,
            parent=sitemap_url,
            last_modified=lastmod,
        ))


async def _bfs_map(
    start_url: str,
    limit: int,
    depth: int,
    filter_pattern: Optional[str],
) -> list[DiscoveredURL]:
    """BFS link-follow map (no content retained, links only)."""
    from icerun import scraper
    from icerun.parser import _extract_links

    start_parsed = urlparse(start_url)
    base_netloc = start_parsed.netloc

    visited: set[str] = set()
    results: list[DiscoveredURL] = []

    queue: asyncio.Queue = asyncio.Queue()
    norm_start, _ = urldefrag(start_url)
    visited.add(norm_start)
    await queue.put((start_url, 0, None))  # (url, depth, parent)

    while not queue.empty() and len(results) < limit:
        url, current_depth, parent = await queue.get()

        fetch_result = await scraper.fetch(url, retries=1)
        if fetch_result.error or fetch_result.status_code == 0:
            continue

        # Apply filter
        if filter_pattern and not fnmatch.fnmatch(url, filter_pattern):
            pass
        else:
            results.append(DiscoveredURL(
                url=url,
                source="crawl",
                depth=current_depth,
                parent=parent,
                last_modified=None,
            ))
            if len(results) >= limit:
                break

        # Enqueue links if within depth
        if current_depth < depth - 1:
            links = _extract_links(fetch_result.content, url)
            for link in links:
                defragged, _ = urldefrag(link)
                if defragged in visited:
                    continue
                parsed = urlparse(defragged)
                if parsed.netloc != base_netloc:
                    continue
                visited.add(defragged)
                await queue.put((defragged, current_depth + 1, url))

    return results


async def map_site(
    url: str,
    limit: int = 1000,
    filter_pattern: Optional[str] = None,
    crawl_fallback: bool = False,
    depth: int = 5,
) -> list[DiscoveredURL]:
    """Discover all URLs on a site via sitemap.xml or BFS link crawl.

    Primary strategy: parse sitemap.xml (discovered via direct URL or robots.txt).
    Fallback: BFS crawl (only when crawl_fallback=True and sitemap returns 0 URLs).
    """
    from icerun import scraper

    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    base = f"{scheme}://{netloc}"
    results: list[DiscoveredURL] = []
    fetched_sitemaps: set[str] = set()

    # --- Step 1: Try /sitemap.xml directly ---
    sitemap_url = f"{base}/sitemap.xml"
    sitemap_result = await scraper.fetch(sitemap_url, retries=1)
    if not sitemap_result.error and sitemap_result.status_code == 200:
        await _fetch_sitemap_urls(
            sitemap_url, netloc, limit, filter_pattern, results, fetched_sitemaps
        )

    # --- Step 2: If sitemap.xml failed, try robots.txt for Sitemap directives ---
    if not results:
        robots_url = f"{base}/robots.txt"
        robots_result = await scraper.fetch(robots_url, retries=1)
        if not robots_result.error and robots_result.status_code == 200:
            robots_text = robots_result.content.decode("utf-8", errors="replace")
            for line in robots_text.splitlines():
                if len(results) >= limit:
                    break
                stripped = line.strip()
                if stripped.lower().startswith("sitemap:"):
                    directive_url = stripped[len("sitemap:"):].strip()
                    if directive_url:
                        await _fetch_sitemap_urls(
                            directive_url, netloc, limit, filter_pattern, results, fetched_sitemaps
                        )

    # --- Step 3: BFS crawl fallback ---
    if not results and crawl_fallback:
        results = await _bfs_map(url, limit, depth, filter_pattern)

    return results
