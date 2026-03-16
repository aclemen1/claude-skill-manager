"""Help modal screen — keybindings reference."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, RichLog


HELP_TEXT = """\
[bold]Navigation[/bold]
  j / k          Move down / up
  Enter          Expand / collapse (fold)
  l              Expand
  h              Collapse / parent
  Space          Select (switch to toggle mode)
  Tab            Next panel
  Shift+Tab      Previous panel

[bold]Install / Uninstall[/bold]
  x              Toggle install/uninstall

[bold]Pending Changes[/bold]
  a              Apply all pending changes
  d              Delete selected pending change
  Esc            Cancel all pending changes
  Click row      Show source + target in context

[bold]Resize Panels[/bold]
  [ / ]          Shrink / grow focused panel width
  - / = (+)      Shrink / grow Pending panel height

[bold]Modals[/bold]
  s              Settings
  Ctrl+S         Save (in Settings modal)
  D              Diagnostics (conflicts, updates)
  ?              This help

[bold]Other[/bold]
  r              Refresh (rescan sources)
  q              Quit
"""


class HelpScreen(ModalScreen):
    CSS = """
    HelpScreen { align: center middle; }
    #help-container {
        width: 60; height: auto; max-height: 35;
        background: $surface; border: round $primary; padding: 1 2;
    }
    #help-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #help-log { height: auto; max-height: 28; }
    #help-footer { text-align: center; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-container"):
            yield Label("[bold]Keybindings[/bold]", id="help-title")
            yield RichLog(id="help-log", wrap=True, markup=True, auto_scroll=False)
            yield Label("[dim]Press Esc or ? to close[/dim]", id="help-footer")

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill_log)

    def _fill_log(self) -> None:
        log = self.query_one("#help-log", RichLog)
        for line in HELP_TEXT.strip().split("\n"):
            log.write(line)
