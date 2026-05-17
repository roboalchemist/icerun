# icerun

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Firecrawl-compatible local web scraper — no API key, no rate limits, bring your own proxies.**

icerun is a CLI tool and Python library that implements the Firecrawl scraping interface locally. It supports six content parsers, proxy rotation, async job queuing, BFS site crawling, sitemap mapping, LLM-powered structured extraction, PDF text extraction, and a stealth browser mode via camoufox.

## Features

- **No external API required** — runs entirely on your machine
- **6 content parsers** — trafilatura, readability, html2text, markdownify, selectolax, raw
- **Proxy rotation** — pass a proxy URL or use a proxy API (e.g. Webshare); auto-rotates per request
- **Async job queue** — fire-and-forget batch jobs with SQLite-backed status tracking
- **BFS site crawler** — follow internal links up to configurable depth/limit
- **Sitemap mapping** — discover all URLs via sitemap.xml, with BFS fallback
- **LLM extraction** — structured JSON extraction using any `instructor`-compatible model
- **PDF support** — extract text from PDF URLs via pdfplumber
- **Stealth browser mode** — JavaScript rendering with camoufox (anti-fingerprinting)
- **Web search** — Serper API (primary) or DuckDuckGo (fallback), with optional auto-scrape of results

## Installation

```bash
# Minimal install
pip install icerun

# With uv
uv add icerun
```

### Optional extras

```bash
pip install "icerun[browser]"   # camoufox stealth browser
pip install "icerun[extract]"   # LLM extraction via instructor
pip install "icerun[pdf]"       # PDF text extraction via pdfplumber
pip install "icerun[search]"    # DuckDuckGo search fallback
pip install "icerun[all]"       # Everything
```

## Quick Start

```bash
# Scrape a single URL to markdown
icerun scrape https://example.com

# Use a specific parser
icerun scrape https://example.com --parser readability

# Save to file
icerun scrape https://example.com -o page.md

# Batch scrape from a URL list
icerun batch urls.txt -o ./output/ -c 10

# Crawl a site up to depth 3, max 200 pages
icerun crawl https://docs.example.com --depth 3 --limit 200

# Map all URLs on a site
icerun map https://example.com

# Web search
icerun search "rust async runtime comparison"

# Show current config
icerun config show
```

## Commands

### `icerun scrape`

Scrape a single URL and output content.

```
icerun scrape URL [OPTIONS]

Options:
  --format, -f    markdown|html|json|screenshot|links  [default: markdown]
  --parser        trafilatura|readability|html2text|markdownify|selectolax|raw
  --browser       Force camoufox browser mode (requires icerun[browser])
  --output, -o    Output file (default: stdout)
  --proxy         Proxy URL (e.g. http://user:pass@host:port)
  --timeout       Request timeout in seconds  [default: 30]
  --metadata      Include metadata header in output
  --header, -H    Custom request header (KEY:VALUE, repeatable)
  --extract       JSON schema string for structured LLM extraction
  --extract-schema  Path to JSON schema file for structured extraction
  --action        Browser actions: click:SELECTOR, scroll:bottom (repeatable)
  --wait          Seconds to wait after page load (browser mode)
```

**Examples:**

```bash
# Get raw HTML
icerun scrape https://example.com --format html

# Force browser rendering with camoufox
icerun scrape https://spa.example.com --browser

# Extract structured data via LLM
icerun scrape https://example.com/product \
  --extract '{"title": "string", "price": "number"}'

# Scrape through a proxy
icerun scrape https://example.com --proxy http://user:pass@proxy:8080

# Click a button then scrape
icerun scrape https://example.com --browser --action "click:#load-more"
```

### `icerun batch`

Batch scrape many URLs concurrently from a file (one URL per line, `#` for comments).

```
icerun batch URLS_FILE [OPTIONS]

Options:
  --concurrency, -c   Parallel requests  [default: 5]
  --format, -f        Output format  [default: markdown]
  --output, -o        Output directory  [default: ./icerun-output]
  --resume            Skip already-scraped URLs
  --async             Return job ID immediately (background processing)
  --rate-limit        Max requests/second per domain
  --parser            Parser backend  [default: trafilatura]
  --naming            Output filename scheme: hash|domain-slug|index  [default: hash]
  --errors-file       Write failed URLs here  [default: errors.txt]
  --proxy             Proxy URL
```

**Example:**

```bash
# Scrape 1000 URLs with 20 workers, resume on failure
icerun batch urls.txt -c 20 --resume -o ./scraped/

# Background async job
icerun batch urls.txt --async
# → Job ID: abc123
icerun job status abc123
```

### `icerun crawl`

Crawl a site by following internal links (BFS).

```
icerun crawl START_URL [OPTIONS]

Options:
  --depth, -d         Max link depth  [default: 3]
  --limit, -l         Max URLs to scrape  [default: 100]
  --include           URL glob pattern to include (repeatable)
  --exclude           URL glob pattern to exclude (repeatable)
  --same-domain       Only follow same-domain links  [default: true]
  --format, -f        Output format  [default: markdown]
  --output, -o        Output directory  [default: ./icerun-output]
  --concurrency, -c   Parallel fetches  [default: 3]
  --delay             Seconds between requests to same domain  [default: 1.0]
  --sitemap           Use sitemap.xml for seed URLs
  --ignore-robots     Skip robots.txt compliance
  --proxy             Proxy URL
```

**Example:**

```bash
icerun crawl https://docs.example.com \
  --depth 5 --limit 500 \
  --include "*/docs/*" \
  --exclude "*/api/*" \
  -o ./docs-output/
```

### `icerun map`

Discover all URLs on a site without downloading page content.

```
icerun map URL [OPTIONS]

Options:
  --sitemap / --no-sitemap  Parse sitemap.xml  [default: true]
  --crawl                   BFS fallback if no sitemap found
  --depth                   Crawl depth for fallback  [default: 5]
  --limit, -l               Max URLs  [default: 1000]
  --filter                  URL glob filter
  --output, -o              Output file (default: stdout)
  --format, -f              lines|json|csv  [default: lines]
  --proxy                   Proxy URL
```

**Example:**

```bash
# Get all URLs as newline-separated list
icerun map https://example.com

# JSON output with metadata
icerun map https://example.com --format json -o urls.json

# BFS fallback for sites without sitemap
icerun map https://example.com --crawl --depth 3
```

### `icerun search`

Search the web and optionally scrape each result.

```
icerun search QUERY [OPTIONS]

Options:
  --limit, -l   Number of results  [default: 10]
  --scrape      Scrape each result URL and include markdown
  --format, -f  json|markdown|lines  [default: json]
  --output, -o  Output file
```

Requires `SERPER_API_KEY` env var for Serper API; falls back to DuckDuckGo (`icerun[search]` required).

### `icerun job`

Manage async scraping jobs.

```bash
icerun job list                  # List all jobs
icerun job status JOB_ID         # Show job status and progress
icerun job watch JOB_ID          # Stream live updates
icerun job cancel JOB_ID         # Cancel a running job
icerun job clean --older-than 7  # Remove jobs older than N days
```

### `icerun config`

Manage configuration.

```bash
icerun config show                        # Show effective config with sources
icerun config set proxy.proxy_url http://user:pass@host:1080
icerun config set proxy.api_key YOUR_KEY
```

Config file location: `~/.config/icerun/config.toml`

## Configuration

icerun reads configuration from three sources (later sources override earlier):
1. Defaults
2. `~/.config/icerun/config.toml`
3. Environment variables

### Environment variables

| Variable | Description |
|----------|-------------|
| `ICER_PROXY` | Proxy URL (e.g. `http://user:pass@host:port`) |
| `ICER_PROXY_API_KEY` | API key for proxy rotation services |
| `ICER_PARSER` | Default parser (`trafilatura`, `readability`, etc.) |
| `ICER_FORMAT` | Default output format |
| `ICER_CONCURRENCY` | Default concurrency level |
| `ICER_BROWSER` | Set to `1` to default to browser mode |
| `ICER_LLM_MODEL` | LLM model for extraction (e.g. `claude-3-haiku-20240307`) |
| `ICER_LLM_PROVIDER` | LLM provider (`anthropic`, `openai`) |
| `SERPER_API_KEY` | Serper API key for web search |
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM extraction |

## Parsers

| Parser | Best for |
|--------|----------|
| `trafilatura` | Article text, news, blogs (default) |
| `readability` | Long-form content, reader mode |
| `html2text` | Preserves link structure |
| `markdownify` | Faithful HTML-to-Markdown conversion |
| `selectolax` | Fast lightweight extraction |
| `raw` | Raw HTML passthrough |

## License

MIT — see [LICENSE](LICENSE)
