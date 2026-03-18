"""Configuration loading from csm.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

from skill_manager.models import SmConfig

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "claude-skill-manager"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "csm.toml"


def load_config(path: Path | None = None) -> SmConfig:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return SmConfig()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    return SmConfig(
        plugins=raw.get("plugins", True),
        source_paths=raw.get("source_paths", []),
        target_paths=raw.get("target_paths", []),
        theme=raw.get("theme", ""),
    )


def save_config(config: SmConfig, path: Path | None = None) -> None:
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}

    if not config.plugins:
        data["plugins"] = False
    if config.source_paths:
        data["source_paths"] = config.source_paths
    if config.target_paths:
        data["target_paths"] = config.target_paths
    if config.theme:
        data["theme"] = config.theme

    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def ensure_config_dir() -> Path:
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_DIR
