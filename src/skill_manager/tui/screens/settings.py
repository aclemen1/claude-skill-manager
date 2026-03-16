"""Settings modal screen."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Input, DataTable

from skill_manager.core.config import load_config, save_config, DEFAULT_CONFIG_PATH
from skill_manager.models import SmConfig

_HOME = str(Path.home())


def _short(p: str) -> str:
    return f"~{p[len(_HOME):]}" if p.startswith(_HOME) else p


def _expand(s: str) -> str:
    return s.replace("~", _HOME) if s.startswith("~") else s


class SettingsScreen(ModalScreen[bool | None]):
    CSS = """
    SettingsScreen { align: center middle; }
    #settings-container {
        width: 75; height: auto; max-height: 35;
        background: $surface; border: round $primary; padding: 1 2;
    }
    #settings-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #settings-table { height: auto; max-height: 20; }
    #path-input { margin-top: 1; display: none; }
    #path-input.visible { display: block; }
    #settings-footer { margin-top: 1; text-align: center; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel_or_close_input", "Cancel/Close", priority=True),
        Binding("enter", "edit_row", "Edit", priority=True, key_display="⏎"),
        Binding("a", "add_row", "Add"),
        Binding("d", "delete_row", "Delete"),
        Binding("E", "open_editor", "$EDITOR"),
        Binding("space", "toggle_row", "Toggle", show=False),
        Binding("x", "toggle_row", "Toggle", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._config = load_config()
        self._plugins = self._config.plugins
        self._source_paths = list(self._config.source_paths)
        self._target_paths = list(self._config.target_paths)
        self._dirty = False
        self._add_section: str | None = None
        self._edit_key: str | None = None  # key of row being edited (None = add mode)

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            yield Label("[bold]Settings[/bold]", id="settings-title")
            dt = DataTable(id="settings-table")
            dt.cursor_type = "row"
            dt.add_columns("", "Setting", "Value")
            yield dt
            yield Input(id="path-input")
            yield Label(
                "[bold]Ctrl+S[/bold] save  [bold]⏎[/bold] edit  [bold]a[/bold]dd  [bold]d[/bold]elete  "
                "[bold]E[/bold] $EDITOR  [bold]space[/bold] toggle  [bold]Esc[/bold] cancel",
                id="settings-footer",
            )

    def on_mount(self) -> None:
        self._rebuild_table()

    def _rebuild_table(self, focus_key: str | None = None) -> None:
        dt = self.query_one("#settings-table", DataTable)
        dt.clear()

        chk = "[green] ● [/green]" if self._plugins else "[dim] ○ [/dim]"
        dt.add_row(chk, "[bold]Plugins[/bold]", "Claude Code marketplaces", key="plugins")

        dt.add_row("", "[bold]Source paths[/bold]", "[dim]glob patterns for SKILL.md[/dim]", key="_src_header")
        for i, p in enumerate(self._source_paths):
            dt.add_row("  ·", "  source", _short(p), key=f"src:{i}")

        dt.add_row("", "[bold]Target paths[/bold]", "[dim]glob patterns for .claude/ dirs[/dim]", key="_tgt_header")
        for i, p in enumerate(self._target_paths):
            dt.add_row("  ·", "  target", _short(p), key=f"tgt:{i}")

        dt.add_row("", "[dim]Config file[/dim]", f"[dim]{_short(str(DEFAULT_CONFIG_PATH))}[/dim]", key="_file")

        if focus_key:
            for row_idx in range(dt.row_count):
                rk = dt.coordinate_to_cell_key((row_idx, 0)).row_key
                if str(rk.value) == focus_key:
                    dt.move_cursor(row=row_idx)
                    break

    def _get_selected_key(self) -> str | None:
        dt = self.query_one("#settings-table", DataTable)
        if not dt.row_count:
            return None
        row_key = dt.coordinate_to_cell_key(dt.cursor_coordinate).row_key
        return str(row_key.value) if row_key else None

    def _current_section(self) -> str | None:
        key = self._get_selected_key()
        if not key:
            return None
        if key.startswith("src:") or key == "_src_header":
            return "source"
        if key.startswith("tgt:") or key == "_tgt_header":
            return "target"
        return None

    def action_toggle_row(self) -> None:
        key = self._get_selected_key()
        if key == "plugins":
            self._plugins = not self._plugins
            self._dirty = True
            self._rebuild_table("plugins")

    def action_delete_row(self) -> None:
        key = self._get_selected_key()
        if not key:
            return
        dt = self.query_one("#settings-table", DataTable)
        row = dt.cursor_coordinate.row
        deleted = False
        if key.startswith("src:"):
            idx = int(key.split(":")[1])
            if 0 <= idx < len(self._source_paths):
                self._source_paths.pop(idx)
                deleted = True
        elif key.startswith("tgt:"):
            idx = int(key.split(":")[1])
            if 0 <= idx < len(self._target_paths):
                self._target_paths.pop(idx)
                deleted = True
        if deleted:
            self._dirty = True
            self._rebuild_table()
            dt.move_cursor(row=min(row, dt.row_count - 1))

    def _get_current_value(self, key: str) -> str | None:
        """Get the current path value for a row key."""
        if key.startswith("src:"):
            idx = int(key.split(":")[1])
            return self._source_paths[idx] if 0 <= idx < len(self._source_paths) else None
        elif key.startswith("tgt:"):
            idx = int(key.split(":")[1])
            return self._target_paths[idx] if 0 <= idx < len(self._target_paths) else None
        return None

    def action_edit_row(self) -> None:
        """Edit the selected path inline."""
        # If input is open, submit it instead
        inp = self.query_one("#path-input", Input)
        if inp.has_class("visible"):
            self.on_input_submitted(Input.Submitted(inp, inp.value))
            return
        key = self._get_selected_key()
        if not key:
            return
        # Toggle plugins on Enter
        if key == "plugins":
            self.action_toggle_row()
            return
        value = self._get_current_value(key)
        if value is None:
            return
        section = self._current_section()
        if not section:
            return
        self._add_section = section
        self._edit_key = key
        inp = self.query_one("#path-input", Input)
        inp.placeholder = f"Edit {section} path, then Enter"
        inp.value = _short(value)
        inp.add_class("visible")
        inp.focus()

    def action_add_row(self) -> None:
        section = self._current_section()
        if not section:
            return
        self._add_section = section
        inp = self.query_one("#path-input", Input)
        hints = {
            "source": "source glob (e.g. ~/code/*, ~/lib/**)",
            "target": "target glob (e.g. ~, ~/code/*)",
        }
        inp.placeholder = f"Enter {hints[section]}, then Enter"
        inp.value = ""
        inp.add_class("visible")
        inp.focus()

    def _close_input(self) -> None:
        inp = self.query_one("#path-input", Input)
        inp.remove_class("visible")
        inp.value = ""
        self._add_section = None
        self._edit_key = None
        self.query_one("#settings-table", DataTable).focus()

    def action_cancel_or_close_input(self) -> None:
        inp = self.query_one("#path-input", Input)
        if inp.has_class("visible"):
            self._close_input()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            self._close_input()
            return

        edit_key = self._edit_key
        section = self._add_section

        focus = None
        if edit_key:
            # Edit mode: replace the value at the existing index
            focus = edit_key
            if edit_key.startswith("src:"):
                idx = int(edit_key.split(":")[1])
                if 0 <= idx < len(self._source_paths):
                    self._source_paths[idx] = value
                    self._dirty = True
            elif edit_key.startswith("tgt:"):
                idx = int(edit_key.split(":")[1])
                if 0 <= idx < len(self._target_paths):
                    self._target_paths[idx] = value
                    self._dirty = True
        else:
            # Add mode
            if section == "source" and value not in self._source_paths:
                self._source_paths.append(value)
                self._dirty = True
                focus = f"src:{len(self._source_paths) - 1}"
            elif section == "target" and value not in self._target_paths:
                self._target_paths.append(value)
                self._dirty = True
                focus = f"tgt:{len(self._target_paths) - 1}"

        self._rebuild_table(focus)
        self._close_input()

    def action_open_editor(self) -> None:
        editor = os.environ.get("EDITOR", "vi")
        with self.app.suspend():
            subprocess.run([editor, str(DEFAULT_CONFIG_PATH)])
        self._config = load_config()
        self._plugins = self._config.plugins
        self._source_paths = list(self._config.source_paths)
        self._target_paths = list(self._config.target_paths)
        self._rebuild_table()
        self._dirty = False

    def action_save(self) -> None:
        config = SmConfig(
            plugins=self._plugins,
            source_paths=self._source_paths,
            target_paths=self._target_paths,
        )
        save_config(config)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)
