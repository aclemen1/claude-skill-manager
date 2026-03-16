"""Tests for plugin install synthesis and matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.deployer import (
    _same_skill_any_version,
    _mp_matches_plugin,
    _check_one,
    _parse_plugin_ref,
)
from skill_manager.models import (
    DiscoveredItem, Install, InstallMethod, InstallState, ItemType,
)


# ── _same_skill_any_version ──────────────────────────────────

def test_same_version():
    assert _same_skill_any_version("plugin:prod@mp:skill-a", "plugin:prod@mp:skill-a")

def test_different_version():
    assert _same_skill_any_version("plugin:prod@mp:skill-a", "plugin:prod@mp#2.0:skill-a")

def test_different_skill():
    assert not _same_skill_any_version("plugin:prod@mp:skill-a", "plugin:prod@mp:skill-b")

def test_different_plugin():
    assert not _same_skill_any_version("plugin:prod@mp:skill-a", "plugin:other@mp:skill-a")


# ── _mp_matches_plugin ───────────────────────────────────────

def test_mp_matches_basic():
    assert _mp_matches_plugin("my-mp", "pdf", "plugin:docs@my-mp:pdf")

def test_mp_matches_with_version():
    assert _mp_matches_plugin("my-mp", "pdf", "plugin:docs@my-mp#1.0:pdf")

def test_mp_no_match_wrong_marketplace():
    assert not _mp_matches_plugin("other-mp", "pdf", "plugin:docs@my-mp:pdf")

def test_mp_no_match_wrong_skill():
    assert not _mp_matches_plugin("my-mp", "xlsx", "plugin:docs@my-mp:pdf")


# ── _check_one ────────────────────────────────────────────────

def test_check_one_plugin_exists(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    inst = Install(source="plugin:x@mp:pdf", target="user", origin=skill_dir, method=InstallMethod.PLUGIN)
    assert _check_one(inst) == InstallState.INSTALLED

def test_check_one_plugin_missing():
    inst = Install(source="plugin:x@mp:pdf", target="user", origin=Path("/nonexistent"), method=InstallMethod.PLUGIN)
    assert _check_one(inst) == InstallState.BROKEN

def test_check_one_symlink(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    link = tmp_path / "link"
    link.symlink_to(origin)
    inst = Install(source="lib:foo", target="user", symlink=link, origin=origin, method=InstallMethod.SYMLINK)
    assert _check_one(inst) == InstallState.INSTALLED

def test_check_one_orphan(tmp_path):
    d = tmp_path / "orphan"
    d.mkdir()
    inst = Install(name="orphan", target="user", origin=d, method=InstallMethod.ORPHAN)
    assert _check_one(inst) == InstallState.INSTALLED

def test_check_one_no_symlink():
    inst = Install(source="lib:foo", target="user", method=InstallMethod.SYMLINK)
    assert _check_one(inst) == InstallState.BROKEN


# ── _parse_plugin_ref ─────────────────────────────────────────

def test_parse_plugin_ref_basic():
    assert _parse_plugin_ref("plugin:productivity@aclemen1-marketplace:gmail-adapter") == "productivity@aclemen1-marketplace"

def test_parse_plugin_ref_with_version():
    assert _parse_plugin_ref("plugin:productivity@aclemen1-marketplace#2.6.1:gmail-adapter") == "productivity@aclemen1-marketplace"

def test_parse_plugin_ref_no_at():
    assert _parse_plugin_ref("plugin:something:skill") is None

def test_parse_plugin_ref_local():
    assert _parse_plugin_ref("lib:my-skill") is None
