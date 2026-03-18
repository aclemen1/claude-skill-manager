"""Tests for install/uninstall via symlinks and filesystem detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.deployer import (
    install_symlink, uninstall_symlink,
    scan_target_installs, _check_one,
    get_install_state,
)
from skill_manager.models import (
    DiscoveredItem, InstallMethod, InstallState, ItemType, TargetConfig,
)


@pytest.fixture
def skill_item(tmp_path: Path) -> DiscoveredItem:
    skill_dir = tmp_path / "source" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\nContent.")
    return DiscoveredItem(
        name="my-skill", source_name="lib", item_type=ItemType.SKILL, path=skill_dir,
    )


@pytest.fixture
def target_dir(tmp_path: Path) -> Path:
    d = tmp_path / "target" / "skills"
    d.mkdir(parents=True)
    return d


def test_install_creates_symlink(skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    ok, msg = install_symlink(skill_item, "user", target_cfg)
    assert ok
    symlink = target_dir / "my-skill"
    assert symlink.is_symlink()
    assert symlink.resolve() == skill_item.path.resolve()


def test_install_idempotent(skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    install_symlink(skill_item, "user", target_cfg)
    ok, _ = install_symlink(skill_item, "user", target_cfg)
    assert ok  # re-install overwrites


def test_install_conflict_with_real_dir(skill_item, target_dir):
    (target_dir / "my-skill").mkdir()
    target_cfg = TargetConfig(path=target_dir)
    ok, msg = install_symlink(skill_item, "user", target_cfg)
    assert not ok
    assert "not a symlink" in msg


def test_uninstall(skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    install_symlink(skill_item, "user", target_cfg)
    ok, _ = uninstall_symlink("my-skill", target_cfg)
    assert ok
    assert not (target_dir / "my-skill").exists()


def test_uninstall_nonexistent(target_dir):
    target_cfg = TargetConfig(path=target_dir)
    ok, _ = uninstall_symlink("nope", target_cfg)
    assert not ok


def test_scan_detects_symlink(skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    install_symlink(skill_item, "user", target_cfg)
    installs = scan_target_installs("user", target_cfg, {}, [skill_item])
    assert len(installs) == 1
    assert installs[0].method == InstallMethod.SYMLINK
    assert installs[0].source == "lib:my-skill"


def test_scan_detects_orphan(tmp_path):
    skills_dir = tmp_path / "skills"
    orphan = skills_dir / "orphan-skill"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text("---\nname: orphan\n---\n")
    target_cfg = TargetConfig(path=skills_dir)
    installs = scan_target_installs("test", target_cfg, {}, [])
    assert len(installs) == 1
    assert installs[0].method == InstallMethod.ORPHAN
    assert installs[0].name == "orphan-skill"


def test_scan_detects_broken_symlink(tmp_path, skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    install_symlink(skill_item, "user", target_cfg)
    # Delete the source
    import shutil
    shutil.rmtree(skill_item.path)
    installs = scan_target_installs("user", target_cfg, {}, [])
    assert len(installs) == 1
    assert installs[0].method == InstallMethod.ORPHAN  # can't resolve source


def test_get_install_state(skill_item, target_dir):
    target_cfg = TargetConfig(path=target_dir)
    targets = {"user": target_cfg}
    sources = {"lib": None}

    assert get_install_state(skill_item, targets, sources, [skill_item]) == InstallState.AVAILABLE

    install_symlink(skill_item, "user", target_cfg)
    assert get_install_state(skill_item, targets, sources, [skill_item]) == InstallState.INSTALLED


# ── _parse_plugin_ref tests ──────────────────────────────────


from skill_manager.core.deployer import _parse_plugin_ref, _resolve_plugin_ref


def test_parse_plugin_ref_standard():
    """plugin:name@marketplace:skill → name@marketplace"""
    ref = _parse_plugin_ref("plugin:productivity@aclemen1-marketplace:gmail")
    assert ref == "productivity@aclemen1-marketplace"


def test_parse_plugin_ref_with_version():
    """plugin:name@marketplace#version:skill → name@marketplace (strips version)."""
    ref = _parse_plugin_ref("plugin:productivity@aclemen1-marketplace#2.6.1:gmail")
    assert ref == "productivity@aclemen1-marketplace"


def test_parse_plugin_ref_no_at():
    """plugin:something without @ returns None."""
    ref = _parse_plugin_ref("plugin:localonly:skill")
    assert ref is None


def test_parse_plugin_ref_mp_returns_none():
    """mp: prefixed names return None (not installed plugins)."""
    ref = _parse_plugin_ref("mp:anthropic-agent-skills:pdf")
    assert ref is None


def test_parse_plugin_ref_bare_string():
    """Non-prefixed string returns None."""
    ref = _parse_plugin_ref("some-random-string")
    assert ref is None


def test_parse_plugin_ref_empty():
    ref = _parse_plugin_ref("")
    assert ref is None


# ── _resolve_plugin_ref tests ────────────────────────────────


def test_resolve_plugin_ref_plugin_source():
    """For plugin: sources, resolves directly."""
    ref = _resolve_plugin_ref("plugin:prod@my-mp:skill1")
    assert ref == "prod@my-mp"


def test_resolve_plugin_ref_mp_no_match():
    """mp: source with no matching installs and no items returns None."""
    ref = _resolve_plugin_ref("mp:my-marketplace:my-skill", all_installs_list=[], items=[])
    assert ref is None


def test_resolve_plugin_ref_mp_matching_install():
    """mp: source matched to an installed plugin via installs list."""
    from skill_manager.models import Install, InstallMethod
    inst = Install(
        source="plugin:prod@my-marketplace:my-skill",
        target="user",
        name="my-skill",
        method=InstallMethod.PLUGIN,
    )
    ref = _resolve_plugin_ref("mp:my-marketplace:my-skill", all_installs_list=[inst])
    assert ref == "prod@my-marketplace"


def test_resolve_plugin_ref_mp_fallback_items():
    """mp: source falls back to building ref from item metadata."""
    item = DiscoveredItem(
        name="my-skill",
        source_name="mp:my-marketplace",
        item_type=ItemType.SKILL,
        path=Path("/fake"),
        plugin_name="prod",
    )
    ref = _resolve_plugin_ref("mp:my-marketplace:my-skill", all_installs_list=[], items=[item])
    assert ref == "prod@my-marketplace"


def test_resolve_plugin_ref_not_mp_not_plugin():
    """Non-plugin, non-mp source returns None."""
    ref = _resolve_plugin_ref("auto:skills:my-skill")
    assert ref is None


def test_resolve_plugin_ref_mp_wrong_format():
    """mp: source with wrong format (missing skill part) returns None."""
    ref = _resolve_plugin_ref("mp:only-marketplace")
    assert ref is None


# ── adopt_orphan tests ────────────────────────────────────────


from skill_manager.core.deployer import adopt_orphan


def test_adopt_orphan_moves_and_creates_symlink(tmp_path):
    """adopt_orphan moves the dir to the source lib and creates a symlink back."""
    skills_dir = tmp_path / "target" / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    source_lib = tmp_path / "sources" / "mylib"
    source_lib.mkdir(parents=True)

    orphan = skills_dir / "my-orphan"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: my-orphan\n---\n")

    ok, msg = adopt_orphan(orphan, source_lib)

    assert ok
    assert "my-orphan" in msg
    # Original location is now a symlink
    assert orphan.is_symlink(), "original path should be a symlink after adoption"
    # Destination exists in source lib
    dest = source_lib / "my-orphan"
    assert dest.is_dir()
    assert (dest / "SKILL.md").exists()
    # Symlink points to dest
    assert orphan.resolve() == dest.resolve()


def test_adopt_orphan_creates_source_dir_if_missing(tmp_path):
    """adopt_orphan creates the destination source dir if it doesn't exist yet."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source_lib = tmp_path / "new-source-lib"  # does not exist yet

    orphan = skills_dir / "my-skill"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: my-skill\n---\n")

    ok, _ = adopt_orphan(orphan, source_lib)

    assert ok
    assert (source_lib / "my-skill").is_dir()


def test_adopt_orphan_fails_if_already_exists_in_source(tmp_path):
    """adopt_orphan returns False if the skill name already exists in the source."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source_lib = tmp_path / "source"
    source_lib.mkdir()

    orphan = skills_dir / "my-skill"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: my-skill\n---\n")

    # Pre-existing in source
    (source_lib / "my-skill").mkdir()

    ok, msg = adopt_orphan(orphan, source_lib)

    assert not ok
    assert "already exists" in msg
    # Original orphan should be untouched
    assert orphan.is_dir()
    assert not orphan.is_symlink()


def test_adopt_orphan_fails_if_symlink(tmp_path):
    """adopt_orphan refuses to adopt a symlink (only plain directories)."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source_lib = tmp_path / "source"
    source_lib.mkdir()

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    symlink = skills_dir / "my-skill"
    symlink.symlink_to(real_dir)

    ok, msg = adopt_orphan(symlink, source_lib)

    assert not ok
    assert "not a plain directory" in msg


def test_adopt_orphan_invalidates_cache(tmp_path):
    """adopt_orphan invalidates the installs cache."""
    from skill_manager.core.deployer import all_installs, invalidate_installs_cache

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source_lib = tmp_path / "source"
    source_lib.mkdir()

    orphan = skills_dir / "orphan"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: orphan\n---\n")

    target_cfg = TargetConfig(path=skills_dir)
    # Warm up the cache
    all_installs({"t": target_cfg}, {}, [])

    adopt_orphan(orphan, source_lib)

    # After adoption, orphan is now a symlink — re-scan should see it as such
    installs = scan_target_installs("t", target_cfg, {}, [])
    # It's now a symlink pointing to an unregistered source → orphan-symlink or empty
    assert len(installs) == 1
    assert installs[0].method == InstallMethod.ORPHAN  # unknown source symlink
    assert installs[0].symlink != Path()  # it IS a symlink now
