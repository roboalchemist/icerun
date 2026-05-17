"""Worker subprocess for async batch jobs.

Usage: python -m icerun.job_worker <job_id> [db_path]
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def _main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m icerun.job_worker <job_id> [db_path]", file=sys.stderr)
        sys.exit(1)

    job_id = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else None

    asyncio.run(run_job(job_id, db_path))


async def run_job(job_id: str, db_path: str | None) -> None:
    """Execute the batch job identified by job_id."""
    from icerun import jobs

    job = jobs.get_job(job_id, db_path=db_path)
    if job is None:
        print(f"[job_worker] Job {job_id} not found", file=sys.stderr)
        sys.exit(1)

    # Mark job as running
    jobs.update_job(
        job_id,
        db_path=db_path,
        status="running",
        pid=os.getpid(),
        started_at=jobs._now(),
    )

    try:
        params = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
        await _run_batch_job(job_id, params, db_path)
        jobs.update_job(
            job_id,
            db_path=db_path,
            status="completed",
            finished_at=jobs._now(),
        )
    except Exception as exc:
        jobs.update_job(
            job_id,
            db_path=db_path,
            status="failed",
            finished_at=jobs._now(),
            error=str(exc),
        )
        print(f"[job_worker] Job {job_id} failed: {exc}", file=sys.stderr)
        sys.exit(1)


async def _run_batch_job(job_id: str, params: dict, db_path: str | None) -> None:
    """Run the batch scrape logic for a job, writing per-URL results."""
    from icerun import jobs, scraper as scraper_mod, parser as parser_mod

    urls: list[str] = params.get("urls", [])
    output_dir = Path(params.get("output", "./icerun-output"))
    fmt = params.get("format", "markdown")
    parser = params.get("parser", "trafilatura")
    naming = params.get("naming", "hash")
    concurrency = int(params.get("concurrency", 5))
    rate_limit = params.get("rate_limit")
    resume = bool(params.get("resume", False))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Format → extension mapping
    _FORMAT_EXT: dict[str, str] = {
        "markdown": ".md",
        "html": ".html",
        "json": ".json",
        "links": ".txt",
    }
    ext = _FORMAT_EXT.get(fmt, ".txt")

    # Build filename mapping (same logic as cli.py batch)
    used_names: set[str] = set()

    def _make_filename(url: str, idx: int) -> str:
        if naming == "hash":
            return hashlib.sha256(url.encode()).hexdigest()[:16] + ext
        elif naming == "domain-slug":
            parsed = urlparse(url)
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed.netloc + parsed.path).strip("-")
            slug = slug[:80]
            candidate = slug + ext
            counter = 1
            while candidate in used_names:
                candidate = f"{slug}-{counter}{ext}"
                counter += 1
            return candidate
        elif naming == "index":
            width = len(str(len(urls)))
            return str(idx).zfill(width) + ext
        else:
            return hashlib.sha256(url.encode()).hexdigest()[:16] + ext

    url_filename_pairs: list[tuple[str, str]] = []
    for i, url in enumerate(urls):
        fname = _make_filename(url, i)
        used_names.add(fname)
        url_filename_pairs.append((url, fname))

    from icerun.scraper import DomainRateLimiter
    rate_limiter = DomainRateLimiter(requests_per_second=rate_limit) if rate_limit else None

    sem = asyncio.Semaphore(concurrency)
    cancel_check_interval = 10
    processed_count = 0

    async def _process_one(url: str, filename: str) -> None:
        nonlocal processed_count
        out_path = output_dir / filename

        if resume and out_path.exists():
            jobs.add_result(job_id, url, "skip", str(out_path), None, db_path=db_path)
            return

        async with sem:
            # Periodically check for cancellation
            nonlocal processed_count
            processed_count += 1
            if processed_count % cancel_check_interval == 0:
                current = jobs.get_job(job_id, db_path=db_path)
                if current and current.get("status") == "cancelled":
                    return

            try:
                fetch_result = await scraper_mod.fetch(url, rate_limiter=rate_limiter)
                if fetch_result.error:
                    raise RuntimeError(fetch_result.error)

                parse_result = parser_mod.parse(fetch_result.content, url, parser=parser, format=fmt)

                # Format output
                if fmt == "markdown":
                    text = parse_result.markdown or ""
                elif fmt == "html":
                    text = parse_result.html or parse_result.markdown or ""
                elif fmt == "json":
                    text = json.dumps(
                        {
                            "url": url,
                            "title": parse_result.title,
                            "markdown": parse_result.markdown,
                            "links": parse_result.links,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                elif fmt == "links":
                    text = "\n".join(parse_result.links)
                else:
                    text = parse_result.markdown or ""

                out_path.write_text(text, encoding="utf-8")
                jobs.add_result(job_id, url, "ok", str(out_path), None, db_path=db_path)
            except Exception as exc:
                jobs.add_result(job_id, url, "fail", None, str(exc), db_path=db_path)

    coros = [_process_one(url, fname) for url, fname in url_filename_pairs]
    await asyncio.gather(*coros)


if __name__ == "__main__":
    _main()
