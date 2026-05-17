"""Basic CLI invocation smoke tests."""
import pytest
from typer.testing import CliRunner
from icerun.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "scrape" in output
    assert "batch" in output
    assert "crawl" in output


def test_scrape_stub_exits_1():
    result = runner.invoke(app, ["scrape", "https://example.com"])
    assert result.exit_code == 1


def test_batch_stub_exits_1():
    result = runner.invoke(app, ["batch", "/nonexistent-file.txt"])
    assert result.exit_code == 1


def test_map_stub_exits_1():
    result = runner.invoke(app, ["map", "https://example.com"])
    assert result.exit_code == 1


def test_search_stub_exits_1():
    result = runner.invoke(app, ["search", "test query"])
    assert result.exit_code == 1


def test_job_help():
    result = runner.invoke(app, ["job", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output


def test_config_help():
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "show" in result.output


def test_config_show_runs():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "trafilatura" in result.output  # default parser


def test_config_set_invalid_key():
    result = runner.invoke(app, ["config", "set", "badkey", "value"])
    assert result.exit_code == 1
