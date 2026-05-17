"""PDF text + table extraction via pdfplumber (optional dep)."""
from __future__ import annotations

import io
from typing import Optional

from icerun.parser import ParseResult


def _require_pdfplumber():
    """Import pdfplumber or raise a helpful ImportError."""
    try:
        import pdfplumber
        return pdfplumber
    except ImportError:
        raise ImportError(
            "pdfplumber is not installed. Install with: uv sync --extra pdf"
        )


def _table_to_markdown(table: list[list[Optional[str]]]) -> str:
    """Convert a pdfplumber table (list-of-rows) to a GFM markdown table.

    The first row is used as a header if all its cells are non-None;
    otherwise synthesise 'Col 1', 'Col 2', ... headers and treat every
    row as a data row.
    """
    if not table:
        return ""

    # Normalise None cells to empty strings
    def clean_row(row: list[Optional[str]]) -> list[str]:
        return [cell if cell is not None else "" for cell in row]

    cleaned = [clean_row(row) for row in table]

    if not cleaned:
        return ""

    # Determine column count from the widest row
    ncols = max(len(row) for row in cleaned)

    # Pad all rows to the same width
    def pad(row: list[str]) -> list[str]:
        return row + [""] * (ncols - len(row))

    cleaned = [pad(row) for row in cleaned]

    # Decide whether to promote first row to header
    first_raw = table[0]
    use_first_as_header = all(cell is not None for cell in first_raw)

    if use_first_as_header:
        header = cleaned[0]
        data_rows = cleaned[1:]
    else:
        header = [f"Col {i + 1}" for i in range(ncols)]
        data_rows = cleaned

    # Escape pipe characters inside cells
    def esc(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", " ").strip()

    sep = ["-" * max(len(esc(h)), 3) for h in header]

    lines = []
    lines.append("| " + " | ".join(esc(h) for h in header) + " |")
    lines.append("| " + " | ".join(sep) + " |")
    for row in data_rows:
        lines.append("| " + " | ".join(esc(cell) for cell in row) + " |")

    return "\n".join(lines)


def parse_pdf(content: bytes, url: str) -> ParseResult:
    """Extract text and tables from PDF bytes using pdfplumber.

    Returns a ParseResult whose ``markdown`` field contains all extracted
    text and tables concatenated in page order.  Metadata keys are
    normalised to lowercase (PDF spec uses PascalCase).

    Raises ImportError if pdfplumber is not installed.
    """
    pdfplumber = _require_pdfplumber()

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)

            # Normalise PDF metadata keys to lowercase
            raw_meta = pdf.metadata or {}
            metadata: dict = {"pages": page_count}
            for key, value in raw_meta.items():
                if value is not None:
                    metadata[key.lower()] = value

            parts: list[str] = []

            for page in pdf.pages:
                # --- plain text ---
                text = page.extract_text()
                if text:
                    parts.append(text.strip())

                # --- tables ---
                tables = page.extract_tables()
                for table in tables:
                    md_table = _table_to_markdown(table)
                    if md_table:
                        parts.append(md_table)

            markdown = "\n\n".join(parts)

            # Derive title from metadata if available
            title: Optional[str] = metadata.get("title") or None

            return ParseResult(
                title=title,
                markdown=markdown,
                html=None,
                metadata=metadata,
            )

    except Exception as exc:
        return ParseResult(
            title=None,
            markdown="",
            html=None,
            metadata={"error": str(exc)},
        )
