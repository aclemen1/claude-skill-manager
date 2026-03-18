"""Preview modal screen — shows SKILL.md content with optional editor launch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Markdown

_HOME = str(Path.home())


def _short(p: Path) -> str:
    s = str(p)
    return f"~{s[len(_HOME):]}" if s.startswith(_HOME) else s


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split YAML frontmatter from markdown body.

    Returns (yaml_source, body) where yaml_source is the raw YAML text
    (without --- delimiters), or empty string if no frontmatter.
    """
    if not text.startswith("---"):
        return "", text
    end = text.find("---", 3)
    if end == -1:
        return "", text
    yaml_src = text[3:end].strip()
    body = text[end + 3:].strip()
    return yaml_src, body


class PreviewScreen(ModalScreen):
    CSS = """
    PreviewScreen { align: center middle; }
    #preview-container {
        width: 90%; height: 90%;
        background: $surface; border: round $primary; padding: 1 2;
    }
    #preview-title { text-align: center; text-style: bold; margin-bottom: 1; }
    #preview-scroll { height: 1fr; }
    #preview-md { margin: 0; }
    #preview-footer { text-align: center; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("e", "edit", "Edit"),
    ]

    def __init__(self, skill_path: Path, editable: bool = False) -> None:
        super().__init__()
        self._skill_path = skill_path
        self._editable = editable

    def compose(self) -> ComposeResult:
        name = self._skill_path.parent.name
        yaml_src, body = self._read_content()
        # Render frontmatter as a YAML code fence inside the Markdown
        if yaml_src:
            md_text = f"```yaml\n{yaml_src}\n```\n\n{body}"
        else:
            md_text = body
        with Vertical(id="preview-container"):
            yield Label(
                f"[bold]{name}[/bold]  [dim]{_short(self._skill_path)}[/dim]",
                id="preview-title",
            )
            with VerticalScroll(id="preview-scroll"):
                yield Markdown(md_text, id="preview-md")
            hint = "[bold]e[/bold] edit  │  " if self._editable else ""
            yield Label(f"[dim]{hint}[bold]Esc[/bold] close[/dim]", id="preview-footer")

    def _read_content(self) -> tuple[str, str]:
        try:
            text = self._skill_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "", f"*File not found: {self._skill_path}*"
        return _split_frontmatter(text)

    def action_edit(self) -> None:
        if not self._editable:
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
        with self.app.suspend():
            subprocess.call([editor, str(self._skill_path)])
        # Refresh content after edit
        yaml_src, body = self._read_content()
        md_text = f"```yaml\n{yaml_src}\n```\n\n{body}" if yaml_src else body
        self.query_one("#preview-md", Markdown).update(md_text)
