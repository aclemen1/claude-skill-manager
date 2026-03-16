"""Tests for the Settings modal screen."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from skill_manager.models import SmConfig
from skill_manager.core.config import save_config, load_config


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """Isolated environment with a config file for settings tests."""
    config_path = tmp_path / "sm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("skill_manager.tui.screens.settings.DEFAULT_CONFIG_PATH", config_path)

    for name in ("alpha", "beta"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

    (tmp_path / "projects" / "proj-a" / ".claude" / "skills").mkdir(parents=True)

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path / "projects" / "*")],
    )
    save_config(config, config_path)
    return tmp_path, config_path


async def _open_settings(app, pilot):
    """Helper: wait for load then open settings."""
    await asyncio.sleep(0.5)
    await pilot.press("s")
    await pilot.pause()
    return app.screen


# ── Open / Close ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_opens_and_closes(settings_env):
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        assert len(app.screen_stack) > 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


# ── Table displays config ────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_table_shows_config(settings_env):
    tmp_path, _ = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        assert screen._plugins is False
        assert len(screen._source_paths) == 1
        assert len(screen._target_paths) == 1


# ── Toggle plugins ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_plugins(settings_env):
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        assert screen._plugins is False

        # Plugins is first row — selected by default
        await pilot.press("space")
        await pilot.pause()
        assert screen._plugins is True

        await pilot.press("space")
        await pilot.pause()
        assert screen._plugins is False


# ── Delete a path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_source_path(settings_env):
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        n_before = len(screen._source_paths)

        # row 0=plugins, 1=src_header, 2=first source
        await pilot.press("down", "down")
        await pilot.press("d")
        await pilot.pause()
        assert len(screen._source_paths) == n_before - 1


@pytest.mark.asyncio
async def test_delete_target_path(settings_env):
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        n_before = len(screen._target_paths)

        # Navigate to target row
        dt = screen.query_one("#settings-table")
        for row_idx in range(dt.row_count):
            key = dt.coordinate_to_cell_key((row_idx, 0)).row_key
            if str(key.value).startswith("tgt:"):
                dt.move_cursor(row=row_idx)
                break
        await pilot.press("d")
        await pilot.pause()
        assert len(screen._target_paths) == n_before - 1


# ── Add a path via 'a' key ──────────────────────────────────


@pytest.mark.asyncio
async def test_add_source_path(settings_env):
    """'a' on source section shows input, typing + Enter adds the path."""
    tmp_path, _ = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        n_before = len(screen._source_paths)

        # Navigate to source header
        await pilot.press("down")  # src_header

        # Press 'a' to start adding
        await pilot.press("a")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert inp.has_class("visible")
        assert "source" in inp.placeholder

        # Type path and submit
        new_path = str(tmp_path / "new-source")
        inp.value = new_path
        await pilot.press("enter")
        await pilot.pause()

        assert len(screen._source_paths) == n_before + 1
        assert new_path in screen._source_paths[-1]
        # Input should be hidden again
        assert not inp.has_class("visible")


@pytest.mark.asyncio
async def test_add_target_path(settings_env):
    """'a' on target section adds a target path."""
    tmp_path, _ = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        n_before = len(screen._target_paths)

        # Navigate to target header
        dt = screen.query_one("#settings-table")
        for row_idx in range(dt.row_count):
            key = dt.coordinate_to_cell_key((row_idx, 0)).row_key
            if str(key.value) == "_tgt_header":
                dt.move_cursor(row=row_idx)
                break

        await pilot.press("a")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert inp.has_class("visible")
        assert "target" in inp.placeholder

        new_path = str(tmp_path / "new-targets")
        inp.value = new_path
        await pilot.press("enter")
        await pilot.pause()

        assert len(screen._target_paths) == n_before + 1


# ── Edit inline ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_source_path(settings_env):
    """Enter on a source path opens input pre-filled, editing replaces the value."""
    tmp_path, _ = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        old_value = screen._source_paths[0]

        # Navigate to first source path (row 0=plugins, 1=src_header, 2=first source)
        await pilot.press("down", "down")
        await pilot.press("enter")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert inp.has_class("visible")
        # Input should be pre-filled with shortened path
        assert inp.value != ""

        # Replace with new value
        new_path = "~/new-source/*"
        inp.value = new_path
        await pilot.press("enter")
        await pilot.pause()

        # Should have replaced, not appended
        assert len(screen._source_paths) == 1
        assert screen._source_paths[0] == new_path
        assert not inp.has_class("visible")


@pytest.mark.asyncio
async def test_edit_target_path(settings_env):
    """Enter on a target path edits it inline."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        # Navigate to target path
        dt = screen.query_one("#settings-table")
        for row_idx in range(dt.row_count):
            key = dt.coordinate_to_cell_key((row_idx, 0)).row_key
            if str(key.value).startswith("tgt:"):
                dt.move_cursor(row=row_idx)
                break

        await pilot.press("enter")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert inp.has_class("visible")

        inp.value = "~/new-target/**"
        await pilot.press("enter")
        await pilot.pause()

        assert screen._target_paths[0] == "~/new-target/**"


@pytest.mark.asyncio
async def test_enter_on_plugins_toggles(settings_env):
    """Enter on plugins row toggles instead of editing."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)
        assert screen._plugins is False

        # Cursor starts on plugins
        await pilot.press("enter")
        await pilot.pause()
        assert screen._plugins is True

        inp = screen.query_one("#path-input")
        assert not inp.has_class("visible")


# ── Esc closes input before dismissing modal ────────────────


@pytest.mark.asyncio
async def test_esc_closes_input_first(settings_env):
    """First Esc closes the input, second Esc dismisses the modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        # Navigate to source, press 'a'
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert inp.has_class("visible")

        # First Esc: close input
        await pilot.press("escape")
        await pilot.pause()
        assert not inp.has_class("visible")
        assert len(app.screen_stack) > 1  # still in settings

        # Second Esc: dismiss modal
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


# ── 'a' on non-path section does nothing ─────────────────────


@pytest.mark.asyncio
async def test_add_on_plugins_row_does_nothing(settings_env):
    """'a' on the plugins row should not open the input."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        # Cursor starts on plugins row
        await pilot.press("a")
        await pilot.pause()

        inp = screen.query_one("#path-input")
        assert not inp.has_class("visible")


# ── Save persists to disk ───────────────────────────────────


@pytest.mark.asyncio
async def test_save_persists(settings_env):
    _, config_path = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        # Toggle plugins on
        await pilot.press("space")
        await pilot.pause()
        assert screen._plugins is True

        # Save with Ctrl+S
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        config = load_config(config_path)
        assert config.plugins is True


# ── Cancel discards changes ──────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_discards(settings_env):
    _, config_path = settings_env
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        await pilot.press("space")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        config = load_config(config_path)
        assert config.plugins is False


# ── Tab works inside settings modal ──────────────────────────


@pytest.mark.asyncio
async def test_tab_works_in_settings(settings_env):
    """Tab should cycle between DataTable and Input within the modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_settings(app, pilot)

        # Open input
        await pilot.press("down")
        await pilot.press("a")
        await pilot.pause()

        # Focus should be on input
        focused = app.focused
        assert focused.id == "path-input"

        # Tab should go back to table
        await pilot.press("tab")
        await pilot.pause()
        focused = app.focused
        assert focused.id == "settings-table"
