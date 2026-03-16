"""Tests for outdated plugin detection and TUI update action."""

from __future__ import annotations

import asyncio

import pytest

from skill_manager.core.updates import detect_outdated
from skill_manager.models import SmConfig
from skill_manager.core.config import save_config


@pytest.fixture
def mock_cc_data(monkeypatch):
    """Provide mock Claude Code plugin data."""
    def _set(plugins):
        monkeypatch.setattr(
            "skill_manager.core.updates.fetch_claude_code_data",
            lambda: {"marketplaces": [], "plugins": plugins},
        )
    return _set


def test_no_outdated(mock_cc_data):
    mock_cc_data([
        {"id": "prod@mp", "version": "1.0", "scope": "user",
         "installPath": "/cache/mp/prod/1.0",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
    ])
    assert detect_outdated() == []


def test_detect_outdated_user_scope(mock_cc_data):
    """Two versions of same plugin in user scope → older is outdated."""
    mock_cc_data([
        {"id": "prod@mp", "version": "1.0", "scope": "user",
         "installPath": "/cache/mp/prod/1.0",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
        {"id": "prod@mp", "version": "2.0", "scope": "user",
         "installPath": "/cache/mp/prod/2.0",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
    ])
    outdated = detect_outdated()
    assert len(outdated) == 1
    assert outdated[0].current_version == "1.0"
    assert outdated[0].latest_version == "2.0"
    assert "user" in outdated[0].target


def test_detect_outdated_project_scope(mock_cc_data):
    """Two versions of same plugin in same project → older is outdated."""
    mock_cc_data([
        {"id": "prod@mp", "version": "old", "scope": "project",
         "installPath": "/cache/mp/prod/old",
         "projectPath": "/home/user/proj-a",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
        {"id": "prod@mp", "version": "new", "scope": "project",
         "installPath": "/cache/mp/prod/new",
         "projectPath": "/home/user/proj-a",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
    ])
    outdated = detect_outdated()
    assert len(outdated) == 1
    assert outdated[0].current_version == "old"
    assert outdated[0].latest_version == "new"
    assert "proj-a" in outdated[0].target


def test_different_projects_not_outdated(mock_cc_data):
    """Same plugin in two different projects with different versions is NOT outdated."""
    mock_cc_data([
        {"id": "prod@mp", "version": "1.0", "scope": "project",
         "installPath": "/cache/mp/prod/1.0",
         "projectPath": "/home/user/proj-a",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
        {"id": "prod@mp", "version": "2.0", "scope": "project",
         "installPath": "/cache/mp/prod/2.0",
         "projectPath": "/home/user/proj-b",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
    ])
    assert detect_outdated() == []


def test_same_version_not_outdated(mock_cc_data):
    """Same version in multiple projects is NOT outdated."""
    mock_cc_data([
        {"id": "prod@mp", "version": "1.0", "scope": "project",
         "installPath": "/cache/mp/prod/1.0",
         "projectPath": "/home/user/proj-a",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
        {"id": "prod@mp", "version": "1.0", "scope": "project",
         "installPath": "/cache/mp/prod/1.0",
         "projectPath": "/home/user/proj-b",
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
    ])
    assert detect_outdated() == []


def test_target_shows_path(mock_cc_data, tmp_path):
    """Target shows the project path for project-scoped plugins."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    mock_cc_data([
        {"id": "prod@mp", "version": "old", "scope": "project",
         "installPath": "/cache/mp/prod/old",
         "projectPath": str(proj),
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
        {"id": "prod@mp", "version": "new", "scope": "project",
         "installPath": "/cache/mp/prod/new",
         "projectPath": str(proj),
         "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
    ])
    outdated = detect_outdated()
    assert len(outdated) == 1
    assert "my-project" in outdated[0].target


# ── TUI: update refreshes the modal ─────────────────────────


_OUTDATED_PLUGINS = [
    {"id": "prod@mp", "version": "1.0", "scope": "user",
     "installPath": "/cache/mp/prod/1.0",
     "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:00:00Z"},
    {"id": "prod@mp", "version": "2.0", "scope": "user",
     "installPath": "/cache/mp/prod/2.0",
     "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
]

_UPTODATE_PLUGINS = [
    {"id": "prod@mp", "version": "2.0", "scope": "user",
     "installPath": "/cache/mp/prod/2.0",
     "installedAt": "2026-01-01T00:00:00Z", "lastUpdated": "2026-03-01T00:00:00Z"},
]


@pytest.fixture
def tui_update_env(tmp_path, monkeypatch):
    """TUI environment with mock outdated plugins."""
    config_path = tmp_path / "sm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    # Create a skill + target
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\n---\n")
    (tmp_path / "proj" / ".claude" / "skills").mkdir(parents=True)

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path / "proj")],
    )
    save_config(config, config_path)

    # Mock CC data: starts with outdated plugins
    call_count = {"n": 0}

    def mock_cc_data():
        call_count["n"] += 1
        # After first call (initial load), simulate update succeeded
        if call_count["n"] <= 2:
            return {"marketplaces": [], "plugins": _OUTDATED_PLUGINS}
        return {"marketplaces": [], "plugins": _UPTODATE_PLUGINS}

    monkeypatch.setattr("skill_manager.core.updates.fetch_claude_code_data", mock_cc_data)
    monkeypatch.setattr("skill_manager.core.discovery.fetch_claude_code_data", mock_cc_data)

    return tmp_path


@pytest.mark.asyncio
async def test_tui_stale_cache_displayed(tui_update_env):
    """Stale cache tab shows outdated plugin entries."""
    from skill_manager.tui.app import SkillManagerApp
    from textual.widgets import DataTable

    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Open diagnostics modal
        await pilot.press("D")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        screen = app.screen

        # Switch to Stale cache tab
        tabs = screen.query_one("TabbedContent")
        tabs.active = "tab-updates"
        await pilot.pause()

        # The table should show the stale plugin
        updates_table = screen.query_one("#updates-table", DataTable)
        assert updates_table.row_count == 1


@pytest.mark.asyncio
async def test_tui_diagnostics_modal_has_both_tabs(tui_update_env):
    """The diagnostics modal should have Conflicts and Stale cache tabs."""
    from skill_manager.tui.app import SkillManagerApp

    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("D")
        await pilot.pause()

        screen = app.screen
        tabs = screen.query_one("TabbedContent")
        tab_ids = {pane.id for pane in tabs.query("TabPane")}
        assert "tab-conflicts" in tab_ids
        assert "tab-updates" in tab_ids
