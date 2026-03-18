"""Tests for config loading and saving."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.config import load_config, save_config
from skill_manager.models import SmConfig


def test_load_empty_config(tmp_path: Path):
    config = load_config(tmp_path / "nonexistent.toml")
    assert config.source_paths == []
    assert config.target_paths == []
    assert config.plugins is True


def test_roundtrip(tmp_path: Path):
    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path)],
    )

    config_path = tmp_path / "csm.toml"
    save_config(config, config_path)

    loaded = load_config(config_path)
    assert loaded.plugins is False
    assert len(loaded.source_paths) == 1
    assert loaded.source_paths[0] == str(tmp_path / "skills")
    assert len(loaded.target_paths) == 1


def test_glob_patterns_preserved(tmp_path: Path):
    """Glob patterns like * and ** are preserved through save/load."""
    config = SmConfig(
        source_paths=["~/code/*", "~/vaults/**"],
        target_paths=["~", "~/code/*/backend"],
    )

    config_path = tmp_path / "csm.toml"
    save_config(config, config_path)

    loaded = load_config(config_path)
    assert loaded.source_paths == ["~/code/*", "~/vaults/**"]
    assert loaded.target_paths == ["~", "~/code/*/backend"]


def test_empty_config_roundtrip(tmp_path: Path):
    """An empty SmConfig saves and loads back cleanly."""
    config = SmConfig()
    config_path = tmp_path / "empty.toml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.plugins is True
    assert loaded.source_paths == []
    assert loaded.target_paths == []


def test_plugins_false_roundtrip(tmp_path: Path):
    """plugins=False is persisted and restored."""
    config = SmConfig(plugins=False)
    config_path = tmp_path / "no_plugins.toml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.plugins is False


def test_glob_patterns_with_question_mark(tmp_path: Path):
    """Glob patterns with ? are preserved."""
    config = SmConfig(
        source_paths=["~/code/proj-?"],
        target_paths=["~/vaults/vault-?"],
    )
    config_path = tmp_path / "qmark.toml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.source_paths == ["~/code/proj-?"]
    assert loaded.target_paths == ["~/vaults/vault-?"]


def test_glob_patterns_with_brackets(tmp_path: Path):
    """Glob patterns with character classes [...] are preserved."""
    config = SmConfig(
        source_paths=["~/code/[abc]"],
    )
    config_path = tmp_path / "brackets.toml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.source_paths == ["~/code/[abc]"]


def test_multiple_paths_roundtrip(tmp_path: Path):
    """Multiple paths in both lists are preserved."""
    config = SmConfig(
        source_paths=["~/a", "~/b", "~/c/*"],
        target_paths=["~", "~/code/*", "~/vaults/**"],
    )
    config_path = tmp_path / "multi.toml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.source_paths == config.source_paths
    assert loaded.target_paths == config.target_paths


def test_save_creates_parent_dirs(tmp_path: Path):
    """save_config creates parent directories if they don't exist."""
    config = SmConfig(source_paths=["~/skills"])
    nested_path = tmp_path / "deep" / "nested" / "csm.toml"
    save_config(config, nested_path)
    loaded = load_config(nested_path)
    assert loaded.source_paths == ["~/skills"]
