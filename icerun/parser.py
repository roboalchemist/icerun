"""Pluggable HTML-to-markdown/HTML parser backends."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin


PARSERS = ["trafilatura", "readability", "html2text", "markdownify", "selectolax", "raw"]


@dataclass
class ParseResult:
    title: Optional[str]
    markdown: Optional[str]
    html: Optional[str]
    links: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _decode(html: bytes) -> str:
    """Decode bytes to str, sniffing charset from meta tag first."""
    m = re.search(rb'charset=["\']?([\w-]+)', html, re.IGNORECASE)
    if m:
        enc = m.group(1).decode("ascii")
        try:
            return html.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return html.decode("utf-8", errors="replace")


def _extract_title(html: bytes) -> Optional[str]:
    """Extract <title> text using selectolax."""
    from selectolax.parser import HTMLParser
    node = HTMLParser(html).css_first("title")
    return node.text().strip() if node else None


def _extract_links(html: bytes, base_url: str = "") -> list[str]:
    """Extract all non-fragment, non-mailto href values from <a> tags."""
    from selectolax.parser import HTMLParser
    links = []
    for a in HTMLParser(html).css("a"):
        href = a.attributes.get("href", "")
        if href and not href.startswith(("#", "mailto:", "javascript:")):
            links.append(urljoin(base_url, href) if base_url else href)
    return links


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

def _parse_trafilatura(html: bytes, url: str) -> ParseResult:
    import trafilatura
    html_str = _decode(html)
    markdown = trafilatura.extract(
        html_str,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_formatting=True,
        include_links=True,
    )
    meta = trafilatura.extract_metadata(html_str, default_url=url)
    title = meta.title if meta else _extract_title(html)
    metadata = meta.as_dict() if meta else {}
    return ParseResult(
        title=title,
        markdown=markdown or "",
        html=None,
        links=_extract_links(html, url),
        metadata=metadata,
    )


def _parse_readability(html: bytes, url: str) -> ParseResult:
    import html2text as h2t
    import readability
    html_str = _decode(html)
    doc = readability.Document(html_str, url=url)
    title = doc.title()
    summary_html = doc.summary()
    converter = h2t.HTML2Text()
    converter.body_width = 0
    markdown = converter.handle(summary_html)
    return ParseResult(
        title=title,
        markdown=markdown.strip(),
        html=summary_html,
        links=_extract_links(html, url),
        metadata={"title": title},
    )


def _parse_html2text(html: bytes, url: str) -> ParseResult:
    import html2text as h2t
    html_str = _decode(html)
    converter = h2t.HTML2Text()
    converter.body_width = 0
    converter.baseurl = url
    markdown = converter.handle(html_str)
    return ParseResult(
        title=_extract_title(html),
        markdown=markdown.strip(),
        html=None,
        links=_extract_links(html, url),
        metadata={},
    )


def _parse_markdownify(html: bytes, url: str) -> ParseResult:
    import markdownify
    from selectolax.parser import HTMLParser
    body_node = HTMLParser(html).body
    body_html = body_node.html if body_node else _decode(html)
    markdown = markdownify.markdownify(body_html, heading_style=markdownify.ATX)
    return ParseResult(
        title=_extract_title(html),
        markdown=markdown.strip(),
        html=body_html,
        links=_extract_links(html, url),
        metadata={},
    )


def _parse_selectolax(html: bytes, url: str) -> ParseResult:
    from selectolax.parser import HTMLParser
    p = HTMLParser(html)
    body = p.body
    body_html = body.html if body else html.decode("utf-8", errors="replace")
    return ParseResult(
        title=_extract_title(html),
        markdown=None,
        html=body_html,
        links=_extract_links(html, url),
        metadata={"encoding": p.input_encoding},
    )


def _parse_raw(html: bytes, url: str) -> ParseResult:
    return ParseResult(
        title=_extract_title(html),
        markdown=None,
        html=html.decode("utf-8", errors="replace"),
        links=_extract_links(html, url),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_PARSERS = {
    "trafilatura": _parse_trafilatura,
    "readability": _parse_readability,
    "html2text": _parse_html2text,
    "markdownify": _parse_markdownify,
    "selectolax": _parse_selectolax,
    "raw": _parse_raw,
}


def parse(
    html: bytes,
    url: str,
    parser: str = "trafilatura",
    format: str = "markdown",
) -> ParseResult:
    """Parse HTML bytes using the specified backend and return a ParseResult.

    If the content begins with the PDF magic bytes ``%PDF``, it is
    dispatched to the PDF parser transparently regardless of the
    ``parser`` argument.

    Args:
        html:   Raw HTML bytes from the fetched page.
        url:    Source URL — used for resolving relative links and metadata.
        parser: One of PARSERS (default: 'trafilatura').
        format: Hint for output preference; each backend returns what it can.

    Returns:
        ParseResult with title, markdown, html, links, and metadata fields.

    Raises:
        ValueError: If an unknown parser name is given.
    """
    # PDF magic-byte detection — more robust than checking content[:4] exactly
    if b'%PDF' in html[:16]:
        from icerun.pdf import parse_pdf
        return parse_pdf(html, url)

    if parser not in _PARSERS:
        raise ValueError(f"Unknown parser '{parser}'. Choose from: {', '.join(PARSERS)}")
    return _PARSERS[parser](html, url)


parse_html = parse
