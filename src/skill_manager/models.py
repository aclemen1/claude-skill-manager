"""Data models for skill-manager.

Core abstraction:
    S = set of sources
    T = set of targets
    installs ⊆ S × T  (each install is a symlink from target to source)
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class ItemType(StrEnum):
    SKILL = "skill"


class SourceType(StrEnum):
    SKILL = "skill"
    MARKETPLACE = "marketplace"
    PLUGIN = "plugin"


class InstallState(StrEnum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    BROKEN = "broken"


# ── Config models (parsed from sm.toml) ──────────────────────


class SourceConfig(BaseModel):
    path: Path = Path()
    type: SourceType = SourceType.SKILL
    recursive: bool = True
    scope: str = ""
    version: str = ""
    project_path: str = ""


class TargetConfig(BaseModel):
    path: Path
    commands_path: Path | None = None


class SmConfig(BaseModel):
    plugins: bool = True
    source_paths: list[str] = Field(default_factory=list)
    target_paths: list[str] = Field(default_factory=list)
    theme: str = ""



# ── Runtime models ────────────────────────────────────────────


class DiscoveredItem(BaseModel):
    """An element s ∈ S — a skill found in a source."""

    name: str
    source_name: str
    item_type: ItemType
    path: Path
    sets: list[str] = Field(default_factory=list)
    description: str = ""
    frontmatter: dict = Field(default_factory=dict)
    plugin_scope: str = ""
    plugin_version: str = ""
    plugin_project: str = ""  # project name for project-scoped plugins
    plugin_name: str = ""  # plugin name within a marketplace (for grouping)

    @property
    def qualified_name(self) -> str:
        return f"{self.source_name}:{self.name}"

    @property
    def deploy_name(self) -> str:
        return self.name


class InstallMethod(StrEnum):
    SYMLINK = "symlink"  # managed by sm (symlink to a known source)
    PLUGIN = "plugin"  # managed by Claude Code
    ORPHAN = "orphan"  # present in target but no known source


class Install(BaseModel):
    """A pair (s, t) ∈ S × T — detected from filesystem, not from a lock file."""

    source: str = ""  # qualified name of the source item (empty for orphans)
    target: str = ""  # target name
    name: str = ""  # skill name
    symlink: Path = Path()
    origin: Path = Path()  # where the symlink points, or the local dir
    item_type: ItemType = ItemType.SKILL
    method: InstallMethod = InstallMethod.SYMLINK
    project_path: str = ""


class ConflictSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Conflict(BaseModel):
    name: str
    target: str = ""  # empty = global, otherwise per-target
    items: list[str]
    conflict_type: str
    severity: ConflictSeverity = ConflictSeverity.ERROR


class BudgetEntry(BaseModel):
    qualified_name: str
    description_chars: int = 0
    content_chars: int = 0
    estimated_tokens: int = 0
