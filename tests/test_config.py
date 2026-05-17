"""Tests for config module."""
import os
import pytest
from pathlib import Path
from icerun.config import (
    load_config, set_config_value, DEFAULTS, _deep_merge,
    _dict_to_toml, _load_toml_safe, get_user_config_path, get_project_config_path
)


def test_defaults_loaded():
    config, sources = load_config(cwd=Path("/nonexistent"))
    assert config["defaults"]["parser"] == "trafilatura"
    assert config["defaults"]["format"] == "markdown"
    assert config["browser"]["headless"] is True


def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"y": 99, "z": 100}}
    result = _deep_merge(base, override)
    assert result["a"]["x"] == 1
    assert result["a"]["y"] == 99
    assert result["a"]["z"] == 100
    assert result["b"] == 3


def test_dict_to_toml_roundtrip():
    data = {"proxy": {"api_key": "abc", "proxy_url": ""}, "defaults": {"concurrency": 5}}
    toml_str = _dict_to_toml(data)
    import tomllib
    parsed = tomllib.loads(toml_str)
    assert parsed["proxy"]["api_key"] == "abc"
    assert parsed["defaults"]["concurrency"] == 5


def test_env_var_overrides(monkeypatch):
    monkeypatch.setenv("ICER_PARSER", "html2text")
    monkeypatch.setenv("ICER_CONCURRENCY", "10")
    config, sources = load_config(cwd=Path("/nonexistent"))
    assert config["defaults"]["parser"] == "html2text"
    assert config["defaults"]["concurrency"] == 10
    assert sources["defaults"]["parser"] == "env:ICER_PARSER"


def test_icer_browser_inverts_headless(monkeypatch):
    monkeypatch.setenv("ICER_BROWSER", "1")
    config, _ = load_config(cwd=Path("/nonexistent"))
    assert config["browser"]["headless"] is False


def test_project_config_overrides_user(tmp_path):
    project_config = tmp_path / ".icerun.toml"
    project_config.write_text('[defaults]\nparser = "markdownify"\n')
    config, sources = load_config(cwd=tmp_path)
    assert config["defaults"]["parser"] == "markdownify"
    assert "project" in sources["defaults"]["parser"]


def test_set_config_value(tmp_path, monkeypatch):
    monkeypatch.setattr("icerun.config.get_user_config_path", lambda: tmp_path / "config.toml")
    set_config_value("proxy.api_key", "my-key")
    import tomllib
    with open(tmp_path / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["proxy"]["api_key"] == "my-key"


def test_set_config_invalid_section(tmp_path, monkeypatch):
    monkeypatch.setattr("icerun.config.get_user_config_path", lambda: tmp_path / "config.toml")
    with pytest.raises(ValueError, match="Unknown section"):
        set_config_value("nonexistent.key", "value")


def test_set_config_invalid_key(tmp_path, monkeypatch):
    monkeypatch.setattr("icerun.config.get_user_config_path", lambda: tmp_path / "config.toml")
    with pytest.raises(ValueError, match="Unknown key"):
        set_config_value("proxy.nonexistent", "value")
