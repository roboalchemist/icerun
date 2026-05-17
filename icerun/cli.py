import asyncio
import hashlib
import json
import re
import sys
import time
import typer
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path
from urllib.parse import urlparse

app = typer.Typer(
    name="icerun",
    help="Firecrawl-compatible web scraper — proxy rotation + local parsers",
    no_args_is_help=True,
)

job_app = typer.Typer(name="job", help="Manage async scraping jobs", no_args_is_help=True)
config_app = typer.Typer(name="config", help="Manage icerun configuration", no_args_is_help=True)

app.add_typer(job_app, name="job")
app.add_typer(config_app, name="config")


def _sanitize_for_json(obj: object) -> object:
    """Recursively convert an object to JSON-serializable types.

    Handles lxml _Element objects and any other non-serializable types by
    converting them to their string representation.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    # Fallback: convert to string (handles lxml _Element and other opaque types)
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _not_implemented(cmd: str) -> None:
    from rich.console import Console
    from rich.panel import Panel
    Console(stderr=True).print(Panel(f"[yellow]Not yet implemented: {cmd}[/yellow]"))
    raise typer.Exit(1)


# Extension mapping for output file naming
_FORMAT_EXT: dict[str, str] = {
    "markdown": ".md",
    "html": ".html",
    "json": ".json",
    "links": ".txt",
}


def _format_output(parse_result: object, url: str, format: str) -> str:
    """Convert a ParseResult to a text string for the given format.

    Shared by scrape and batch commands to avoid duplication.
    """
    from icerun import parser as parser_mod

    if format == "markdown":
        return parse_result.markdown or ""
    elif format == "html":
        return parse_result.html or parse_result.markdown or ""
    elif format == "json":
        return json.dumps(
            {
                "url": url,
                "title": parse_result.title,
                "markdown": parse_result.markdown,
                "links": parse_result.links,
                "metadata": _sanitize_for_json(parse_result.metadata),
            },
            ensure_ascii=False,
            indent=2,
        )
    elif format == "links":
        return "\n".join(parse_result.links)
    else:
        raise ValueError(f"Unknown format {format!r}")


@app.command()
def scrape(
    url: str = typer.Argument(..., help="URL to scrape"),
    format: str = typer.Option("markdown", "--format", "-f", help="Output: markdown|html|json|screenshot|links"),
    parser: str = typer.Option("trafilatura", "--parser", help="Parser: trafilatura|readability|html2text|markdownify|raw"),
    browser: bool = typer.Option(False, "--browser", help="Force camoufox browser mode"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    proxy: Optional[str] = typer.Option(None, "--proxy", help="Proxy URL"),
    timeout: int = typer.Option(30, "--timeout", help="Request timeout seconds"),
    metadata: bool = typer.Option(False, "--metadata", help="Include metadata header"),
    headers: Optional[List[str]] = typer.Option(None, "--header", "-H", help="Custom headers KEY:VALUE"),
    extract: Optional[str] = typer.Option(None, "--extract", help="JSON schema for structured extraction"),
    action: Optional[List[str]] = typer.Option(None, "--action", help="Browser actions: click:SEL, scroll:bottom"),
    wait: Optional[float] = typer.Option(None, "--wait", help="Seconds to wait after page load (browser mode)"),
) -> None:
    """Scrape a single URL and output content."""
    from icerun.config import load_config
    from icerun.proxy import ProxyPool
    import icerun.scraper as scraper
    from icerun import parser as parser_mod

    # 1. Load config; CLI flags override config defaults
    config, _ = load_config()

    # 2. Parse --header KEY:VALUE pairs into dict
    header_dict: dict = {}
    for h in (headers or []):
        key, _, val = h.partition(":")
        header_dict[key.strip()] = val.strip()

    # 3. Resolve proxy: CLI flag wins, else ProxyPool.from_env()
    proxy_url: Optional[str] = proxy
    if proxy_url is None:
        pool = ProxyPool.from_env()
        proxy_url = pool.get()

    # 4. Build actions list; inject wait action if --wait given
    action_list: list = list(action or [])
    if wait is not None:
        action_list.append(f"wait:{wait}")

    # 5. Fetch
    fetch_result = asyncio.run(
        scraper.fetch(
            url,
            proxy=proxy_url,
            headers=header_dict or None,
            timeout=timeout,
            use_browser=browser,
            actions=action_list or None,
            screenshot=(format == "screenshot"),
        )
    )

    if fetch_result.error:
        typer.echo(f"Error: {fetch_result.error}", err=True)
        raise typer.Exit(1)

    # 6. Format dispatch
    try:
        if format == "screenshot":
            if not fetch_result.screenshot_bytes:
                typer.echo("Error: no screenshot available (use --browser for screenshot support)", err=True)
                raise typer.Exit(1)
            if output:
                output.write_bytes(fetch_result.screenshot_bytes)
            else:
                sys.stdout.buffer.write(fetch_result.screenshot_bytes)
            return

        # Parse HTML for all text formats
        parse_result = parser_mod.parse(fetch_result.content, url, parser=parser, format=format)

        if format == "markdown":
            text_output = parse_result.markdown or ""
        elif format == "html":
            text_output = parse_result.html or parse_result.markdown or ""
        elif format == "json":
            text_output = json.dumps({
                "url": url,
                "title": parse_result.title,
                "markdown": parse_result.markdown,
                "links": parse_result.links,
                "metadata": _sanitize_for_json(parse_result.metadata),
            }, ensure_ascii=False, indent=2)
        elif format == "links":
            text_output = "\n".join(parse_result.links)
        else:
            typer.echo(f"Error: unknown format {format!r}", err=True)
            raise typer.Exit(2)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Parse error: {e}", err=True)
        raise typer.Exit(2)

    # 7. Prepend metadata header if requested
    if metadata:
        title = parse_result.title or ""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta_header = (
            f"---\n"
            f"url: {url}\n"
            f"title: {title}\n"
            f"parser: {parser}\n"
            f"timestamp: {ts}\n"
            f"---\n\n"
        )
        text_output = meta_header + text_output

    # 8. --extract: structured extraction via instructor (optional dep)
    if extract:
        try:
            import instructor
            import anthropic as anthropic_sdk
        except ImportError:
            typer.echo("Error: --extract requires 'instructor' package. Install with: uv sync --extra extract", err=True)
            raise typer.Exit(1)
        try:
            schema = json.loads(extract)
            from pydantic import create_model
            fields = {k: (str, ...) for k in schema.get("properties", {}).keys()}
            DynModel = create_model("Extracted", **fields)  # type: ignore[call-overload]
            llm_cfg = config.get("llm", {})
            client = instructor.from_anthropic(anthropic_sdk.Anthropic(api_key=llm_cfg.get("api_key") or None))
            extracted = client.chat.completions.create(
                model=llm_cfg.get("model", "claude-sonnet-4-6"),
                max_tokens=4096,
                messages=[{"role": "user", "content": f"Extract structured data from:\n\n{text_output}"}],
                response_model=DynModel,
            )
            text_output = json.dumps(extracted.model_dump(), ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            typer.echo(f"Error: --extract value must be a JSON schema: {e}", err=True)
            raise typer.Exit(1)

    # 9. Write output
    if output:
        output.write_text(text_output, encoding="utf-8")
    else:
        typer.echo(text_output, nl=False)


@app.command()
def batch(
    urls_file: Path = typer.Argument(..., help="File with one URL per line"),
    concurrency: int = typer.Option(5, "--concurrency", "-c", help="Parallel requests"),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format"),
    output: Path = typer.Option(Path("./icerun-output"), "--output", "-o", help="Output directory"),
    resume: bool = typer.Option(False, "--resume", help="Skip already-scraped URLs"),
    async_mode: bool = typer.Option(False, "--async", help="Return job ID immediately"),
    rate_limit: Optional[float] = typer.Option(None, "--rate-limit", help="Max requests/second per domain"),
    parser: str = typer.Option("trafilatura", "--parser", help="Parser backend"),
    naming: str = typer.Option("hash", "--naming", help="Output filename scheme: hash|domain-slug|index"),
    errors_file: Path = typer.Option(Path("errors.txt"), "--errors-file", help="Write failed URLs here"),
) -> None:
    """Batch scrape many URLs concurrently."""
    # --async mode requires ICER-13 job system
    if async_mode:
        typer.echo(
            "Error: async mode requires the job system (ICER-13), not yet implemented",
            err=True,
        )
        raise typer.Exit(1)

    # Validate urls_file exists
    if not urls_file.exists():
        typer.echo(f"Error: URLs file not found: {urls_file}", err=True)
        raise typer.Exit(1)

    # Read and parse URLs (skip blank lines and # comments)
    raw_lines = urls_file.read_text(encoding="utf-8").splitlines()
    urls = [line.strip() for line in raw_lines if line.strip() and not line.strip().startswith("#")]

    if not urls:
        typer.echo(f"Error: no URLs found in {urls_file}", err=True)
        raise typer.Exit(1)

    # Create output directory
    output.mkdir(parents=True, exist_ok=True)

    # Determine file extension for chosen format
    ext = _FORMAT_EXT.get(format, ".txt")

    # Build URL → filename mapping
    def _make_filename(url: str, idx: int, used_names: set[str]) -> str:
        if naming == "hash":
            return hashlib.sha256(url.encode()).hexdigest()[:16] + ext
        elif naming == "domain-slug":
            parsed = urlparse(url)
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed.netloc + parsed.path).strip("-")
            slug = slug[:80]
            candidate = slug + ext
            # Collision avoidance: append counter suffix
            counter = 1
            while candidate in used_names:
                candidate = f"{slug}-{counter}{ext}"
                counter += 1
            return candidate
        elif naming == "index":
            width = len(str(len(urls)))
            return str(idx).zfill(width) + ext
        else:
            # Fallback to hash if unknown scheme
            return hashlib.sha256(url.encode()).hexdigest()[:16] + ext

    used_names: set[str] = set()
    url_filename_pairs: list[tuple[str, str]] = []
    for i, url in enumerate(urls):
        fname = _make_filename(url, i, used_names)
        used_names.add(fname)
        url_filename_pairs.append((url, fname))

    # Set up rate limiter if requested
    from icerun.scraper import DomainRateLimiter
    import icerun.scraper as scraper_mod
    from icerun import parser as parser_mod

    rate_limiter = DomainRateLimiter(requests_per_second=rate_limit) if rate_limit else None

    # Run the async batch pipeline
    start_time = time.monotonic()

    async def _run_batch() -> tuple[int, int, int]:
        """Returns (succeeded, failed, skipped)."""
        from rich.progress import (
            Progress,
            SpinnerColumn,
            BarColumn,
            MofNCompleteColumn,
            TimeElapsedColumn,
        )
        from rich.console import Console

        sem = asyncio.Semaphore(concurrency)
        succeeded = 0
        failed = 0
        skipped = 0
        failed_urls: list[str] = []

        console = Console(stderr=True, highlight=False)
        use_progress = sys.stderr.isatty()

        async def _process_one(
            url: str, filename: str, task_id: object, progress: object
        ) -> str:
            """Fetch + parse + write one URL. Returns 'ok', 'skip', or 'fail'."""
            out_path = output / filename

            # --resume: skip if output already exists
            if resume and out_path.exists():
                if use_progress and progress is not None:
                    progress.advance(task_id)  # type: ignore[attr-defined]
                return "skip"

            async with sem:
                try:
                    fetch_result = await scraper_mod.fetch(
                        url,
                        rate_limiter=rate_limiter,
                    )
                    if fetch_result.error:
                        raise RuntimeError(fetch_result.error)

                    parse_result = parser_mod.parse(
                        fetch_result.content,
                        url,
                        parser=parser,
                        format=format,
                    )
                    text_output = _format_output(parse_result, url, format)
                    out_path.write_text(text_output, encoding="utf-8")
                    if use_progress and progress is not None:
                        progress.advance(task_id)  # type: ignore[attr-defined]
                    return "ok"
                except Exception as exc:
                    failed_urls.append(url)
                    if use_progress and progress is not None:
                        progress.advance(task_id)  # type: ignore[attr-defined]
                    return "fail"

        if use_progress:
            with Progress(
                SpinnerColumn(),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=Console(stderr=True),
            ) as progress:
                task_id = progress.add_task("Scraping...", total=len(url_filename_pairs))
                coros = [
                    _process_one(url, fname, task_id, progress)
                    for url, fname in url_filename_pairs
                ]
                results = await asyncio.gather(*coros)
        else:
            coros = [
                _process_one(url, fname, None, None)
                for url, fname in url_filename_pairs
            ]
            results = await asyncio.gather(*coros)

        for status in results:
            if status == "ok":
                succeeded += 1
            elif status == "fail":
                failed += 1
            elif status == "skip":
                skipped += 1

        # Write errors file if any failures
        if failed_urls:
            errors_file.write_text("\n".join(failed_urls) + "\n", encoding="utf-8")

        return succeeded, failed, skipped

    succeeded, failed, skipped = asyncio.run(_run_batch())

    elapsed = time.monotonic() - start_time
    typer.echo(
        f"{succeeded} succeeded, {failed} failed, {skipped} skipped in {elapsed:.1f}s",
        err=True,
    )

    # Exit 1 if all failed or no URLs processed successfully
    if succeeded == 0 and skipped == 0:
        raise typer.Exit(1)


@app.command()
def crawl(
    start_url: str = typer.Argument(..., help="Starting URL"),
    depth: int = typer.Option(3, "--depth", "-d", help="Max link depth"),
    limit: int = typer.Option(100, "--limit", "-l", help="Max URLs to scrape"),
    include: Optional[List[str]] = typer.Option(None, "--include", help="URL patterns to include (glob)"),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", help="URL patterns to exclude (glob)"),
    same_domain: bool = typer.Option(True, "--same-domain/--allow-external", help="Only follow same-domain links"),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format"),
    output: Path = typer.Option(Path("./icerun-output"), "--output", "-o", help="Output directory"),
    concurrency: int = typer.Option(3, "--concurrency", "-c", help="Parallel fetches"),
    delay: float = typer.Option(1.0, "--delay", help="Seconds between requests to same domain"),
    sitemap: bool = typer.Option(False, "--sitemap", help="Use sitemap.xml for seed URLs"),
) -> None:
    """Crawl a site by following internal links."""
    _not_implemented("crawl")


@app.command(name="map")
def map_cmd(
    url: str = typer.Argument(..., help="Site URL to map"),
    sitemap: bool = typer.Option(True, "--sitemap/--no-sitemap", help="Parse sitemap.xml"),
    crawl_fallback: bool = typer.Option(False, "--crawl", help="Fallback to link crawl if no sitemap"),
    depth: int = typer.Option(5, "--depth", help="Crawl depth for fallback"),
    limit: int = typer.Option(1000, "--limit", "-l", help="Max URLs"),
    filter_pattern: Optional[str] = typer.Option(None, "--filter", help="URL glob filter"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    format: str = typer.Option("lines", "--format", "-f", help="Output: lines|json|csv"),
) -> None:
    """Discover all URLs on a site without downloading content."""
    _not_implemented("map")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of results"),
    scrape: bool = typer.Option(False, "--scrape", help="Scrape each result URL"),
    format: str = typer.Option("json", "--format", "-f", help="Output: json|markdown|lines"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
) -> None:
    """Search the web and optionally scrape each result."""
    _not_implemented("search")


@job_app.command("status")
def job_status(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    """Show status of an async job."""
    _not_implemented("job status")


@job_app.command("watch")
def job_watch(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    """Stream results from a running job."""
    _not_implemented("job watch")


@job_app.command("list")
def job_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
) -> None:
    """List all async jobs."""
    _not_implemented("job list")


@job_app.command("cancel")
def job_cancel(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    """Cancel a running job."""
    _not_implemented("job cancel")


@job_app.command("clean")
def job_clean(
    older_than: int = typer.Option(7, "--older-than", help="Remove jobs older than N days"),
) -> None:
    """Remove completed jobs older than N days."""
    _not_implemented("job clean")


@config_app.command("show")
def config_show() -> None:
    """Show effective configuration (merged from all sources)."""
    from icerun.config import load_config
    from rich.console import Console
    from rich.table import Table

    config, sources = load_config()
    console = Console()
    table = Table(title="icerun configuration", show_header=True)
    table.add_column("Section", style="cyan")
    table.add_column("Key", style="green")
    table.add_column("Value")
    table.add_column("Source", style="dim")

    for section, keys in config.items():
        if isinstance(keys, dict):
            for key, val in keys.items():
                display = "***" if key == "api_key" and val else str(val)
                source = sources.get(section, {}).get(key, "default")
                table.add_row(section, key, display, source)

    console.print(table)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (section.key, e.g. proxy.api_key)"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a configuration value in the user config file."""
    from icerun.config import set_config_value
    from rich.console import Console
    console = Console()
    try:
        set_config_value(key, value)
        console.print(f"[green]Set[/green] {key} = {value!r}")
    except ValueError as e:
        Console(stderr=True).print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
