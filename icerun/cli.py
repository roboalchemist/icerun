import typer
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
) -> None:
    """Scrape a single URL and output content."""
    _not_implemented("scrape")


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
