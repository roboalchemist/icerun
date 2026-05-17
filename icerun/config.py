"""Layered configuration system for icerun."""
from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "defaults": {
        "parser": "trafilatura",
        "format": "markdown",
        "concurrency": 5,
        "timeout": 30,
        "browser": False,
    },
    "proxy": {
        "api_key": "",
        "proxy_url": "",
        "proxy_file": "",
    },
    "llm": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "api_key": "",
    },
    "browser": {
        "wait_timeout": 30,
        "headless": True,
    },
    "search": {
        "api_key": "",
        "provider": "auto",  # auto | serper | ddg
    },
}

VALID_KEYS: dict[str, set[str]] = {
    section: set(keys.keys()) for section, keys in DEFAULTS.items()
}

_PARSER_VALUES = ["trafilatura", "readability", "html2text", "markdownify", "selectolax", "raw"]
_FORMAT_VALUES = ["markdown", "html", "json", "screenshot", "links"]


def get_user_config_path() -> Path:
    return Path.home() / ".config" / "icerun" / "config.toml"


def get_project_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".icerun.toml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


def _load_toml_safe(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"Config parse error in {path}: {e}")


def _apply_env_vars(config: dict, sources: dict) -> tuple[dict, dict]:
    env_map = [
        ("ICER_PARSER", "defaults", "parser"),
        ("ICER_FORMAT", "defaults", "format"),
        ("ICER_CONCURRENCY", "defaults", "concurrency"),
        ("ICER_PROXY", "proxy", "proxy_url"),
        ("ICER_PROXY_API_KEY", "proxy", "api_key"),
    ]
    for env_var, section, key in env_map:
        val = os.environ.get(env_var, "").strip()
        if val:
            if key == "concurrency":
                try:
                    val = int(val)  # type: ignore[assignment]
                except ValueError:
                    pass
            config[section][key] = val
            sources.setdefault(section, {})[key] = f"env:{env_var}"

    # ICER_BROWSER=1 → headless=False
    if os.environ.get("ICER_BROWSER", "").strip() == "1":
        config["browser"]["headless"] = False
        sources.setdefault("browser", {})["headless"] = "env:ICER_BROWSER"

    # ICER_LLM_MODEL and ICER_LLM_PROVIDER env vars
    if os.environ.get("ICER_LLM_MODEL"):
        config.setdefault("llm", {})["model"] = os.environ["ICER_LLM_MODEL"]
        sources.setdefault("llm", {})["model"] = "env:ICER_LLM_MODEL"
    if os.environ.get("ICER_LLM_PROVIDER"):
        config.setdefault("llm", {})["provider"] = os.environ["ICER_LLM_PROVIDER"]
        sources.setdefault("llm", {})["provider"] = "env:ICER_LLM_PROVIDER"

    # SERPER_API_KEY env var for search
    if os.environ.get("SERPER_API_KEY"):
        config.setdefault("search", {})["api_key"] = os.environ["SERPER_API_KEY"]
        sources.setdefault("search", {})["api_key"] = "env:SERPER_API_KEY"

    # LLM API keys from standard env vars
    provider = config["llm"].get("provider", "anthropic")
    if not config["llm"].get("api_key"):
        if provider == "anthropic":
            ak = os.environ.get("ANTHROPIC_API_KEY", "")
        else:
            ak = os.environ.get("OPENAI_API_KEY", "")
        if ak:
            config["llm"]["api_key"] = ak
            key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
            sources.setdefault("llm", {})["api_key"] = f"env:{key_name}"

    return config, sources


def load_config(cwd: Path | None = None) -> tuple[dict, dict]:
    """Load merged config. Returns (config, sources) where sources tracks provenance."""
    sources: dict[str, dict[str, str]] = {}

    # Layer 1: defaults
    config = deepcopy(DEFAULTS)
    for section, keys in DEFAULTS.items():
        sources[section] = {k: "default" for k in keys}

    # Layer 2: user config
    user_path = get_user_config_path()
    user_data = _load_toml_safe(user_path)
    if user_data:
        config = _deep_merge(config, user_data)
        for section, keys in user_data.items():
            if isinstance(keys, dict):
                for k in keys:
                    sources.setdefault(section, {})[k] = f"user:{user_path}"

    # Layer 3: project config
    project_path = get_project_config_path(cwd)
    project_data = _load_toml_safe(project_path)
    if project_data:
        config = _deep_merge(config, project_data)
        for section, keys in project_data.items():
            if isinstance(keys, dict):
                for k in keys:
                    sources.setdefault(section, {})[k] = f"project:{project_path.name}"

    # Layer 4: env vars
    config, sources = _apply_env_vars(config, sources)

    return config, sources


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    # Escape string values
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dict_to_toml(data: dict) -> str:
    lines = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    sections = {k: v for k, v in data.items() if isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_value(v)}")
    for k, v in sections.items():
        if lines:
            lines.append("")
        lines.append(f"[{k}]")
        for sk, sv in v.items():
            lines.append(f"{sk} = {_toml_value(sv)}")
    return "\n".join(lines) + "\n"


def set_config_value(key_path: str, value: str) -> None:
    """Set a config value in the user config file. key_path must be 'section.key'."""
    parts = key_path.split(".")
    if len(parts) != 2:
        raise ValueError(f"Key must be 'section.key', got: {key_path!r}")
    section, key = parts

    if section not in VALID_KEYS:
        raise ValueError(f"Unknown section '{section}'. Valid: {', '.join(sorted(VALID_KEYS))}")
    if key not in VALID_KEYS[section]:
        raise ValueError(f"Unknown key '{key}' in [{section}]. Valid: {', '.join(sorted(VALID_KEYS[section]))}")

    # Type coerce
    coerced: Any = value
    if value.lower() in ("true", "false"):
        coerced = value.lower() == "true"
    elif value.isdigit():
        coerced = int(value)

    path = get_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_toml_safe(path)
    existing.setdefault(section, {})[key] = coerced
    path.write_text(_dict_to_toml(existing))
