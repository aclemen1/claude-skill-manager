"""Diagnostics modal screen — conflicts, stale cache, updates."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, TabbedContent, TabPane

from skill_manager.core.conflicts import detect_diagnostics
from skill_manager.core.updates import detect_outdated
from skill_manager.models import ConflictSeverity, DiscoveredItem, Install, TargetConfig


class ConflictsScreen(ModalScreen):
    """Modal showing per-target diagnostics and stale plugin cache."""

    CSS = """
    ConflictsScreen { align: center middle; }
    #conflicts-container {
        width: 85%;
        height: 85%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }
    #conflicts-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #conflicts-footer { margin-top: 1; text-align: center; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(
        self,
        items: list[DiscoveredItem],
        installs: list[Install] | None = None,
        all_targets: dict[str, TargetConfig] | None = None,
    ) -> None:
        super().__init__()
        self._items = items
        self._installs = installs
        self._all_targets = all_targets

    def compose(self) -> ComposeResult:
        with Vertical(id="conflicts-container"):
            yield Label("[bold]Diagnostics[/bold]", id="conflicts-title")
            with TabbedContent():
                with TabPane("Conflicts", id="tab-conflicts"):
                    ct = DataTable(id="conflicts-table")
                    ct.cursor_type = "row"
                    ct.add_columns("Sev", "Target", "Name", "Type", "Items")
                    yield ct
                with TabPane("Stale cache", id="tab-updates"):
                    ut = DataTable(id="updates-table")
                    ut.cursor_type = "row"
                    ut.add_columns("", "Plugin", "Target", "Stale version", "Active version", "Scope")
                    yield ut
            yield Label(
                "[bold]Esc[/bold] close",
                id="conflicts-footer",
            )

    def on_mount(self) -> None:
        self._fill_conflicts()
        self._fill_updates()

    def _fill_conflicts(self) -> None:
        table = self.query_one("#conflicts-table", DataTable)
        found = detect_diagnostics(self._items, self._installs)
        visible = [c for c in found if c.severity != ConflictSeverity.INFO]

        if not visible:
            table.add_row("", "", "", "", "No actionable conflicts")
            return

        sev_icon = {
            ConflictSeverity.ERROR: "[red]!![/red]",
            ConflictSeverity.WARNING: "[yellow]![/yellow]",
            ConflictSeverity.INFO: "[dim]i[/dim]",
        }

        for c in sorted(visible, key=lambda x: (x.severity, x.target, x.name)):
            table.add_row(
                sev_icon[c.severity],
                c.target or "(global)",
                c.name,
                c.conflict_type,
                " vs ".join(c.items),
            )

    def _build_target_by_path(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not self._all_targets:
            return result
        for tname, tcfg in self._all_targets.items():
            if tcfg and tcfg.path:
                if tcfg.path.name == "skills" and tcfg.path.parent.name == ".claude":
                    project_dir = tcfg.path.parent.parent
                elif tcfg.path.name == "skills":
                    project_dir = tcfg.path.parent
                else:
                    project_dir = tcfg.path.parent
                try:
                    result[str(project_dir.resolve())] = tname
                except OSError:
                    pass
        return result

    def _fill_updates(self) -> None:
        table = self.query_one("#updates-table", DataTable)
        target_by_path = self._build_target_by_path()
        self._outdated = detect_outdated(target_by_path)

        if not self._outdated:
            table.add_row("", "", "", "", "No stale cache", "")
            return

        for o in self._outdated:
            short_stale = o.current_version[:12]
            short_active = o.latest_version[:12]
            table.add_row(
                "[yellow]⚠[/yellow]",
                f"{o.plugin_name}@{o.marketplace}",
                o.target,
                f"[dim]{short_stale}[/dim]",
                f"[green]{short_active}[/green]",
                o.scope,
            )
