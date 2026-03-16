"""Diagnostics — per-target name collisions and install guards.

A collision is only meaningful when two items with the same deploy_name
are active in the same target. Items in different targets never conflict.
"""

from __future__ import annotations

from collections import defaultdict

from skill_manager.core.inventory import is_plugin_source
from skill_manager.models import (
    Conflict, ConflictSeverity, DiscoveredItem, Install, InstallMethod,
)


def _extract_marketplace(source_name: str) -> str | None:
    if source_name.startswith("mp:"):
        return source_name[3:]
    if source_name.startswith("plugin:") and "@" in source_name:
        after = source_name.split("@", 1)[1]
        return after.split("#")[0].split(":")[0]
    return None


def _classify_collision(colliding: list[DiscoveredItem]) -> tuple[str, ConflictSeverity]:
    """Classify a group of colliding items within a single target."""
    user_items = [i for i in colliding if not is_plugin_source(i.source_name)]
    plugin_items = [i for i in colliding if is_plugin_source(i.source_name)]

    if user_items and len(user_items) > 1:
        return "user-user", ConflictSeverity.ERROR

    if user_items and plugin_items:
        return "user-plugin", ConflictSeverity.WARNING

    marketplaces = {_extract_marketplace(i.source_name) for i in colliding}
    marketplaces.discard(None)
    if len(marketplaces) > 1:
        return "cross-marketplace", ConflictSeverity.WARNING

    return "mp-cache", ConflictSeverity.INFO


def detect_diagnostics(
    items: list[DiscoveredItem],
    installs: list[Install] | None = None,
) -> list[Conflict]:
    """Detect per-target diagnostics: name collisions + orphan-plugin overlaps.

    If installs are provided, diagnostics are scoped per target.
    Without installs, falls back to global detection.
    """
    if not installs:
        return _detect_global(items)

    items_by_qname: dict[str, DiscoveredItem] = {i.qualified_name: i for i in items}
    items_by_name: dict[str, list[DiscoveredItem]] = defaultdict(list)
    for i in items:
        items_by_name[i.name].append(i)

    by_target: dict[str, list[Install]] = defaultdict(list)
    for inst in installs:
        by_target[inst.target].append(inst)

    diagnostics: list[Conflict] = []
    seen: set[tuple[str, str]] = set()

    for target, target_installs in by_target.items():
        # Collect known items by deploy_name
        by_name: dict[str, list[DiscoveredItem]] = defaultdict(list)
        seen_qnames: set[str] = set()

        # Collect orphan names and plugin names for case 6
        orphan_names: set[str] = set()
        plugin_names: set[str] = set()

        for inst in target_installs:
            if inst.method == InstallMethod.ORPHAN:
                orphan_names.add(inst.name)
                continue
            if inst.method == InstallMethod.PLUGIN:
                skill_name = inst.source.rsplit(":", 1)[-1] if inst.source else inst.name
                plugin_names.add(skill_name)

            # Find the item for collision detection
            item = items_by_qname.get(inst.source)
            if not item:
                skill_name = inst.source.rsplit(":", 1)[-1] if inst.source else inst.name
                candidates = items_by_name.get(skill_name, [])
                item = candidates[0] if candidates else None
            if item and item.qualified_name not in seen_qnames:
                seen_qnames.add(item.qualified_name)
                by_name[item.deploy_name].append(item)

        # Item-vs-item collisions
        for name, colliding in by_name.items():
            if len(colliding) <= 1:
                continue
            key = (target, name)
            if key in seen:
                continue
            seen.add(key)

            conflict_type, severity = _classify_collision(colliding)
            diagnostics.append(Conflict(
                name=name,
                target=target,
                items=[i.qualified_name for i in colliding],
                conflict_type=conflict_type,
                severity=severity,
            ))

        # Case 6: orphan-plugin overlap
        overlap = orphan_names & plugin_names
        for name in sorted(overlap):
            key = (target, f"orphan:{name}")
            if key in seen:
                continue
            seen.add(key)
            # Find the plugin install source for the message
            plugin_sources = [
                inst.source for inst in target_installs
                if inst.method == InstallMethod.PLUGIN
                and (inst.source.rsplit(":", 1)[-1] if inst.source else inst.name) == name
            ]
            diagnostics.append(Conflict(
                name=name,
                target=target,
                items=[f"orphan:{name}"] + plugin_sources,
                conflict_type="orphan-plugin",
                severity=ConflictSeverity.WARNING,
            ))

    return diagnostics


# Keep old name as alias for backwards compatibility
detect_conflicts = detect_diagnostics


def _detect_global(items: list[DiscoveredItem]) -> list[Conflict]:
    """Fallback: global detection without install context."""
    diagnostics: list[Conflict] = []
    by_name: dict[str, list[DiscoveredItem]] = defaultdict(list)
    for item in items:
        by_name[item.deploy_name].append(item)

    for name, colliding in by_name.items():
        if len(colliding) <= 1:
            continue
        conflict_type, severity = _classify_collision(colliding)
        diagnostics.append(Conflict(
            name=name,
            items=[i.qualified_name for i in colliding],
            conflict_type=conflict_type,
            severity=severity,
        ))

    return diagnostics


# ── Install guards ───────────────────────────────────────────


def check_install_guards(
    pending_installs: list[tuple[str, str, str]],  # [(source_qname, deploy_name, target)]
    installs: list[Install],
    items: list[DiscoveredItem],
) -> list[tuple[str, str]]:
    """Check pending installs for conflicts. Returns list of (error_msg, severity).

    severity: "error" = blocked, "warning" = proceed with caution.
    """
    errors: list[tuple[str, str]] = []

    # Build current state per target: deploy_name → (method, source)
    target_state: dict[str, dict[str, list[tuple[InstallMethod, str]]]] = defaultdict(lambda: defaultdict(list))
    for inst in installs:
        name = inst.source.rsplit(":", 1)[-1] if inst.source else inst.name
        target_state[inst.target][name].append((inst.method, inst.source))

    # Build pending state: check within pending installs themselves
    pending_by_target: dict[str, list[tuple[str, str]]] = defaultdict(list)  # target → [(qname, deploy_name)]
    for qname, deploy_name, target in pending_installs:
        pending_by_target[target].append((qname, deploy_name))

    # Check pending-vs-pending (case 1/8: two local symlinks with same name)
    for target, pending_list in pending_by_target.items():
        names_seen: dict[str, str] = {}  # deploy_name → first qname
        for qname, deploy_name in pending_list:
            if deploy_name in names_seen:
                first = names_seen[deploy_name]
                errors.append((
                    f"Cannot install '{deploy_name}' into {target}: "
                    f"both '{first}' and '{qname}' have the same name",
                    "error",
                ))
            else:
                names_seen[deploy_name] = qname

    # Check pending-vs-existing
    items_by_qname = {i.qualified_name: i for i in items}
    for qname, deploy_name, target in pending_installs:
        existing = target_state.get(target, {}).get(deploy_name, [])
        if not existing:
            continue

        item = items_by_qname.get(qname)
        is_local = item and not is_plugin_source(item.source_name) if item else True

        for method, source in existing:
            if method == InstallMethod.ORPHAN:
                # Case 5/9: blocked by filesystem (exists and not a symlink)
                errors.append((
                    f"Cannot install '{deploy_name}' into {target}: "
                    f"an unmanaged copy already exists (remove it first)",
                    "error",
                ))
            elif method == InstallMethod.SYMLINK and source != qname:
                # Case 1/8: different local source already installed
                errors.append((
                    f"Cannot install '{deploy_name}' into {target}: "
                    f"already installed from '{source}'",
                    "error",
                ))
            elif method == InstallMethod.PLUGIN and is_local:
                # Case 2: local skill shadows a CC plugin
                errors.append((
                    f"'{deploy_name}' in {target}: will coexist with plugin '{source}' "
                    f"(CC plugin in a different path)",
                    "warning",
                ))

    return errors
