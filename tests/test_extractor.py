"""Tests for icerun.extractor module."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _parse_schema tests
# ---------------------------------------------------------------------------

def test_parse_schema_shorthand_string_field():
    from icerun.extractor import _parse_schema

    fields = _parse_schema({"title": "string"})
    assert "title" in fields
    typ, default = fields["title"]
    # typ should be Optional[str]
    assert default is None
    # Unwrap Optional — for Optional[str] the __args__ contains str and NoneType
    import typing
    args = typing.get_args(typ)
    assert str in args


def test_parse_schema_shorthand_number_field():
    from icerun.extractor import _parse_schema

    fields = _parse_schema({"price": "number"})
    typ, default = fields["price"]
    assert default is None
    import typing
    args = typing.get_args(typ)
    assert float in args


def test_parse_schema_shorthand_array_description():
    """'array of strings' shorthand — first word 'array' → list."""
    from icerun.extractor import _parse_schema

    fields = _parse_schema({"reviews": "array of strings"})
    typ, default = fields["reviews"]
    assert default is None
    import typing
    args = typing.get_args(typ)
    assert list in args


def test_parse_schema_json_schema_formal():
    """Formal JSON Schema with properties dict."""
    from icerun.extractor import _parse_schema

    schema = {
        "type": "object",
        "properties": {
            "price": {"type": "number"},
            "name": {"type": "string"},
        },
    }
    fields = _parse_schema(schema)
    assert "price" in fields
    assert "name" in fields
    import typing
    price_type, _ = fields["price"]
    assert float in typing.get_args(price_type)
    name_type, _ = fields["name"]
    assert str in typing.get_args(name_type)


def test_parse_schema_json_schema_missing_type_defaults_string():
    """Properties without 'type' key default to str."""
    from icerun.extractor import _parse_schema

    schema = {"properties": {"weird": {}}}
    fields = _parse_schema(schema)
    typ, _ = fields["weird"]
    import typing
    assert str in typing.get_args(typ)


# ---------------------------------------------------------------------------
# run_extraction tests (mocked instructor)
# ---------------------------------------------------------------------------

def _make_mock_extracted(data: dict):
    """Create a mock object whose .model_dump() returns data."""
    m = MagicMock()
    m.model_dump.return_value = data
    return m


def _make_mock_usage(input_tokens: int = 100, output_tokens: int = 50):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return usage


def _build_mock_instructor_module(extracted_obj, raw_response):
    """Return a mock instructor module with from_provider and from_openai."""
    mock_instructor = MagicMock()

    mock_client = MagicMock()
    mock_client.create_with_completion.return_value = (extracted_obj, raw_response)

    mock_instructor.from_provider.return_value = mock_client
    mock_instructor.from_openai.return_value = mock_client

    return mock_instructor, mock_client


def test_run_extraction_shorthand_schema():
    """Shorthand schema {"title": "string"} produces a model with Optional[str] field."""
    from icerun.extractor import run_extraction

    extracted_obj = _make_mock_extracted({"title": "My Product"})
    raw = SimpleNamespace(usage=_make_mock_usage())
    mock_instr, _ = _build_mock_instructor_module(extracted_obj, raw)

    with patch.dict(sys.modules, {"instructor": mock_instr}):
        result = run_extraction(
            text="Buy My Product for $9.99",
            url="https://example.com",
            schema_dict={"title": "string"},
            llm_cfg={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )

    assert result["extracted"]["title"] == "My Product"


def test_run_extraction_json_schema():
    """Formal JSON Schema with price: number produces Optional[float] field."""
    from icerun.extractor import run_extraction

    extracted_obj = _make_mock_extracted({"price": 29.99})
    raw = SimpleNamespace(usage=_make_mock_usage(200, 80))
    mock_instr, _ = _build_mock_instructor_module(extracted_obj, raw)

    schema = {"properties": {"price": {"type": "number"}}}

    with patch.dict(sys.modules, {"instructor": mock_instr}):
        result = run_extraction(
            text="Price: $29.99",
            url="https://shop.example.com",
            schema_dict=schema,
            llm_cfg={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )

    assert result["extracted"]["price"] == pytest.approx(29.99)


def test_run_extraction_returns_envelope():
    """Output envelope has url, extracted, model, tokens_used."""
    from icerun.extractor import run_extraction

    extracted_obj = _make_mock_extracted({"title": "Widget"})
    raw = SimpleNamespace(usage=_make_mock_usage(50, 30))
    mock_instr, _ = _build_mock_instructor_module(extracted_obj, raw)

    with patch.dict(sys.modules, {"instructor": mock_instr}):
        result = run_extraction(
            text="Widget on sale",
            url="https://example.com/widget",
            schema_dict={"title": "string"},
            llm_cfg={"provider": "anthropic", "model": "claude-haiku-4-5"},
        )

    assert result["url"] == "https://example.com/widget"
    assert "extracted" in result
    assert result["model"] == "claude-haiku-4-5"
    assert "tokens_used" in result
    assert result["tokens_used"] == 80  # 50 + 30


def test_run_extraction_missing_instructor():
    """ImportError with helpful message when instructor not installed."""
    # Temporarily remove instructor from sys.modules and block import
    original = sys.modules.pop("instructor", None)
    try:
        with patch.dict(sys.modules, {"instructor": None}):
            with pytest.raises(ImportError, match="instructor not installed"):
                from icerun import extractor as ext_mod
                # Force re-execution of the import guard
                import importlib
                importlib.reload(ext_mod)
                ext_mod.run_extraction("text", "http://x.com", {"a": "string"}, {})
    finally:
        if original is not None:
            sys.modules["instructor"] = original


def test_run_extraction_missing_instructor_via_none():
    """When sys.modules["instructor"] = None, run_extraction raises ImportError."""
    # patch.dict with None makes 'import instructor' raise ImportError
    with patch.dict(sys.modules, {"instructor": None}):
        with pytest.raises(ImportError, match="instructor not installed"):
            # We need a fresh import of the function body path
            # Use a subprocess-style approach: call the function with instructor blocked
            from icerun.extractor import run_extraction as _re
            _re("text", "http://x.com", {"a": "string"}, {})


def test_run_extraction_openai_provider():
    """When provider=openai, from_openai is called (not from_provider)."""
    from icerun.extractor import run_extraction

    extracted_obj = _make_mock_extracted({"name": "Gadget"})
    # OpenAI usage has total_tokens
    raw = SimpleNamespace(usage=SimpleNamespace(total_tokens=120))
    mock_instr, mock_client = _build_mock_instructor_module(extracted_obj, raw)

    # Also mock openai module
    mock_openai = MagicMock()
    mock_openai_instance = MagicMock()
    mock_openai.OpenAI.return_value = mock_openai_instance

    with patch.dict(sys.modules, {"instructor": mock_instr, "openai": mock_openai}):
        result = run_extraction(
            text="Gadget review",
            url="https://review.example.com",
            schema_dict={"name": "string"},
            llm_cfg={"provider": "openai", "model": "gpt-4o", "api_key": "sk-test"},
        )

    mock_instr.from_openai.assert_called_once()
    mock_instr.from_provider.assert_not_called()
    assert result["extracted"]["name"] == "Gadget"
    assert result["tokens_used"] == 120


# ---------------------------------------------------------------------------
# Config env var tests
# ---------------------------------------------------------------------------

def test_config_icer_llm_model_env(monkeypatch):
    """ICER_LLM_MODEL env var sets config['llm']['model']."""
    from pathlib import Path
    monkeypatch.setenv("ICER_LLM_MODEL", "claude-haiku-4-5")
    from icerun.config import load_config

    config, sources = load_config(cwd=Path("/nonexistent"))
    assert config["llm"]["model"] == "claude-haiku-4-5"
    assert sources["llm"]["model"] == "env:ICER_LLM_MODEL"


def test_config_icer_llm_provider_env(monkeypatch):
    """ICER_LLM_PROVIDER env var sets config['llm']['provider']."""
    from pathlib import Path
    monkeypatch.setenv("ICER_LLM_PROVIDER", "openai")
    from icerun.config import load_config

    config, sources = load_config(cwd=Path("/nonexistent"))
    assert config["llm"]["provider"] == "openai"
    assert sources["llm"]["provider"] == "env:ICER_LLM_PROVIDER"


# ---------------------------------------------------------------------------
# CLI --extract-schema test
# ---------------------------------------------------------------------------

def test_scrape_extract_schema_flag(tmp_path):
    """--extract-schema flag is accepted and calls run_extraction."""
    import json
    from unittest.mock import AsyncMock, patch

    from typer.testing import CliRunner
    from icerun.cli import app
    from icerun.scraper import FetchResult

    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps({"title": "string", "price": "number"}))

    mock_result = FetchResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        content_type="text/html",
        content=b"<html><body><h1>Widget</h1><p>Price: $9.99</p></body></html>",
        headers={},
        error=None,
        screenshot_bytes=None,
    )

    extracted_obj = _make_mock_extracted({"title": "Widget", "price": 9.99})
    raw = SimpleNamespace(usage=_make_mock_usage(100, 50))
    mock_instr, _ = _build_mock_instructor_module(extracted_obj, raw)

    runner = CliRunner()
    with patch("icerun.scraper.fetch", new=AsyncMock(return_value=mock_result)):
        with patch.dict(sys.modules, {"instructor": mock_instr}):
            result = runner.invoke(
                app,
                ["scrape", "https://example.com", "--extract-schema", str(schema_file)],
            )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["url"] == "https://example.com"
    assert "extracted" in data
    assert "tokens_used" in data
