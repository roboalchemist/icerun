import asyncio
import json
import sys
import typer
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path

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
) -> None:
    """Batch scrape many URLs concurrently."""
    _not_implemented("batch")


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
    ignore_robots: bool = typer.Option(False, "--ignore-robots", help="Skip robots.txt compliance"),
) -> None:
    """Crawl a site by following internal links."""
    import icerun.crawler as crawler
    from icerun import parser as parser_mod

    async def _run_crawl() -> None:
        output.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, str] = {}

        async for result in crawler.crawl(
            start_url,
            depth=depth,
            limit=limit,
            include=list(include or []),
            exclude=list(exclude or []),
            same_domain=same_domain,
            delay=delay,
            concurrency=concurrency,
            ignore_robots=ignore_robots,
        ):
            # Build a filesystem-safe slug from the URL
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(result.url)
            slug_path = (parsed.netloc + parsed.path).strip("/").replace("/", "_").replace(":", "_")
            if not slug_path:
                slug_path = "index"
            # Append format extension
            ext_map = {"markdown": "md", "html": "html", "json": "json"}
            ext = ext_map.get(format, "md")
            filename = f"{slug_path}.{ext}"
            # Avoid filename collisions
            counter = 0
            candidate = filename
            while (output / candidate).exists() and manifest.get(result.url) != candidate:
                counter += 1
                candidate = f"{slug_path}_{counter}.{ext}"
            filename = candidate

            # Parse and format content
            try:
                parse_result = parser_mod.parse(result.content, result.url, format=format)
                if format == "markdown":
                    text = parse_result.markdown or ""
                elif format == "html":
                    text = parse_result.html or parse_result.markdown or ""
                elif format == "json":
                    text = json.dumps({
                        "url": result.url,
                        "depth": result.depth,
                        "title": parse_result.title,
                        "markdown": parse_result.markdown,
                        "links": result.links,
                    }, ensure_ascii=False, indent=2)
                else:
                    text = parse_result.markdown or ""
            except Exception as e:
                typer.echo(f"Warning: parse error for {result.url}: {e}", err=True)
                text = result.content.decode("utf-8", errors="replace")

            (output / filename).write_text(text, encoding="utf-8")
            manifest[result.url] = filename

        # Write manifest
        manifest_path = output / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"Crawled {len(manifest)} pages -> {output}", err=True)

    asyncio.run(_run_crawl())


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
