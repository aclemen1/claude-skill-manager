"""Tests for diagnostics — per-target detection and install guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.conflicts import detect_diagnostics, check_install_guards
from skill_manager.models import (
    ConflictSeverity, DiscoveredItem, Install, InstallMethod, ItemType,
)


def _item(name: str, source: str, path: str = "/tmp") -> DiscoveredItem:
    return DiscoveredItem(
        name=name, source_name=source, item_type=ItemType.SKILL, path=Path(path),
    )


def _install(source_qname: str, target: str, method=InstallMethod.SYMLINK) -> Install:
    return Install(
        source=source_qname, target=target, name=source_qname.rsplit(":", 1)[-1],
        method=method,
    )


# ── detect_diagnostics ───────────────────────────────────────


def test_no_conflicts():
    items = [_item("a", "lib"), _item("b", "lib")]
    installs = [_install("lib:a", "proj"), _install("lib:b", "proj")]
    assert detect_diagnostics(items, installs) == []


def test_same_name_different_targets_no_conflict():
    items = [_item("foo", "lib1"), _item("foo", "lib2")]
    installs = [_install("lib1:foo", "proj-a"), _install("lib2:foo", "proj-b")]
    assert detect_diagnostics(items, installs) == []


def test_user_user_conflict_same_target():
    items = [_item("foo", "lib1"), _item("foo", "lib2")]
    installs = [_install("lib1:foo", "proj-a"), _install("lib2:foo", "proj-a")]
    diags = detect_diagnostics(items, installs)
    assert len(diags) == 1
    assert diags[0].severity == ConflictSeverity.ERROR
    assert diags[0].conflict_type == "user-user"
    assert diags[0].target == "proj-a"


def test_user_plugin_same_target():
    items = [
        _item("pdf", "my-lib"),
        _item("pdf", "plugin:doc-skills@anthropic"),
    ]
    installs = [
        _install("my-lib:pdf", "proj-a"),
        _install("plugin:doc-skills@anthropic:pdf", "proj-a", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    assert any(c.conflict_type == "user-plugin" for c in diags)


def test_cross_marketplace_same_target():
    items = [
        _item("frontend-design", "mp:marketplace-a"),
        _item("frontend-design", "plugin:skills@marketplace-b"),
    ]
    installs = [
        _install("mp:marketplace-a:frontend-design", "user", InstallMethod.PLUGIN),
        _install("plugin:skills@marketplace-b:frontend-design", "user", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    assert any(c.conflict_type == "cross-marketplace" for c in diags)


def test_mp_cache_same_target_is_info():
    items = [
        _item("pdf", "mp:anthropic"),
        _item("pdf", "plugin:doc-skills@anthropic"),
    ]
    installs = [
        _install("mp:anthropic:pdf", "user", InstallMethod.PLUGIN),
        _install("plugin:doc-skills@anthropic:pdf", "user", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    assert len(diags) == 1
    assert diags[0].severity == ConflictSeverity.INFO


def test_multi_version_same_marketplace_is_info():
    items = [
        _item("gmail", "plugin:productivity@aclemen1-marketplace"),
        _item("gmail", "plugin:productivity@aclemen1-marketplace#2.6.1"),
    ]
    installs = [
        _install("plugin:productivity@aclemen1-marketplace:gmail", "user", InstallMethod.PLUGIN),
        _install("plugin:productivity@aclemen1-marketplace#2.6.1:gmail", "user", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    assert len(diags) == 1
    assert diags[0].severity == ConflictSeverity.INFO


def test_orphans_ignored_in_item_collisions():
    items = [_item("foo", "lib")]
    installs = [
        _install("lib:foo", "proj"),
        Install(name="foo", target="proj", method=InstallMethod.ORPHAN),
    ]
    diags = detect_diagnostics(items, installs)
    # No item-vs-item collision (orphan skipped in that pass)
    item_diags = [d for d in diags if d.conflict_type != "orphan-plugin"]
    assert len(item_diags) == 0


def test_global_fallback():
    items = [_item("foo", "lib1"), _item("foo", "lib2")]
    diags = detect_diagnostics(items)
    assert len(diags) == 1
    assert diags[0].target == ""


# ── Case 6: orphan-plugin ────────────────────────────────────


def test_orphan_plugin_detected():
    """Orphan skill with same name as a CC plugin in the same target."""
    items = [_item("pdf", "plugin:doc@anthropic")]
    installs = [
        Install(name="pdf", target="proj-a", method=InstallMethod.ORPHAN),
        _install("plugin:doc@anthropic:pdf", "proj-a", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    orphan_diags = [d for d in diags if d.conflict_type == "orphan-plugin"]
    assert len(orphan_diags) == 1
    assert orphan_diags[0].target == "proj-a"
    assert orphan_diags[0].severity == ConflictSeverity.WARNING
    assert "orphan:pdf" in orphan_diags[0].items


def test_orphan_no_plugin_no_diagnostic():
    """Orphan alone doesn't trigger orphan-plugin diagnostic."""
    items = []
    installs = [Install(name="foo", target="proj", method=InstallMethod.ORPHAN)]
    diags = detect_diagnostics(items, installs)
    assert len(diags) == 0


def test_orphan_plugin_different_names_no_diagnostic():
    """Orphan and plugin with different names don't conflict."""
    items = [_item("pdf", "plugin:doc@anthropic")]
    installs = [
        Install(name="xlsx", target="proj", method=InstallMethod.ORPHAN),
        _install("plugin:doc@anthropic:pdf", "proj", InstallMethod.PLUGIN),
    ]
    diags = detect_diagnostics(items, installs)
    orphan_diags = [d for d in diags if d.conflict_type == "orphan-plugin"]
    assert len(orphan_diags) == 0


# ── check_install_guards ─────────────────────────────────────


def test_guard_case1_two_local_same_name():
    """Case 1: two local symlinks with same deploy_name in same target → error."""
    pending = [
        ("lib1:foo", "foo", "proj"),
        ("lib2:foo", "foo", "proj"),
    ]
    issues = check_install_guards(pending, [], [])
    errors = [msg for msg, sev in issues if sev == "error"]
    assert len(errors) == 1
    assert "both" in errors[0]


def test_guard_case1_different_targets_ok():
    """Case 1 variant: same name in different targets → no error."""
    pending = [
        ("lib1:foo", "foo", "proj-a"),
        ("lib2:foo", "foo", "proj-b"),
    ]
    issues = check_install_guards(pending, [], [])
    assert len(issues) == 0


def test_guard_existing_symlink_different_source():
    """Installing a skill when a different source is already installed → error."""
    pending = [("lib2:foo", "foo", "proj")]
    existing = [_install("lib1:foo", "proj", InstallMethod.SYMLINK)]
    issues = check_install_guards(pending, existing, [])
    errors = [msg for msg, sev in issues if sev == "error"]
    assert len(errors) == 1
    assert "already installed" in errors[0]


def test_guard_existing_symlink_same_source_ok():
    """Re-installing the same source → no error (idempotent)."""
    pending = [("lib:foo", "foo", "proj")]
    existing = [_install("lib:foo", "proj", InstallMethod.SYMLINK)]
    issues = check_install_guards(pending, existing, [])
    errors = [msg for msg, sev in issues if sev == "error"]
    assert len(errors) == 0


def test_guard_orphan_blocks():
    """Case 5: orphan exists where we want to install → error."""
    pending = [("lib:foo", "foo", "proj")]
    existing = [Install(name="foo", target="proj", method=InstallMethod.ORPHAN)]
    issues = check_install_guards(pending, existing, [])
    errors = [msg for msg, sev in issues if sev == "error"]
    assert len(errors) == 1
    assert "unmanaged" in errors[0]


def test_guard_case2_local_vs_plugin_warns():
    """Case 2: local skill shadows CC plugin → warning."""
    items = [_item("pdf", "my-lib")]
    pending = [("my-lib:pdf", "pdf", "proj")]
    existing = [_install("plugin:doc@anthropic:pdf", "proj", InstallMethod.PLUGIN)]
    issues = check_install_guards(pending, existing, items)
    warnings = [msg for msg, sev in issues if sev == "warning"]
    assert len(warnings) == 1
    assert "coexist" in warnings[0]


def test_guard_no_existing_ok():
    """No existing installs → no issues."""
    pending = [("lib:foo", "foo", "proj")]
    issues = check_install_guards(pending, [], [])
    assert len(issues) == 0
