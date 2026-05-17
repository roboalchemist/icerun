"""Shared pytest fixtures for icerun tests."""
import pytest


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    """Redirect icerun user config dir to a temp path for test isolation.

    Prevents ~/.config/icerun/config.toml from leaking into tests that assert
    default config values (e.g. defaults.parser == 'trafilatura').
    """
    fake_config = tmp_path / "config.toml"
    monkeypatch.setattr("icerun.config.get_user_config_path", lambda: fake_config)
    return fake_config
