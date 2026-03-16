"""Detect outdated plugins and perform updates."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from skill_manager.core.discovery import fetch_claude_code_data


@dataclass
class OutdatedPlugin:
    plugin_id: str  # e.g. "productivity@aclemen1-marketplace"
    plugin_name: str
    marketplace: str
    current_version: str
    latest_version: str
    scope: str
    project_path: str
    target: str  # resolved target name


def detect_outdated(target_by_path: dict[str, str] | None = None) -> list[OutdatedPlugin]:
    """Find plugins installed with multiple versions in the same scope/project.

    The version with the most recent lastUpdated is considered "latest";
    other entries for the same (plugin_id, project) are outdated.
    """
    data = fetch_claude_code_data()
    plugins = data.get("plugins", [])

    # Group by (plugin_id, project_path) — same plugin in same project
    Key = tuple[str, str]  # (plugin_id, project_path_or_"user")
    by_key: dict[Key, list[dict]] = defaultdict(list)

    for entry in plugins:
        pid = entry.get("id", "")
        pp = entry.get("projectPath", "")
        key = (pid, pp if entry.get("scope") == "project" else "")
        by_key[key].append(entry)

    outdated: list[OutdatedPlugin] = []

    for (pid, pp), entries in by_key.items():
        if len(entries) <= 1:
            continue

        # Sort by lastUpdated descending → first is latest
        sorted_entries = sorted(
            entries,
            key=lambda e: e.get("lastUpdated", ""),
            reverse=True,
        )
        latest = sorted_entries[0]
        latest_version = latest.get("version", "?")

        parts = pid.split("@")
        plugin_name = parts[0]
        marketplace = parts[1] if len(parts) > 1 else "unknown"

        # Use the project path directly for clarity
        if latest.get("scope") == "user" or not pp:
            target = "~/.claude (user)"
        else:
            home = str(Path.home())
            target = f"~{pp[len(home):]}" if pp.startswith(home) else pp

        for old in sorted_entries[1:]:
            old_version = old.get("version", "?")
            if old_version == latest_version:
                continue  # same version, just different entry
            outdated.append(OutdatedPlugin(
                plugin_id=pid,
                plugin_name=plugin_name,
                marketplace=marketplace,
                current_version=old_version,
                latest_version=latest_version,
                scope=old.get("scope", "user"),
                project_path=old.get("projectPath", ""),
                target=target,
            ))

    return outdated


def update_plugin(plugin_id: str, scope: str = "user", project_path: str = "") -> tuple[bool, str]:
    """Run claude plugin update for a specific plugin."""
    cmd = ["claude", "plugin", "update", plugin_id, "--scope", scope]
    try:
        kwargs: dict = dict(capture_output=True, text=True, timeout=30)
        if scope == "project" and project_path:
            kwargs["cwd"] = project_path
        result = subprocess.run(cmd, **kwargs)
        if result.returncode == 0:
            return True, f"Updated {plugin_id} ({scope})"
        return False, f"Failed: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return False, f"Error: {e}"
