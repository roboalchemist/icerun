"""Tests for parser module."""
import pytest
from icerun.parser import parse, ParseResult, PARSERS, _decode, _extract_title, _extract_links

SAMPLE_HTML = b"""<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<h1>Hello World</h1>
<p>This is a test page with some content about Python programming.</p>
<a href="/page1">Link 1</a>
<a href="https://example.com">External</a>
</body>
</html>"""


def test_all_parsers_listed():
    assert len(PARSERS) == 6
    assert "trafilatura" in PARSERS
    assert "raw" in PARSERS


def test_decode_utf8():
    assert _decode(b"hello") == "hello"


def test_extract_title():
    assert _extract_title(SAMPLE_HTML) == "Test Page"


def test_extract_links():
    links = _extract_links(SAMPLE_HTML, "https://example.com")
    assert any("page1" in l for l in links)
    assert "https://example.com" in links


@pytest.mark.parametrize("parser", ["trafilatura", "readability", "html2text", "markdownify", "selectolax", "raw"])
def test_parse_returns_result(parser):
    result = parse(SAMPLE_HTML, "https://example.com", parser=parser)
    assert isinstance(result, ParseResult)
    assert result.title is not None or result.html is not None or result.markdown is not None


def test_trafilatura_returns_markdown():
    result = parse(SAMPLE_HTML, "https://example.com", parser="trafilatura")
    # trafilatura may return None/empty for short pages — just check type
    assert result.markdown is not None


def test_raw_returns_html():
    result = parse(SAMPLE_HTML, "https://example.com", parser="raw")
    assert result.html is not None
    assert "Hello World" in result.html


def test_unknown_parser_raises():
    with pytest.raises(ValueError):
        parse(SAMPLE_HTML, "https://example.com", parser="nonexistent")


def test_links_extracted():
    result = parse(SAMPLE_HTML, "https://example.com", parser="raw")
    assert len(result.links) >= 1


def test_readability_returns_markdown_and_html():
    result = parse(SAMPLE_HTML, "https://example.com", parser="readability")
    assert result.markdown is not None
    assert result.html is not None


def test_html2text_returns_markdown():
    result = parse(SAMPLE_HTML, "https://example.com", parser="html2text")
    assert result.markdown is not None
    assert len(result.markdown) > 0


def test_markdownify_returns_markdown_and_html():
    result = parse(SAMPLE_HTML, "https://example.com", parser="markdownify")
    assert result.markdown is not None
    assert result.html is not None


def test_selectolax_returns_html():
    result = parse(SAMPLE_HTML, "https://example.com", parser="selectolax")
    assert result.html is not None
    assert "Hello World" in result.html


def test_title_extracted_for_all_parsers():
    for parser in PARSERS:
        result = parse(SAMPLE_HTML, "https://example.com", parser=parser)
        # Every parser should extract the title
        assert result.title is not None, f"Parser '{parser}' did not extract title"
        assert "Test Page" in result.title or result.title != "", f"Parser '{parser}' got unexpected title: {result.title!r}"


def test_links_have_absolute_urls():
    result = parse(SAMPLE_HTML, "https://example.com", parser="raw")
    # /page1 should be resolved to https://example.com/page1
    assert any("example.com/page1" in l for l in result.links)


def test_decode_charset_sniffing():
    # HTML with charset declaration
    latin1_html = b'<html><head><meta charset="latin-1"></head><body>hello</body></html>'
    decoded = _decode(latin1_html)
    assert "hello" in decoded


def test_parse_result_has_links_list():
    result = parse(SAMPLE_HTML, "https://example.com", parser="raw")
    assert isinstance(result.links, list)
    assert isinstance(result.metadata, dict)
