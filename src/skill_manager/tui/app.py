"""Skill Manager TUI — S × T → installs

Layout:
  ┌─ Sources (S) ────────┬─ Targets (T) ────────┐
  │                      │                       │
  │                      │                       │
  ├─ Pending Changes ────┴───────────────────────┤
  │  + backtest → user                           │
  │  - trend-scout × quant                       │
  └──────────────────────────────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, RichLog, DataTable, Tree, ListView, ListItem
from textual.screen import ModalScreen

class _PendingTable(DataTable):
    """DataTable with contextual bindings shown in the footer."""
    BINDINGS = [
        Binding("d", "app.delete_pending", "Delete", show=True),
        Binding("escape", "app.cancel_changes", "Cancel", show=True),
    ]


from skill_manager.core.config import load_config
from skill_manager.core.budget import get_token_estimate
from skill_manager.core.deployer import (
    get_installs_for_item, get_installs_for_target,
    install_symlink, uninstall_symlink,
    cc_plugin_install, cc_plugin_uninstall,
)
from skill_manager.core.discovery import discover_all, resolve_all_sources, resolve_all_targets, invalidate_cache
from skill_manager.models import DiscoveredItem, Install, TargetConfig
from skill_manager.tui.widgets.source_panel import SourcePanel
from skill_manager.tui.widgets.target_panel import TargetPanel


@dataclass
class PendingChange:
    source_qname: str
    source_name: str
    target: str
    action: str  # "install" or "uninstall"


class PendingChanges:
    def __init__(self) -> None:
        self._changes: dict[tuple[str, str], PendingChange] = {}

    def toggle(self, source_qname: str, source_name: str, target: str, currently_installed: bool) -> None:
        key = (source_qname, target)
        if key in self._changes:
            del self._changes[key]
        else:
            self._changes[key] = PendingChange(
                source_qname=source_qname,
                source_name=source_name,
                target=target,
                action="uninstall" if currently_installed else "install",
            )

    def clear(self) -> None:
        self._changes.clear()

    @property
    def installs(self) -> list[PendingChange]:
        return [c for c in self._changes.values() if c.action == "install"]

    @property
    def uninstalls(self) -> list[PendingChange]:
        return [c for c in self._changes.values() if c.action == "uninstall"]

    @property
    def count(self) -> int:
        return len(self._changes)

    def is_pending(self, source_qname: str, target: str) -> str | None:
        c = self._changes.get((source_qname, target))
        return c.action if c else None

    def remove(self, source_qname: str, target: str) -> None:
        self._changes.pop((source_qname, target), None)

    def __bool__(self) -> bool:
        return bool(self._changes)


class SkillManagerApp(App):
    ENABLE_COMMAND_PALETTE = True
    TITLE = "Skill Manager"


    def on_key(self, event) -> None:
        """Handle Tab/Shift+Tab to cycle focus between the three panels (main screen only)."""
        if len(self.screen_stack) > 1:
            return  # don't intercept Tab in modals
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self._cycle_focus(forward=True)
        elif event.key == "shift+tab":
            event.prevent_default()
            event.stop()
            self._cycle_focus(forward=False)

    def _cycle_focus(self, forward: bool = True) -> None:
        focus_order = [
            self.query_one("#src-tree", Tree),
            self.query_one("#tgt-tree", Tree),
            self.query_one("#pending-table", DataTable),
        ]
        current = self.focused
        # Find current widget in focus order (match by identity or by ancestry)
        idx = -1
        for i, w in enumerate(focus_order):
            if w is current:
                idx = i
                break
        # If not found directly, check if focused widget is a child of one
        if idx == -1 and current is not None:
            widget = current
            while widget is not None:
                for i, w in enumerate(focus_order):
                    if w is widget:
                        idx = i
                        break
                if idx >= 0:
                    break
                widget = widget.parent

        if idx == -1:
            idx = 0

        delta = 1 if forward else -1
        next_idx = (idx + delta) % len(focus_order)
        focus_order[next_idx].focus()
    CSS = """
    HeaderIcon { visibility: hidden; width: 0; }
    #main-layout { layout: horizontal; height: 1fr; }
    #source-panel { border-right: solid $primary-darken-2; }
    #target-panel { }
    .panel-title {
        dock: top; height: 1;
        background: $primary-darken-1; color: $text;
        text-align: center; text-style: bold; padding: 0 1;
    }
    #pending-panel {
        height: 8;
        border-top: solid $primary-darken-2;
    }
    #pending-title {
        dock: top; height: 1;
        background: $surface-darken-1; color: $text;
        padding: 0 2;
    }
    #pending-log {
        height: 1fr;
        padding: 0 2;
    }
    Tree { height: 1fr; padding: 0 1; }
    """

    _source_pct = 50   # source panel width percentage
    _pending_h = 8     # pending panel height in rows

    BINDINGS = [
        Binding("left_square_bracket", "resize_left", "Shrink", show=False),
        Binding("right_square_bracket", "resize_right", "Grow", show=False),
        Binding("minus", "resize_pending_down", "Shrink pending", show=False),
        Binding("-", "resize_pending_down", "Shrink pending", show=False),
        Binding("equal", "resize_pending_up", "Grow pending", show=False),
        Binding("=", "resize_pending_up", "Grow pending", show=False),
        Binding("plus", "resize_pending_up", "Grow pending", show=False),
        Binding("d", "delete_pending", "Delete", show=False),
        Binding("escape", "cancel_changes", "Cancel", show=False),
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "apply", "Apply"),
        Binding("s", "show_settings", "Settings"),
        Binding("D", "show_diagnostics", "Diagnostics"),
        Binding("question_mark", "show_help", "Help", key_display="?"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            yield SourcePanel(id="source-panel")
            yield TargetPanel(id="target-panel")
        with Vertical(id="pending-panel"):
            yield Label("[bold]Pending Changes[/bold]  [dim]toggle above, [bold]a[/bold]pply, [bold]d[/bold]elete, [bold]Esc[/bold] cancel all[/dim]", id="pending-title")
            dt = _PendingTable(id="pending-table")
            dt.cursor_type = "row"
            dt.add_columns("", "Action", "Source", "Target", "Tokens")
            yield dt
        yield Footer()

    def on_mount(self) -> None:
        self.pending = PendingChanges()
        # Restore saved theme
        config = load_config()
        if config.theme and config.theme in self.available_themes:
            self.theme = config.theme
        self.query_one("#src-tree").focus()
        self._apply_resize()
        self.notify("Loading sources...", timeout=10)
        self.run_worker(self._async_refresh, exclusive=True)

    _theme_before_preview: str = ""
    _previewing_theme: bool = False

    def on_command_palette_opened(self, event) -> None:
        """Remember theme before command palette opens."""
        self._theme_before_preview = self.theme
        self._previewing_theme = True

    def on_command_palette_option_highlighted(self, event) -> None:
        """Preview theme as user navigates the theme list."""
        if not self._previewing_theme:
            return
        try:
            option = event.highlighted_event.option
            # Try multiple ways to extract the theme name
            theme_name = ""
            if hasattr(option, "hit"):
                hit = option.hit
                # hit could be DiscoveryHit or Hit — try text, then display
                theme_name = str(hit.text) if hit.text else str(hit.display) if hasattr(hit, "display") else ""
            # Clean up any Rich markup
            if theme_name:
                import re
                theme_name = re.sub(r"\[.*?\]", "", theme_name).strip()
            if theme_name and theme_name in self.available_themes:
                self.theme = theme_name
        except (AttributeError, TypeError):
            pass

    def on_command_palette_closed(self, event) -> None:
        """Restore or confirm theme when palette closes."""
        if not self._previewing_theme:
            return
        self._previewing_theme = False
        if not event.option_selected:
            # Cancelled — restore original theme
            self.theme = self._theme_before_preview

    def watch_theme(self, old_value: str, new_value: str) -> None:
        """Save theme preference when confirmed (not during preview)."""
        if not new_value or not hasattr(self, "pending"):
            return
        if self._previewing_theme:
            return  # don't save during preview
        config = load_config()
        if config.theme != new_value:
            config.theme = new_value
            from skill_manager.core.config import save_config
            save_config(config)

    def _apply_resize(self) -> None:
        self.query_one("#source-panel").styles.width = f"{self._source_pct}%"
        self.query_one("#target-panel").styles.width = f"{100 - self._source_pct}%"
        self.query_one("#pending-panel").styles.height = self._pending_h

    def _focused_panel(self) -> str:
        """Return which panel has focus: 'source', 'target', or 'pending'."""
        focused = self.focused
        if focused is None:
            return "source"
        widget = focused
        while widget is not None:
            wid = widget.id or ""
            if "source" in wid or wid == "src-tree":
                return "source"
            if "target" in wid or wid == "tgt-tree":
                return "target"
            if "pending" in wid:
                return "pending"
            widget = widget.parent
        return "source"

    def action_resize_left(self) -> None:
        panel = self._focused_panel()
        if panel == "source":
            self._source_pct = max(20, self._source_pct - 5)
        elif panel == "target":
            self._source_pct = min(80, self._source_pct + 5)
        elif panel == "pending":
            self._pending_h = max(3, self._pending_h - 2)
        self._apply_resize()

    def action_resize_right(self) -> None:
        panel = self._focused_panel()
        if panel == "source":
            self._source_pct = min(80, self._source_pct + 5)
        elif panel == "target":
            self._source_pct = max(20, self._source_pct - 5)
        elif panel == "pending":
            self._pending_h = min(30, self._pending_h + 2)
        self._apply_resize()

    def action_resize_pending_down(self) -> None:
        self._pending_h = max(3, self._pending_h - 2)
        self._apply_resize()

    def action_resize_pending_up(self) -> None:
        self._pending_h = min(30, self._pending_h + 2)
        self._apply_resize()

    async def _async_refresh(self) -> None:
        """Run discovery in a worker thread to avoid blocking the UI."""
        import asyncio
        invalidate_cache()
        self.config = load_config()
        
        self.items = await asyncio.to_thread(discover_all, self.config)
        self.all_sources = resolve_all_sources(self.config)
        self.all_targets = resolve_all_targets(self.config)

        self.query_one("#source-panel", SourcePanel).refresh_data(
            self.config, self.items, self.all_sources, self.all_targets)
        self.query_one("#target-panel", TargetPanel).refresh_data(
            self.all_targets, self.all_sources, self.items)
        self._update_pending_panel()
        self.clear_notifications()
        self.notify(f"{len(self.items)} skills, {len(self.all_targets)} targets", timeout=3)

    def refresh_data(self) -> None:
        """Synchronous refresh (used after apply/cancel)."""
        invalidate_cache()
        self.config = load_config()
        
        self.items = discover_all(self.config)
        self.all_sources = resolve_all_sources(self.config)
        self.all_targets = resolve_all_targets(self.config)

        self.query_one("#source-panel", SourcePanel).refresh_data(
            self.config, self.items, self.all_sources, self.all_targets)
        self.query_one("#target-panel", TargetPanel).refresh_data(
            self.all_targets, self.all_sources, self.items)
        self._update_pending_panel()

    def action_refresh(self) -> None:
        self.pending.clear()
        self.run_worker(self._async_refresh, exclusive=True)
        self.notify("Refreshing...")

    def _update_pending_panel(self) -> None:
        table = self.query_one("#pending-table", DataTable)
        title = self.query_one("#pending-title", Label)
        table.clear()

        if not self.pending:
            title.update("[bold]Pending Changes[/bold]  [dim]toggle above, [bold]a[/bold]pply, [bold]d[/bold]elete, [bold]Esc[/bold] cancel all[/dim]")
            return

        # Resolve tokens for each pending change
        items_by_qn = {i.qualified_name: i for i in self.items} if hasattr(self, 'items') else {}
        items_by_name = {i.name: i for i in self.items} if hasattr(self, 'items') else {}

        def _tok(c):
            item = items_by_qn.get(c.source_qname) or items_by_name.get(c.source_name)
            return get_token_estimate(item) if item else 0

        n_inst = len(self.pending.installs)
        n_uninst = len(self.pending.uninstalls)
        tok_add = sum(_tok(c) for c in self.pending.installs)
        tok_rm = sum(_tok(c) for c in self.pending.uninstalls)
        tok_delta = tok_add - tok_rm

        parts = []
        if n_inst:
            parts.append(f"[green]+{n_inst}[/green]")
        if n_uninst:
            parts.append(f"[red]-{n_uninst}[/red]")
        if tok_delta > 0:
            parts.append(f"[green]+{tok_delta}t[/green]")
        elif tok_delta < 0:
            parts.append(f"[red]{tok_delta}t[/red]")
        title.update(
            f"[bold]Pending Changes[/bold]  {' '.join(parts)}"
            f"  │  [bold]a[/bold]pply  [bold]d[/bold]elete  [bold]Esc[/bold] cancel all"
        )

        for c in self.pending.installs:
            method = "cc" if c.source_qname.startswith("plugin:") or c.source_qname.startswith("mp:") else "sm"
            tok = _tok(c)
            table.add_row(
                "[green]+[/green]", "install", c.source_name,
                f"{c.target} [dim]({method})[/dim]",
                f"[green]+{tok}t[/green]" if tok else "",
                key=f"{c.source_qname}|{c.target}",
            )
        for c in self.pending.uninstalls:
            method = "cc" if c.source_qname.startswith("plugin:") or c.source_qname.startswith("mp:") else "sm"
            tok = _tok(c)
            table.add_row(
                "[red]−[/red]", "uninstall", c.source_name,
                f"{c.target} [dim]({method})[/dim]",
                f"[red]-{tok}t[/red]" if tok else "",
                key=f"{c.source_qname}|{c.target}",
            )

    # action_refresh defined above with async worker

    def action_delete_pending(self) -> None:
        """Delete the selected row from pending changes."""
        table = self.query_one("#pending-table", DataTable)
        if not table.row_count:
            return
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        key_str = str(row_key.value) if row_key else None
        if not key_str or "|" not in key_str:
            return
        source_qname, target = key_str.split("|", 1)
        self.pending.remove(source_qname, target)
        # Refresh both panels preserving expand state
        self.query_one("#source-panel", SourcePanel).refresh_preserving_state(self.pending)
        self.query_one("#target-panel", TargetPanel).refresh_preserving_state(self.pending)
        self._update_pending_panel()


    def action_cancel_changes(self) -> None:
        if self.pending:
            self.pending.clear()
            self.refresh_data()
            self.notify("Changes cancelled")

    # ── Enter (select) → rebuild other panel fully ──────────

    def _get_plugin_installs(self, item: DiscoveredItem) -> list[Install]:
        """Get installs for a CC plugin (matching all skills from the same plugin ref)."""
        from skill_manager.core.deployer import all_installs, _parse_plugin_ref
        ref = _parse_plugin_ref(item.qualified_name)
        if not ref:
            # mp: item — try to find plugin ref from metadata
            if item.source_name.startswith("mp:") and item.plugin_name:
                mp = item.source_name[3:]
                ref = f"{item.plugin_name}@{mp}"
        if not ref:
            return get_installs_for_item(item, self.all_targets, self.all_sources, self.items)

        # Find all installs matching this plugin ref
        unified = all_installs(self.all_targets, self.all_sources, self.items)
        result: list[Install] = []
        seen_targets: set[str] = set()
        for inst in unified:
            if inst.source and _parse_plugin_ref(inst.source) == ref and inst.target not in seen_targets:
                result.append(inst)
                seen_targets.add(inst.target)
        return result

    def on_source_panel_item_selected(self, event: SourcePanel.ItemSelected) -> None:
        item = event.item
        src = self.query_one("#source-panel", SourcePanel)
        tgt = self.query_one("#target-panel", TargetPanel)
        tgt._selected_target = ""
        src._active_target = None

        # For CC plugins, get installs at plugin level
        from skill_manager.core.inventory import is_plugin_source
        if is_plugin_source(item.source_name) or item.source_name.startswith("mp:"):
            installs = self._get_plugin_installs(item)
        else:
            installs = get_installs_for_item(item, self.all_targets, self.all_sources, self.items)

        tgt.show_for_source(item, installs, self.pending)
        # Refresh source to show bold reverse (with pending to preserve indicators)
        self.call_after_refresh(lambda: src.refresh_preserving_state(self.pending))
        # Move focus to target panel
        tgt_tree = tgt.query_one("#tgt-tree")
        tgt_tree.focus()

    def on_target_panel_target_selected(self, event: TargetPanel.TargetSelected) -> None:
        target_name = event.target_name
        src = self.query_one("#source-panel", SourcePanel)
        tgt = self.query_one("#target-panel", TargetPanel)
        src._selected_qname = ""
        tgt._active_source = None
        installs = get_installs_for_target(target_name, self.all_targets, self.all_sources, self.items)
        src.show_for_target(installs, target_name, self.pending)
        # Refresh target to show bold reverse (with pending to preserve indicators)
        self.call_after_refresh(lambda: tgt.refresh_preserving_state(self.pending))
        # Move focus to source panel
        src_tree = src.query_one("#src-tree")
        src_tree.focus()

    # ── Space (toggle) → preserve expand state on BOTH ──────

    def on_target_panel_toggle_install(self, event: TargetPanel.ToggleInstall) -> None:
        self.pending.toggle(event.source_qname, event.source_name, event.target, event.currently_installed)
        # Toggle only updates counts — preserve expand state on BOTH panels
        self.query_one("#source-panel", SourcePanel).refresh_preserving_state(self.pending)
        self.query_one("#target-panel", TargetPanel).refresh_preserving_state(self.pending)
        self._update_pending_panel()

    def on_source_panel_toggle_install(self, event: SourcePanel.ToggleInstall) -> None:
        self.pending.toggle(event.source_qname, event.source_name, event.target, event.currently_installed)
        # Toggle only updates counts — preserve expand state on BOTH panels
        self.query_one("#source-panel", SourcePanel).refresh_preserving_state(self.pending)
        self.query_one("#target-panel", TargetPanel).refresh_preserving_state(self.pending)
        self._update_pending_panel()

    def _refresh_both_panels(self) -> None:
        """Re-render both panels to reflect pending changes in counts."""
        src_panel = self.query_one("#source-panel", SourcePanel)
        tgt_panel = self.query_one("#target-panel", TargetPanel)

        # If a source was selected → re-render targets with toggles
        if src_panel.selected_item:
            installs = get_installs_for_item(src_panel.selected_item, self.all_targets, self.all_sources, self.items)
            tgt_panel.show_for_source(src_panel.selected_item, installs, self.pending)

        # If a target was selected → re-render sources with toggles
        if src_panel._active_target:
            target_name = src_panel._active_target
            installs = get_installs_for_target(target_name, self.all_targets, self.all_sources, self.items)
            src_panel.show_for_target(installs, target_name, self.pending)

        self._update_pending_panel()

    # ── Apply ─────────────────────────────────────────────────

    def action_apply(self) -> None:
        if not self.pending:
            self.notify("No pending changes")
            return

        # Run install guards before showing confirmation
        from skill_manager.core.conflicts import check_install_guards
        from skill_manager.core.deployer import all_installs
        current_installs = all_installs(self.all_targets, self.all_sources, self.items)

        pending_installs = [
            (c.source_qname, c.source_name, c.target)
            for c in self.pending.installs
        ]
        guard_issues = check_install_guards(pending_installs, current_installs, self.items)

        errors = [msg for msg, sev in guard_issues if sev == "error"]
        warnings = [msg for msg, sev in guard_issues if sev == "warning"]

        if errors:
            self.notify(f"Blocked: {errors[0]}", severity="error")
            return

        self.push_screen(
            ApplyScreen(self.pending, warnings),
            callback=self._do_apply,
        )

    def _do_apply(self, confirmed: bool | None) -> None:
        if not confirmed:
            return

        from skill_manager.core.deployer import (
            cc_plugin_install, cc_plugin_uninstall, all_installs,
        )

        current_installs = all_installs(self.all_targets, self.all_sources, self.items)
        errors: list[str] = []

        for change in self.pending.installs:
            if change.source_qname.startswith("plugin:") or change.source_qname.startswith("mp:"):
                ok, msg = cc_plugin_install(change.source_qname, change.target, current_installs, self.items)
                if not ok:
                    errors.append(msg)
            else:
                target_cfg = self.all_targets.get(change.target)
                if not target_cfg:
                    errors.append(f"Target '{change.target}' not found")
                    continue
                matches = [i for i in self.items if i.qualified_name == change.source_qname]
                if not matches:
                    errors.append(f"Source '{change.source_qname}' not found")
                    continue
                ok, msg = install_symlink(matches[0], change.target, target_cfg)
                if not ok:
                    errors.append(msg)

        for change in self.pending.uninstalls:
            if change.source_qname.startswith("plugin:") or change.source_qname.startswith("mp:"):
                ok, msg = cc_plugin_uninstall(change.source_qname, change.target, current_installs, self.items)
                if not ok:
                    errors.append(msg)
            else:
                target_cfg = self.all_targets.get(change.target)
                if target_cfg:
                    ok, msg = uninstall_symlink(change.source_name, target_cfg)
                    if not ok:
                        errors.append(msg)
        n = self.pending.count
        self.pending.clear()

        # Save current mode to restore after refresh
        src = self.query_one("#source-panel", SourcePanel)
        tgt = self.query_one("#target-panel", TargetPanel)
        saved_target = tgt._selected_target
        saved_source = src.selected_item
        saved_active_target = src._active_target

        self.refresh_data()

        # Restore toggle mode
        if saved_active_target:
            installs = get_installs_for_target(saved_active_target, self.all_targets, self.all_sources, self.items)
            tgt._selected_target = saved_active_target
            src.show_for_target(installs, saved_active_target, self.pending)
            tgt.refresh_preserving_state(self.pending)
        elif saved_source:
            from skill_manager.core.deployer import get_installs_for_item
            src._selected_qname = saved_source.qualified_name
            src.selected_item = saved_source
            installs = get_installs_for_item(saved_source, self.all_targets, self.all_sources, self.items)
            tgt.show_for_source(saved_source, installs, self.pending)
            src.refresh_preserving_state(self.pending)

        if errors:
            msg = errors[0]
            if len(errors) > 1:
                msg += f" (+{len(errors)-1} more)"
            self.notify(msg, severity="warning", timeout=15)
        else:
            self.notify(f"Applied {n} changes")

    # ── Modals ────────────────────────────────────────────────

    def action_show_settings(self) -> None:
        from skill_manager.tui.screens.settings import SettingsScreen
        self.push_screen(SettingsScreen(), callback=self._on_settings_closed)

    def _on_settings_closed(self, saved: bool | None) -> None:
        if saved:
            self.pending.clear()
            self.notify("Settings saved, reloading...")
            self.run_worker(self._async_refresh, exclusive=True)

    def action_show_help(self) -> None:
        from skill_manager.tui.screens.help import HelpScreen
        self.push_screen(HelpScreen())

    def action_show_diagnostics(self) -> None:
        from skill_manager.tui.screens.conflicts import ConflictsScreen
        from skill_manager.core.deployer import all_installs
        installs = all_installs(self.all_targets, self.all_sources, self.items)
        self.push_screen(ConflictsScreen(self.items, installs, self.all_targets))

    # ── Orphan adoption ────────────────────────────────────────

    def on_target_panel_adopt_orphan(self, event: TargetPanel.AdoptOrphan) -> None:
        from skill_manager.core.discovery import resolve_adoption_destinations
        skill_sources = resolve_adoption_destinations(self.config.source_paths)
        if not skill_sources:
            self.notify("No skill sources configured", severity="warning")
            return
        self.push_screen(
            AdoptScreen(event.orphan_name, skill_sources),
            callback=lambda dest: self._do_adopt(event.orphan_path, dest),
        )

    def _do_adopt(self, orphan_path, dest_source_dir) -> None:
        if not dest_source_dir:
            return
        from pathlib import Path
        from skill_manager.core.deployer import adopt_orphan
        ok, msg = adopt_orphan(orphan_path, Path(dest_source_dir))
        if ok:
            self.notify(f"Adopted: {msg}")
            self.refresh_data()
        else:
            self.notify(f"Failed: {msg}", severity="error")



# ── Apply confirmation modal ──────────────────────────────────


class ApplyScreen(ModalScreen[bool | None]):
    CSS = """
    ApplyScreen { align: center middle; }
    #apply-container {
        width: 70; height: auto; max-height: 25;
        background: $surface; border: round $primary; padding: 1 2;
    }
    #apply-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #apply-log { height: auto; max-height: 15; margin-bottom: 1; }
    #apply-buttons { height: 1; text-align: center; }
    """
    BINDINGS = [
        Binding("enter", "confirm", "Apply"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, pending: PendingChanges, warnings: list[str] | None = None) -> None:
        super().__init__()
        self._pending = pending
        self._warnings = warnings or []

    def compose(self) -> ComposeResult:
        with Vertical(id="apply-container"):
            yield Label("[bold]Apply changes?[/bold]", id="apply-title")
            yield RichLog(id="apply-log", wrap=True, markup=True)
            yield Label("[bold]Enter[/bold] apply  │  [bold]Esc[/bold] cancel", id="apply-buttons")

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill_log)

    def _fill_log(self) -> None:
        log = self.query_one("#apply-log", RichLog)
        if self._warnings:
            for w in self._warnings:
                log.write(f"  [yellow]⚠ {w}[/yellow]")
            log.write("")
        for c in self._pending.installs:
            method = "[blue]claude plugin install[/blue]" if c.source_qname.startswith("plugin:") or c.source_qname.startswith("mp:") else "[green]symlink[/green]"
            log.write(f"  [green]+[/green] {c.source_name}  →  {c.target}  [dim]via {method}[/dim]")
        for c in self._pending.uninstalls:
            is_cc = c.source_qname.startswith("plugin:") or c.source_qname.startswith("mp:")
            method = "[blue]claude plugin uninstall[/blue]" if is_cc else "[red]rm symlink[/red]"
            log.write(f"  [red]-[/red] {c.source_name}  ×  {c.target}  [dim]via {method}[/dim]")
        log.write(f"\n[bold]{self._pending.count} change(s)[/bold]")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Adopt orphan modal ────────────────────────────────────────


class AdoptScreen(ModalScreen[str | None]):
    CSS = """
    AdoptScreen { align: center middle; }
    #adopt-container {
        width: 70; height: auto; max-height: 20;
        background: $surface; border: round $warning; padding: 1 2;
    }
    #adopt-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #adopt-list { height: auto; max-height: 12; margin-bottom: 1; }
    #adopt-buttons { height: 1; text-align: center; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def __init__(self, orphan_name: str, skill_sources: dict) -> None:
        super().__init__()
        self._orphan_name = orphan_name
        home = str(__import__("pathlib").Path.home())
        self._sources: list[tuple[str, str, str]] = []
        for name, cfg in sorted(skill_sources.items()):
            path_str = str(cfg.path)
            short = f"~{path_str[len(home):]}" if path_str.startswith(home) else path_str
            self._sources.append((name, short, str(cfg.path)))

    def compose(self) -> ComposeResult:
        with Vertical(id="adopt-container"):
            yield Label(f"[bold]Adopt orphan:[/bold] [dark_orange]{self._orphan_name}[/dark_orange]", id="adopt-title")
            items = [ListItem(Label(f"{short}  [dim]({name})[/dim]")) for name, short, _ in self._sources]
            yield ListView(*items, id="adopt-list")
            yield Label("[bold]Enter[/bold] adopt (select item)  │  [bold]Esc[/bold] cancel", id="adopt-buttons")

    def on_mount(self) -> None:
        self.query_one("#adopt-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a list item confirms the selection."""
        lv = self.query_one("#adopt-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._sources):
            _, _, path = self._sources[idx]
            self.dismiss(path)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#adopt-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#adopt-list", ListView).action_cursor_up()
