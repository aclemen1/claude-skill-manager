"""Tests for the TUI mechanics."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from skill_manager.models import SmConfig
from skill_manager.core.config import save_config


@pytest.fixture
def tui_env(tmp_path, monkeypatch):
    """Set up an isolated environment with skills, targets, and config."""
    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    # Create skills
    for name in ("alpha", "beta", "gamma"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}\n---\n")

    # Create two targets with .claude/skills
    for proj in ("project-a", "project-b"):
        (tmp_path / "projects" / proj / ".claude" / "skills").mkdir(parents=True)

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path / "projects" / "*")],
    )
    save_config(config, config_path)
    return tmp_path


# ── App startup ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_starts_and_loads(tui_env):
    """App starts, loads sources and targets."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Wait for async refresh
        await asyncio.sleep(0.5)
        assert hasattr(app, "items")
        assert hasattr(app, "all_targets")
        assert len(app.items) >= 3  # alpha, beta, gamma
        assert "project-a" in app.all_targets
        assert "project-b" in app.all_targets


# ── Tab navigation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tab_cycles_focus(tui_env):
    """Tab cycles between source tree, target tree, pending table."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)
        # Should start on source tree
        assert app.focused is not None
        assert app.focused.id == "src-tree"

        await pilot.press("tab")
        assert app.focused.id == "tgt-tree"

        await pilot.press("tab")
        assert app.focused.id == "pending-table"

        await pilot.press("tab")
        assert app.focused.id == "src-tree"

        # Shift+tab goes backwards
        await pilot.press("shift+tab")
        assert app.focused.id == "pending-table"


@pytest.mark.asyncio
async def test_tab_then_shift_tab_returns(tui_env):
    """Tab then Shift+Tab returns to the starting panel."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)
        assert app.focused.id == "src-tree"

        await pilot.press("tab")
        assert app.focused.id == "tgt-tree"

        await pilot.press("shift+tab")
        assert app.focused.id == "src-tree"


# ── Enter selects and switches focus ──────────────────────────


async def _nav_to_select_leaf(pilot, tree):
    """Navigate the source tree to find a select leaf."""
    for _ in range(15):
        node = tree.cursor_node
        if node and isinstance(node.data, tuple) and node.data[0] == "select":
            return True
        await pilot.press("j")
    return False


@pytest.mark.asyncio
async def test_space_on_source_switches_to_targets(tui_env):
    """Space on a source item selects it and moves focus to targets."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        tree = app.query_one("#src-tree")
        assert await _nav_to_select_leaf(pilot, tree)

        await pilot.press("space")
        await pilot.pause()

        assert app.focused.id == "tgt-tree"
        src = app.query_one("#source-panel")
        assert src._selected_qname != ""


@pytest.mark.asyncio
async def test_space_on_target_switches_to_sources(tui_env):
    """Space on a target selects it and moves focus to sources."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Go to target tree
        await pilot.press("tab")
        assert app.focused.id == "tgt-tree"

        # Navigate to a project target
        for _ in range(15):
            node = app.query_one("#tgt-tree").cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("target", "toggle"):
                break
            await pilot.press("j")

        await pilot.press("space")
        await pilot.pause()

        # Focus should move to source tree
        assert app.focused.id == "src-tree"

        # Target panel should have a selected target
        tgt = app.query_one("#target-panel")
        assert tgt._selected_target != ""


# ── Space toggles ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_x_toggles_on_target(tui_env):
    """After selecting a source, x on a target creates a pending change."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Select a source with Space
        tree = app.query_one("#src-tree")
        assert await _nav_to_select_leaf(pilot, tree)
        await pilot.press("space")
        await pilot.pause()

        # Now on target tree, navigate to a toggle leaf
        tgt_tree = app.query_one("#tgt-tree")
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "toggle":
                break
            await pilot.press("j")

        await pilot.press("x")
        await pilot.pause()

        assert app.pending.count == 1
        assert len(app.pending.installs) == 1


@pytest.mark.asyncio
async def test_x_toggles_on_source(tui_env):
    """After selecting a target, x on a source creates a pending change."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        assert app.pending.count == 1


# ── Space preserves expand state ──────────────────────────────


@pytest.mark.asyncio
async def test_x_preserves_tree_state(tui_env):
    """x (toggle) should not collapse/expand tree nodes."""
    from skill_manager.tui.app import SkillManagerApp
    from skill_manager.tui.widgets.tree_utils import save_expand_state
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Select a target with space
        await pilot.press("tab")
        for _ in range(15):
            tree = app.query_one("#tgt-tree")
            node = tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("target", "toggle"):
                break
            await pilot.press("j")
        await pilot.press("space")
        await pilot.pause()

        # Record source tree expand state
        src_tree = app.query_one("#src-tree")
        state_before = save_expand_state(src_tree)

        # Navigate to a toggle leaf
        for _ in range(15):
            node = src_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("toggle", "toggle_plugin"):
                break
            await pilot.press("j")

        await pilot.press("x")
        await pilot.pause()

        # Expand state should be mostly preserved (pending may expand one node)
        state_after = save_expand_state(src_tree)
        common_keys = set(state_before.keys()) & set(state_after.keys())
        changed = sum(1 for k in common_keys if state_before[k] != state_after[k])
        # At most 1 node should change (the one containing the toggled item)
        assert changed <= 1


# ── Pending changes ───────────────────────────────────────────


async def _create_pending_change(app, pilot):
    """Helper: select a source (space), then toggle a target (x) to create a pending change."""
    await asyncio.sleep(0.5)
    tree = app.query_one("#src-tree")
    assert await _nav_to_select_leaf(pilot, tree), "Could not find a select leaf in source tree"
    await pilot.press("space")
    await pilot.pause()

    tgt_tree = app.query_one("#tgt-tree")
    for _ in range(15):
        node = tgt_tree.cursor_node
        if node and isinstance(node.data, tuple) and node.data[0] == "toggle":
            break
        await pilot.press("j")
    await pilot.press("x")
    await pilot.pause()


@pytest.mark.asyncio
async def test_pending_delete(tui_env):
    """d key deletes a pending change."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        assert app.pending.count == 1

        # Go to pending table and delete
        await pilot.press("tab")  # to pending
        await pilot.press("d")
        await pilot.pause()
        assert app.pending.count == 0


@pytest.mark.asyncio
async def test_escape_cancels_all(tui_env):
    """Escape cancels all pending changes."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        assert app.pending.count >= 1

        await pilot.press("escape")
        await pilot.pause()
        assert app.pending.count == 0


# ── Apply modal ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_modal_shows_changes(tui_env):
    """Apply modal displays the pending changes (not empty)."""
    from skill_manager.tui.app import SkillManagerApp
    from textual.widgets import RichLog
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        assert app.pending.count >= 1

        # Press 'a' to open apply modal
        await pilot.press("a")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        # The RichLog should contain the change description
        log = app.screen.query_one("#apply-log", RichLog)
        # Wait for call_after_refresh to fill the log
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert len(log.lines) > 0, "Apply modal log should not be empty"

        # Cancel to close
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_apply_modal_cancel_does_not_apply(tui_env):
    """Pressing Esc in apply modal does not apply changes."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        n_before = app.pending.count

        await pilot.press("a")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        # Pending changes should still be there
        assert app.pending.count == n_before


@pytest.mark.asyncio
async def test_apply_modal_confirm_applies(tui_env):
    """Pressing Enter in apply modal applies and clears pending changes."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _create_pending_change(app, pilot)
        assert app.pending.count >= 1

        await pilot.press("a")
        await pilot.pause()
        # Wait for log to fill
        await asyncio.sleep(0.1)

        await pilot.press("enter")
        await pilot.pause()

        # Modal dismissed, pending cleared
        assert len(app.screen_stack) == 1
        assert app.pending.count == 0


@pytest.mark.asyncio
async def test_apply_preserves_toggle_mode(tui_env):
    """After applying, the TUI should stay in toggle mode with the same target selected."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Select a target first (Enter on target → source toggle mode)
        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("target", "toggle"):
                break
            await pilot.press("j")
        target_name = tgt_tree.cursor_node.data[1]
        await pilot.press("space")
        await pilot.pause()

        # Now in source toggle mode — find and toggle a skill or plugin
        src_tree = app.query_one("#src-tree")
        for _ in range(20):
            node = src_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("toggle", "toggle_plugin"):
                break
            await pilot.press("j")
        await pilot.press("x")
        await pilot.pause()
        assert app.pending.count >= 1

        # Apply
        await pilot.press("a")
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.press("enter")
        await pilot.pause()

        # Should still be in toggle mode with same target
        src = app.query_one("#source-panel")
        assert src._active_target == target_name


# ── Vi keybindings ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vi_navigation(tui_env):
    """j/k navigate the tree."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        tree = app.query_one("#src-tree")

        # j moves down twice
        await pilot.press("j")
        await pilot.press("j")
        line_after_jj = tree.cursor_line

        # k moves up
        await pilot.press("k")
        assert tree.cursor_line < line_after_jj


# ── Modals ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_modal(tui_env):
    """? opens help modal, Esc closes it."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("question_mark")
        await pilot.pause()
        # Help screen should be pushed
        assert len(app.screen_stack) > 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_settings_modal(tui_env):
    """s opens settings modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("s")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_diagnostics_modal(tui_env):
    """c opens diagnostics modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("D")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


# ── Selection visual marker ───────────────────────────────────


@pytest.mark.asyncio
async def test_selection_clears_on_other_side(tui_env):
    """Space on one side clears the selection marker on the other side."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        src = app.query_one("#source-panel")
        tgt = app.query_one("#target-panel")

        # Select a source with space
        tree = app.query_one("#src-tree")
        assert await _nav_to_select_leaf(pilot, tree)
        await pilot.press("space")
        await pilot.pause()
        assert src._selected_qname != ""
        assert tgt._selected_target == ""

        # Now select a target with space
        tgt_tree = app.query_one("#tgt-tree")
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("target", "toggle"):
                break
            await pilot.press("j")
        await pilot.press("space")
        await pilot.pause()
        assert tgt._selected_target != ""
        assert src._selected_qname == ""  # cleared


# ── Orphans ──────────────────────────────────────────────────


@pytest.fixture
def tui_env_with_orphans(tmp_path, monkeypatch):
    """Environment with skills, targets, and orphan skills in a target."""
    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    # Create skills in source
    for name in ("alpha", "beta"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}\n---\n")

    # Create target with .claude/skills
    skills_dir = tmp_path / "projects" / "myproj" / ".claude" / "skills"
    skills_dir.mkdir(parents=True)

    # Create orphan skills (local dirs, not symlinks, not from any source)
    for orphan in ("orphan-x", "orphan-y"):
        od = skills_dir / orphan
        od.mkdir()
        (od / "SKILL.md").write_text(f"---\nname: {orphan}\n---\n")

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path / "projects" / "*")],
    )
    save_config(config, config_path)
    return tmp_path


@pytest.mark.asyncio
async def test_orphans_visible_in_target_tree(tui_env_with_orphans):
    """Target nodes with orphans should have expandable children."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Find the target node for "myproj" in the target tree
        tgt_tree = app.query_one("#tgt-tree")
        found_target = None
        for node in tgt_tree.root.children:
            for child in _walk_tree(node):
                if isinstance(child.data, tuple) and child.data[0] == "target" and child.data[1] == "myproj":
                    found_target = child
                    break

        assert found_target is not None, "myproj target not found in tree"
        # Should NOT be a leaf (has orphan children)
        assert found_target.allow_expand, "target with orphans should be expandable"
        # Should have 2 orphan children
        assert len(found_target.children) == 2


@pytest.mark.asyncio
async def test_orphan_node_expand_with_l_key(tui_env_with_orphans):
    """l key should expand a non-leaf target node (orphans)."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Navigate to target tree
        await pilot.press("tab")
        assert app.focused.id == "tgt-tree"

        tgt_tree = app.query_one("#tgt-tree")

        # Navigate down to find the myproj target node
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        assert node.data == ("target", "myproj"), f"Expected myproj target, got {node.data}"

        # Node should be collapsed by default
        assert not node.is_expanded

        # Press l to expand
        await pilot.press("l")
        assert node.is_expanded, "l should expand the orphan node"

        # Press h to go back / collapse
        await pilot.press("h")


@pytest.mark.asyncio
async def test_space_on_orphan_parent_navigates(tui_env_with_orphans):
    """Space on a target node with orphans should select it (navigate to sources)."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")

        # Navigate to myproj
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        # Space selects the target
        await pilot.press("space")
        await pilot.pause()

        # Focus should move to source tree
        assert app.focused.id == "src-tree"
        tgt = app.query_one("#target-panel")
        assert tgt._selected_target == "myproj"


@pytest.mark.asyncio
async def test_tab_still_works_after_orphan_interaction(tui_env_with_orphans):
    """Tab cycling should work correctly even after interacting with orphan nodes."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Full tab cycle
        assert app.focused.id == "src-tree"
        await pilot.press("tab")
        assert app.focused.id == "tgt-tree"
        await pilot.press("tab")
        assert app.focused.id == "pending-table"
        await pilot.press("tab")
        assert app.focused.id == "src-tree"

        # Navigate to target, interact with orphan node, then tab again
        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target":
                break
            await pilot.press("j")
        await pilot.press("l")  # expand orphan node
        await pilot.press("j")  # navigate into orphan child

        # Tab should still cycle correctly
        await pilot.press("tab")
        assert app.focused.id == "pending-table"
        await pilot.press("tab")
        assert app.focused.id == "src-tree"


def _walk_tree(node):
    """Recursively yield all nodes in a tree."""
    yield node
    for child in node.children:
        yield from _walk_tree(child)


# ── Orphan adoption (TUI) ────────────────────────────────────


async def _nav_to_orphan_child(pilot, tgt_tree):
    """Navigate to a real-dir orphan leaf node in the target tree.

    Returns True if found, False otherwise.
    """
    for _ in range(20):
        node = tgt_tree.cursor_node
        if node and isinstance(node.data, tuple) and node.data[0] == "orphan":
            from pathlib import Path
            _, name, origin, symlink, target = node.data
            if symlink == Path():  # real dir orphan
                return True
        await pilot.press("j")
    return False


@pytest.mark.asyncio
async def test_adopt_key_opens_modal(tui_env_with_orphans):
    """Pressing A on an orphan node opens the AdoptScreen modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Go to target tree
        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")

        # Find and expand the myproj node
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        await pilot.press("l")  # expand to reveal orphan children
        await pilot.press("j")  # move to first orphan child

        assert await _nav_to_orphan_child(pilot, tgt_tree)

        await pilot.press("A")
        await pilot.pause()

        # AdoptScreen should be pushed
        assert len(app.screen_stack) > 1


@pytest.mark.asyncio
async def test_adopt_modal_cancel_leaves_filesystem_unchanged(tui_env_with_orphans):
    """Pressing Esc in AdoptScreen cancels without moving anything."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    orphan_path = tui_env_with_orphans / "projects" / "myproj" / ".claude" / "skills" / "orphan-x"

    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")

        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        await pilot.press("l")
        await pilot.press("j")
        assert await _nav_to_orphan_child(pilot, tgt_tree)

        await pilot.press("A")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        # Cancel
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # Orphan should still be a plain directory
        assert orphan_path.is_dir()
        assert not orphan_path.is_symlink()


@pytest.mark.asyncio
async def test_adopt_modal_confirm_moves_orphan(tui_env_with_orphans):
    """Pressing Enter in AdoptScreen moves the orphan and creates a symlink."""
    from skill_manager.tui.app import SkillManagerApp
    from textual.widgets import ListView
    app = SkillManagerApp()
    skills_source = tui_env_with_orphans / "skills"

    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")

        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        await pilot.press("l")
        await pilot.press("j")
        assert await _nav_to_orphan_child(pilot, tgt_tree)

        # Capture which orphan we're about to adopt
        node = tgt_tree.cursor_node
        orphan_name = node.data[1]
        orphan_origin = node.data[2]

        await pilot.press("A")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        # Confirm adoption (Enter selects the first source in the list)
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.1)

        # Modal should be dismissed
        assert len(app.screen_stack) == 1

        # The orphan_origin should now be a symlink
        assert orphan_origin.is_symlink(), f"{orphan_origin} should be a symlink after adoption"
        # And the destination in the source lib should exist
        dest = skills_source / orphan_name
        assert dest.is_dir(), f"{dest} should exist in source lib after adoption"


@pytest.mark.asyncio
async def test_a_key_ignored_on_non_orphan_nodes(tui_env_with_orphans):
    """Pressing A on a non-orphan node does not open any modal."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Press A while focused on source tree (no orphan selected)
        await pilot.press("A")
        await pilot.pause()

        # No modal should open
        assert len(app.screen_stack) == 1

        # Press A on a target node (not an orphan leaf)
        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")
        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target":
                break
            await pilot.press("j")

        await pilot.press("A")
        await pilot.pause()
        assert len(app.screen_stack) == 1


# ── Skill preview (TUI) ─────────────────────────────────────


async def _nav_to_first_skill(pilot, tree, max_steps=20):
    """Navigate to the first skill leaf node with a DiscoveredItem."""
    from skill_manager.models import DiscoveredItem
    for _ in range(max_steps):
        node = tree.cursor_node
        if node and isinstance(node.data, tuple):
            if node.data[0] in ("select", "select_plugin") and isinstance(node.data[1], DiscoveredItem):
                return True
        await pilot.press("j")
    return False


@pytest.mark.asyncio
async def test_preview_key_opens_modal_from_source(tui_env):
    """Pressing p on a skill in the source panel opens PreviewScreen."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        src_tree = app.query_one("#src-tree")
        # Expand local section and navigate to a skill leaf
        for _ in range(10):
            await pilot.press("j")
        await pilot.press("l")  # expand
        for _ in range(5):
            await pilot.press("j")

        assert await _nav_to_first_skill(pilot, src_tree)

        await pilot.press("p")
        await pilot.pause()

        # PreviewScreen should be pushed
        assert len(app.screen_stack) > 1

        # Close it
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_preview_key_opens_modal_from_orphan(tui_env_with_orphans):
    """Pressing p on an orphan node opens PreviewScreen."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        await pilot.press("tab")
        tgt_tree = app.query_one("#tgt-tree")

        for _ in range(15):
            node = tgt_tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "target" and node.data[1] == "myproj":
                break
            await pilot.press("j")

        await pilot.press("l")  # expand to reveal orphans
        await pilot.press("j")  # move to first orphan

        assert await _nav_to_orphan_child(pilot, tgt_tree)

        await pilot.press("p")
        await pilot.pause()

        assert len(app.screen_stack) > 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_preview_shows_skill_content(tui_env):
    """PreviewScreen displays the content of the SKILL.md file."""
    from skill_manager.tui.app import SkillManagerApp
    from skill_manager.tui.screens.preview import PreviewScreen
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        src_tree = app.query_one("#src-tree")
        for _ in range(10):
            await pilot.press("j")
        await pilot.press("l")
        for _ in range(5):
            await pilot.press("j")

        assert await _nav_to_first_skill(pilot, src_tree)

        await pilot.press("p")
        await pilot.pause()

        # The top screen should be a PreviewScreen
        assert isinstance(app.screen, PreviewScreen)
        assert app.screen._skill_path.name == "SKILL.md"
        assert app.screen._skill_path.exists()


@pytest.mark.asyncio
async def test_preview_p_ignored_on_non_skill_nodes(tui_env):
    """Pressing p on a non-skill node (e.g. section header) does nothing."""
    from skill_manager.tui.app import SkillManagerApp
    app = SkillManagerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await asyncio.sleep(0.5)

        # Cursor starts on the root section ("Local" or "Marketplaces") — not a skill
        await pilot.press("p")
        await pilot.pause()

        # No modal should be pushed
        assert len(app.screen_stack) == 1
