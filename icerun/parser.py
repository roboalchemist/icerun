"""Pluggable HTML-to-markdown/HTML parser backends."""
from dataclasses import dataclass, field
from typing import Optional


PARSERS = ["trafilatura", "readability", "html2text", "markdownify", "selectolax", "raw"]


@dataclass
class ParseResult:
    title: Optional[str]
    markdown: Optional[str]
    html: Optional[str]
    links: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def parse(
    html: bytes,
    url: str,
    parser: str = "trafilatura",
    format: str = "markdown",
) -> ParseResult:
    """Parse HTML to the desired output format using the specified backend."""
    raise NotImplementedError(f"parser.parse not yet implemented (parser={parser})")
