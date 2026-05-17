"""Tests for PDF parsing (icerun/pdf.py) and magic-byte routing in parser.py."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from icerun.parser import parse, ParseResult


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake pdfplumber page / pdf object
# ---------------------------------------------------------------------------

def _make_fake_page(text: str = "", tables: list | None = None):
    page = MagicMock()
    page.page_number = 1
    page.extract_text.return_value = text
    page.extract_tables.return_value = tables if tables is not None else []
    return page


def _make_fake_pdf(pages: list, metadata: dict | None = None):
    """Return a context-manager-compatible fake pdf object."""
    pdf = MagicMock()
    pdf.pages = pages
    pdf.metadata = metadata if metadata is not None else {}
    # Support `with pdfplumber.open(...) as pdf:`
    pdf.__enter__ = MagicMock(return_value=pdf)
    pdf.__exit__ = MagicMock(return_value=False)
    return pdf


# ---------------------------------------------------------------------------
# 1. Magic-byte routing
# ---------------------------------------------------------------------------

def test_parse_pdf_magic_bytes_routes_to_pdf_parser():
    """parse() with %PDF magic bytes must delegate to parse_pdf, not HTML parsers."""
    fake_page = _make_fake_page(text="Hello from PDF")
    fake_pdf = _make_fake_pdf(pages=[fake_page])

    # Patch pdfplumber.open so we don't need the real library installed
    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse(b'%PDF-1.4 fake content', "https://example.com/doc.pdf")

    assert isinstance(result, ParseResult)
    # If PDF routing worked, markdown should contain our fake text
    assert "Hello from PDF" in (result.markdown or "")


# ---------------------------------------------------------------------------
# 2. Text extraction
# ---------------------------------------------------------------------------

def test_parse_pdf_text_extraction():
    """parse_pdf extracts page text and includes it in markdown."""
    from icerun.pdf import parse_pdf

    fake_page = _make_fake_page(text="This is page one text.")
    fake_pdf = _make_fake_pdf(pages=[fake_page], metadata={})

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    assert "This is page one text." in result.markdown


# ---------------------------------------------------------------------------
# 3. Table extraction → GFM markdown table
# ---------------------------------------------------------------------------

def test_parse_pdf_table_extraction():
    """parse_pdf converts extracted tables to GFM markdown tables."""
    from icerun.pdf import parse_pdf

    table = [
        ["Name", "Age", "City"],
        ["Alice", "30", "NYC"],
        ["Bob", "25", "LA"],
    ]
    fake_page = _make_fake_page(text="", tables=[table])
    fake_pdf = _make_fake_pdf(pages=[fake_page], metadata={})

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    md = result.markdown
    # GFM table must have header, separator, and data rows
    assert "| Name" in md
    assert "| Age" in md
    assert "Alice" in md
    assert "Bob" in md
    # Separator row should be present
    assert "|---" in md or "| ---" in md


def test_parse_pdf_table_none_cells():
    """Tables with None cells are converted to empty strings, not 'None'."""
    from icerun.pdf import parse_pdf

    table = [
        [None, "Header2"],
        ["val1", None],
    ]
    fake_page = _make_fake_page(text="", tables=[table])
    fake_pdf = _make_fake_pdf(pages=[fake_page], metadata={})

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    assert "None" not in result.markdown


def test_parse_pdf_table_synthesised_header():
    """When first row contains None cells, synthesise Col 1, Col 2 headers."""
    from icerun.pdf import _table_to_markdown

    table = [
        [None, "second"],   # first row has None → synthesise headers
        ["a", "b"],
    ]
    md = _table_to_markdown(table)
    assert "Col 1" in md
    assert "Col 2" in md


# ---------------------------------------------------------------------------
# 4. Metadata normalisation
# ---------------------------------------------------------------------------

def test_parse_pdf_metadata():
    """PDF metadata keys (PascalCase) are normalised to lowercase."""
    from icerun.pdf import parse_pdf

    fake_page = _make_fake_page(text="Content")
    fake_pdf = _make_fake_pdf(
        pages=[fake_page],
        metadata={"Title": "My Report", "Author": "Jane Doe", "CreationDate": "D:20240101"},
    )

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    assert result.metadata.get("title") == "My Report"
    assert result.metadata.get("author") == "Jane Doe"
    assert result.metadata.get("creationdate") == "D:20240101"
    # 'pages' count should be included
    assert result.metadata.get("pages") == 1
    # Original PascalCase keys must NOT appear
    assert "Title" not in result.metadata
    assert "Author" not in result.metadata


def test_parse_pdf_metadata_title_propagated():
    """ParseResult.title is taken from the PDF's Title metadata key."""
    from icerun.pdf import parse_pdf

    fake_page = _make_fake_page(text="Body text")
    fake_pdf = _make_fake_pdf(
        pages=[fake_page],
        metadata={"Title": "Important Doc"},
    )

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    assert result.title == "Important Doc"


# ---------------------------------------------------------------------------
# 5. pdfplumber not installed → helpful ImportError
# ---------------------------------------------------------------------------

def test_parse_pdf_no_pdfplumber():
    """parse_pdf raises ImportError with install hint when pdfplumber is absent."""
    # Remove pdfplumber from sys.modules to simulate it not being installed
    with patch.dict(sys.modules, {"pdfplumber": None}):
        # Re-import pdf module so it sees the patched sys.modules
        import importlib
        import icerun.pdf as pdf_mod
        importlib.reload(pdf_mod)

        with pytest.raises(ImportError, match="pdfplumber"):
            pdf_mod.parse_pdf(b"%PDF-1.4 fake", "https://example.com/sample.pdf")

    # Reload to restore normal state for other tests
    import importlib
    import icerun.pdf as pdf_mod
    importlib.reload(pdf_mod)


# ---------------------------------------------------------------------------
# 6. Corrupted PDF → error in metadata, no exception raised
# ---------------------------------------------------------------------------

def test_parse_pdf_corrupted():
    """parse_pdf catches exceptions from corrupted PDFs and returns error metadata."""
    from icerun.pdf import parse_pdf

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.side_effect = Exception("corrupted PDF stream")

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 invalid data here", "https://example.com/bad.pdf")

    assert isinstance(result, ParseResult)
    assert result.markdown == ""
    assert "error" in result.metadata
    assert "corrupted" in result.metadata["error"]


# ---------------------------------------------------------------------------
# 7. Multi-page PDF: all pages concatenated
# ---------------------------------------------------------------------------

def test_parse_pdf_multipage():
    """Text from all pages appears in the markdown output."""
    from icerun.pdf import parse_pdf

    pages = [
        _make_fake_page(text="Page one content"),
        _make_fake_page(text="Page two content"),
        _make_fake_page(text="Page three content"),
    ]
    fake_pdf = _make_fake_pdf(pages=pages, metadata={})
    for i, p in enumerate(pages, 1):
        p.page_number = i

    fake_pdfplumber = MagicMock()
    fake_pdfplumber.open.return_value = fake_pdf

    with patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}):
        result = parse_pdf(b"%PDF-1.4 fake", "https://example.com/multi.pdf")

    assert "Page one content" in result.markdown
    assert "Page two content" in result.markdown
    assert "Page three content" in result.markdown
    assert result.metadata["pages"] == 3


# ---------------------------------------------------------------------------
# 8. _table_to_markdown unit tests
# ---------------------------------------------------------------------------

def test_table_to_markdown_basic():
    from icerun.pdf import _table_to_markdown

    table = [["A", "B"], ["1", "2"]]
    md = _table_to_markdown(table)
    assert "| A" in md
    assert "| B" in md
    assert "| 1" in md
    assert "| 2" in md


def test_table_to_markdown_empty():
    from icerun.pdf import _table_to_markdown

    assert _table_to_markdown([]) == ""


def test_table_to_markdown_pipe_escape():
    from icerun.pdf import _table_to_markdown

    table = [["Col|A", "Col B"], ["val|1", "val 2"]]
    md = _table_to_markdown(table)
    # Pipe inside cells should be escaped
    assert "Col\\|A" in md
    assert "val\\|1" in md
