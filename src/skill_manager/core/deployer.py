"""Install/uninstall via symlinks + filesystem-based install detection.

No lock file — the filesystem IS the source of truth.
"""

from __future__ import annotations

from pathlib import Path

from skill_manager.models import (
    DiscoveredItem,
    Install,
    InstallMethod,
    InstallState,
    ItemType,
    TargetConfig,
)


# ── Filesystem scan ───────────────────────────────────────────


def scan_target_installs(
    target_name: str,
    target_cfg: TargetConfig,
    all_sources: dict,
    items: list[DiscoveredItem] | None = None,
) -> list[Install]:
    """Scan a target's .claude/skills/ and detect what's installed.

    Returns Install entries detected from the filesystem.
    """
    installs: list[Install] = []
    skills_dir = target_cfg.path
    if not skills_dir.exists():
        return installs

    # Build reverse map: resolved source path → (source_name, item)
    source_by_path: dict[str, tuple[str, DiscoveredItem | None]] = {}
    if items:
        for item in items:
            try:
                source_by_path[str(item.path.resolve())] = (item.qualified_name, item)
            except OSError:
                pass

    for entry in sorted(skills_dir.iterdir()):
        if entry.name.startswith("."):
            continue

        if entry.is_symlink():
            origin = entry.resolve()
            origin_str = str(origin)
            if origin_str in source_by_path:
                qname, _ = source_by_path[origin_str]
                method = InstallMethod.SYMLINK
            else:
                qname = ""
                method = InstallMethod.ORPHAN
            installs.append(Install(
                source=qname,
                target=target_name,
                name=entry.name,
                symlink=entry,
                origin=origin,
                method=method,
            ))
        elif entry.is_dir() and (entry / "SKILL.md").exists():
            # Local skill, not a symlink — orphan
            installs.append(Install(
                source="",
                target=target_name,
                name=entry.name,
                origin=entry,
                method=InstallMethod.ORPHAN,
            ))

    return installs


def scan_all_installs(
    all_targets: dict[str, TargetConfig],
    all_sources: dict,
    items: list[DiscoveredItem] | None = None,
) -> list[Install]:
    """Scan all targets and return all detected installs."""
    installs: list[Install] = []
    for target_name, target_cfg in all_targets.items():
        if target_cfg:
            installs.extend(scan_target_installs(target_name, target_cfg, all_sources, items))
    return installs


# ── Plugin installs (from Claude Code) ────────────────────────


def synthesize_plugin_installs(items: list[DiscoveredItem], all_targets: dict | None = None) -> list[Install]:
    """Create Install entries from Claude Code plugin data."""
    from skill_manager.core.discovery import load_plugin_install_entries

    raw_entries = load_plugin_install_entries()
    installs: list[Install] = []

    # Build reverse map: resolved project path → target name
    _target_by_path: dict[str, str] = {}
    if all_targets:
        for tname, tcfg in all_targets.items():
            if tcfg and tcfg.path:
                if tcfg.path.name == "skills" and tcfg.path.parent.name == ".claude":
                    project_dir = tcfg.path.parent.parent
                elif tcfg.path.name == "skills":
                    project_dir = tcfg.path.parent
                else:
                    project_dir = tcfg.path.parent
                try:
                    _target_by_path[str(project_dir.resolve())] = tname
                except OSError:
                    pass

    def _resolve_target(scope, project_path):
        if scope == "user":
            return "user", ""
        if project_path:
            try:
                resolved = str(Path(project_path).resolve())
            except OSError:
                resolved = project_path
            if resolved in _target_by_path:
                return _target_by_path[resolved], project_path
            return Path(project_path).name, project_path
        return "project", ""

    # installPath → [(target, project_path)]
    path_to_targets: dict[str, list[tuple[str, str]]] = {}
    for entry in raw_entries:
        ip = entry["install_path"]
        target, pp = _resolve_target(entry["scope"], entry.get("project_path", ""))
        path_to_targets.setdefault(ip, []).append((target, pp))

    for item in (items or []):
        if not item.source_name.startswith("plugin:"):
            continue
        item_path_str = str(item.path)
        for ip, target_list in path_to_targets.items():
            if item_path_str.startswith(ip):
                for target, proj_path in target_list:
                    installs.append(Install(
                        source=item.qualified_name,
                        target=target,
                        name=item.name,
                        origin=item.path,
                        method=InstallMethod.PLUGIN,
                        project_path=proj_path,
                    ))
                break

    return installs


# ── Unified installs (cached) ─────────────────────────────────


_installs_cache: list[Install] | None = None
_installs_cache_key: tuple | None = None


def all_installs(
    all_targets: dict[str, TargetConfig],
    all_sources: dict,
    items: list[DiscoveredItem] | None = None,
) -> list[Install]:
    """All installs: filesystem scan + Claude Code plugins.

    Cached per (targets, sources) to avoid redundant I/O during a single render cycle.
    Call invalidate_installs_cache() or discovery.invalidate_cache() to reset.
    """
    global _installs_cache, _installs_cache_key
    key = (
        tuple(sorted(all_targets.keys())) if all_targets else (),
        tuple(sorted(all_sources.keys())) if all_sources else (),
        len(items) if items else 0,
    )
    if _installs_cache is not None and _installs_cache_key == key:
        return _installs_cache

    result = scan_all_installs(all_targets, all_sources, items)
    if items:
        result.extend(synthesize_plugin_installs(items, all_targets))
    _installs_cache = result
    _installs_cache_key = key
    return result


def invalidate_installs_cache() -> None:
    """Clear the all_installs cache (call after install/uninstall or config reload)."""
    global _installs_cache, _installs_cache_key
    _installs_cache = None
    _installs_cache_key = None


# ── Status helpers ────────────────────────────────────────────


def _check_one(inst: Install) -> InstallState:
    if inst.method == InstallMethod.PLUGIN:
        return InstallState.INSTALLED if inst.origin.exists() else InstallState.BROKEN
    if inst.method == InstallMethod.ORPHAN:
        return InstallState.INSTALLED
    # Symlink
    if not inst.symlink or inst.symlink == Path():
        return InstallState.BROKEN
    if not inst.symlink.is_symlink():
        return InstallState.BROKEN
    if not inst.symlink.resolve().exists():
        return InstallState.BROKEN
    return InstallState.INSTALLED


def check_status(all_targets, all_sources, items=None):
    return [(_inst, _check_one(_inst)) for _inst in all_installs(all_targets, all_sources, items)]


def get_install_state(item: DiscoveredItem, all_targets, all_sources, items=None):
    unified = all_installs(all_targets, all_sources, items)
    for inst in unified:
        if inst.source == item.qualified_name:
            return _check_one(inst)
    # Marketplace matching
    if item.source_name.startswith("mp:"):
        mp_name = item.source_name[3:]
        for inst in unified:
            if inst.method == InstallMethod.PLUGIN and _mp_matches_plugin(mp_name, item.name, inst.source):
                return _check_one(inst)
    return InstallState.AVAILABLE


def _mp_matches_plugin(mp_name, skill_name, plugin_source):
    if not plugin_source.endswith(f":{skill_name}"):
        return False
    at_pos = plugin_source.find("@")
    if at_pos == -1:
        return False
    after_at = plugin_source[at_pos + 1:]
    end = len(after_at)
    for sep in (":", "#"):
        pos = after_at.find(sep)
        if pos != -1 and pos < end:
            end = pos
    return after_at[:end] == mp_name


def _same_skill_any_version(qname, inst_source):
    skill_q = qname.rsplit(":", 1)[-1]
    skill_i = inst_source.rsplit(":", 1)[-1]
    if skill_q != skill_i:
        return False
    def _plugin_at_mp(s):
        without_prefix = s.removeprefix("plugin:")
        before_skill = without_prefix.rsplit(":", 1)[0]
        return before_skill.split("#")[0]
    return _plugin_at_mp(qname) == _plugin_at_mp(inst_source)


def get_installs_for_item(item: DiscoveredItem, all_targets, all_sources, items=None):
    unified = all_installs(all_targets, all_sources, items)
    seen_targets: set[str] = set()
    result: list[Install] = []

    for i in unified:
        if i.target in seen_targets:
            continue
        if i.source == item.qualified_name or (i.source and _same_skill_any_version(item.qualified_name, i.source)):
            result.append(i)
            seen_targets.add(i.target)

    if not result and item.source_name.startswith("mp:"):
        mp_name = item.source_name[3:]
        for i in unified:
            if (i.method == InstallMethod.PLUGIN
                    and _mp_matches_plugin(mp_name, item.name, i.source)
                    and i.target not in seen_targets):
                result.append(i)
                seen_targets.add(i.target)

    return result


def get_installs_for_target(target_name, all_targets, all_sources, items=None):
    return [i for i in all_installs(all_targets, all_sources, items) if i.target == target_name]


# ── Install / Uninstall (symlink operations) ──────────────────


def install_symlink(item: DiscoveredItem, target_name: str, target_cfg: TargetConfig) -> tuple[bool, str]:
    """Create a symlink in target's .claude/skills/ pointing to the source."""
    skills_dir = target_cfg.path
    skills_dir.mkdir(parents=True, exist_ok=True)
    symlink = skills_dir / item.deploy_name

    if symlink.exists() and not symlink.is_symlink():
        return False, f"'{item.deploy_name}' exists and is not a symlink"
    if symlink.is_symlink():
        symlink.unlink()

    symlink.symlink_to(item.path)
    invalidate_installs_cache()
    return True, f"{symlink.name} -> {item.path}"


def uninstall_symlink(name: str, target_cfg: TargetConfig) -> tuple[bool, str]:
    """Remove a symlink from target's .claude/skills/."""
    symlink = target_cfg.path / name
    if symlink.is_symlink():
        symlink.unlink()
        invalidate_installs_cache()
        return True, f"removed {name}"
    return False, f"'{name}' is not a symlink"


def adopt_orphan(orphan_path: Path, destination_source_dir: Path) -> tuple[bool, str]:
    """Move an orphan skill directory to a source library and symlink back.

    orphan_path: real directory in .claude/skills/ (not a symlink)
    destination_source_dir: target source library path
    """
    import shutil

    if not orphan_path.is_dir() or orphan_path.is_symlink():
        return False, f"'{orphan_path.name}' is not a plain directory"

    dest = destination_source_dir / orphan_path.name
    if dest.exists():
        home = str(Path.home())
        dest_str = str(destination_source_dir)
        short = f"~{dest_str[len(home):]}" if dest_str.startswith(home) else dest_str
        return False, f"'{orphan_path.name}' already exists in {short}"

    destination_source_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(orphan_path), str(dest))
    orphan_path.symlink_to(dest)
    invalidate_installs_cache()

    home = str(Path.home())
    dest_str = str(dest)
    short = f"~{dest_str[len(home):]}" if dest_str.startswith(home) else dest_str
    return True, f"{orphan_path.name} → {short}"


# ── Claude Code plugin install/uninstall ──────────────────────


def _parse_plugin_ref(source_qname: str) -> str | None:
    """Extract the plugin ref (name@marketplace) from a qualified name.

    plugin:productivity@aclemen1-marketplace:gmail → productivity@aclemen1-marketplace
    plugin:productivity@aclemen1-marketplace#2.6.1:gmail → productivity@aclemen1-marketplace
    mp:anthropic-agent-skills:pdf → None (mp: items are marketplace catalog entries, not installed plugins)
    """
    if source_qname.startswith("plugin:"):
        after = source_qname[7:]
        if "@" not in after:
            return None
        before_skill = after.rsplit(":", 1)[0]
        return before_skill.split("#")[0]
    return None


def _resolve_plugin_ref(
    source_qname: str,
    all_installs_list: list[Install] | None = None,
    items: list[DiscoveredItem] | None = None,
) -> str | None:
    """Resolve a source qname to a plugin ref for claude CLI.

    For plugin: sources, extract directly.
    For mp: sources, first try matching an installed plugin,
    then fall back to building ref from item metadata.
    """
    ref = _parse_plugin_ref(source_qname)
    if ref:
        return ref
    if not source_qname.startswith("mp:"):
        return None

    parts = source_qname.split(":", 2)  # mp:marketplace:skill
    if len(parts) != 3:
        return None
    mp_name, skill_name = parts[1], parts[2]

    # Try matching an installed plugin
    if all_installs_list:
        for inst in all_installs_list:
            if inst.method == InstallMethod.PLUGIN and inst.source:
                inst_skill = inst.source.rsplit(":", 1)[-1]
                if inst_skill == skill_name and f"@{mp_name}" in inst.source:
                    return _parse_plugin_ref(inst.source)

    # Fall back: build ref from item metadata (plugin_name@marketplace)
    if items:
        for item in items:
            if item.qualified_name == source_qname and item.plugin_name:
                return f"{item.plugin_name}@{mp_name}"

    return None


def _project_dir_from_target(target_cfg: "TargetConfig | None") -> str | None:
    """Resolve a TargetConfig to the project root directory path."""
    if not target_cfg:
        return None
    p = target_cfg.path
    if p.name == "skills" and p.parent.name == ".claude":
        return str(p.parent.parent)
    if p.name == ".claude":
        return str(p.parent)
    return str(p)


def cc_plugin_install(source_qname: str, target: str, current_installs: list[Install] | None = None, items: list[DiscoveredItem] | None = None, target_cfg: "TargetConfig | None" = None) -> tuple[bool, str]:
    import subprocess
    ref = _resolve_plugin_ref(source_qname, current_installs, items)
    if not ref:
        skill = source_qname.rsplit(":", 1)[-1] if ":" in source_qname else source_qname
        return False, f"No plugin ref found for '{skill}'"
    scope = "user" if target == "user" else "project"
    cwd = _project_dir_from_target(target_cfg) if scope == "project" else None
    cmd = ["claude", "plugin", "install", ref, "--scope", scope]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
        if result.returncode == 0:
            return True, f"claude plugin install {ref} --scope {scope}"
        return False, f"Failed: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return False, f"Error: {e}"


def cc_plugin_uninstall(source_qname: str, target: str, current_installs: list[Install] | None = None, items: list[DiscoveredItem] | None = None, target_cfg: "TargetConfig | None" = None) -> tuple[bool, str]:
    import subprocess
    ref = _resolve_plugin_ref(source_qname, current_installs, items)
    if not ref:
        skill = source_qname.rsplit(":", 1)[-1] if ":" in source_qname else source_qname
        return False, f"No installed plugin found for '{skill}' — nothing to uninstall"
    scope = "user" if target == "user" else "project"
    cwd = _project_dir_from_target(target_cfg) if scope == "project" else None
    cmd = ["claude", "plugin", "uninstall", ref, "--scope", scope]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
        if result.returncode == 0:
            return True, f"claude plugin uninstall {ref} --scope {scope}"
        return False, f"Failed: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return False, f"Error: {e}"
