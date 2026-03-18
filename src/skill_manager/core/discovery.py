"""Discover skills from configured sources."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from skill_manager.models import (
    DiscoveredItem,
    ItemType,
    SmConfig,
    SourceConfig,
    SourceType,
    TargetConfig,
)




def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    front = text[3:end].strip()
    body = text[end + 3 :].strip()
    try:
        meta = yaml.safe_load(front) or {}
    except Exception:
        meta = {}
    return meta, body


# ── Scanners by source type ──────────────────────────────────


def _scan_skills(source_name: str, base_path: Path, recursive: bool) -> list[DiscoveredItem]:
    items: list[DiscoveredItem] = []
    if not base_path.exists():
        return items

    skill_files = sorted(base_path.rglob("SKILL.md") if recursive else base_path.glob("*/SKILL.md"))

    for skill_md in skill_files:
        skill_dir = skill_md.parent
        meta, body = _parse_frontmatter(skill_md)
        items.append(
            DiscoveredItem(
                name=meta.get("name", skill_dir.name),
                source_name=source_name,
                item_type=ItemType.SKILL,
                path=skill_dir,
                description=meta.get("description", ""),
                frontmatter=meta,
            )
        )
    return items


def _read_plugin_json(install_path: Path) -> dict:
    """Read plugin.json metadata from a plugin's install directory."""
    for candidate in (install_path / ".claude-plugin" / "plugin.json", install_path / "plugin.json"):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    return {}


def _scan_plugin(
    source_name: str, install_path: Path,
    scope: str = "", version: str = "", project_path: str = "",
) -> list[DiscoveredItem]:
    items: list[DiscoveredItem] = []
    project_name = Path(project_path).name if project_path else ""

    skills_dir = install_path / "skills"
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta, body = _parse_frontmatter(skill_md)
            items.append(
                DiscoveredItem(
                    name=meta.get("name", skill_dir.name),
                    source_name=source_name,
                    item_type=ItemType.SKILL,
                    path=skill_dir,
                    description=meta.get("description", ""),
                    frontmatter=meta,
                    plugin_scope=scope,
                    plugin_version=version,
                    plugin_project=project_name,
                )
            )

    # If no skills found, represent the plugin itself as an item
    # so that hook-only or agent-only plugins remain visible.
    if not items:
        meta = _read_plugin_json(install_path)
        plugin_name = meta.get("name", install_path.name)
        items.append(
            DiscoveredItem(
                name=plugin_name,
                source_name=source_name,
                item_type=ItemType.PLUGIN,
                path=install_path,
                description=meta.get("description", ""),
                frontmatter=meta,
                plugin_scope=scope,
                plugin_version=version,
                plugin_project=project_name,
                plugin_name=plugin_name,
            )
        )

    return items


def _scan_marketplace(source_name: str, marketplace_path: Path) -> list[DiscoveredItem]:
    items: list[DiscoveredItem] = []
    if not marketplace_path.exists():
        return items

    # Flat layout: skills/ directly under root
    flat_skills = marketplace_path / "skills"
    if flat_skills.is_dir():
        for skill_dir in sorted(flat_skills.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta, body = _parse_frontmatter(skill_md)
            items.append(
                DiscoveredItem(
                    name=meta.get("name", skill_dir.name),
                    source_name=source_name,
                    item_type=ItemType.SKILL,
                    path=skill_dir,
                    description=meta.get("description", ""),
                    frontmatter=meta,
                )
            )

    # Plugin layout: plugins/<plugin>/skills/<skill>/SKILL.md
    for subdir_name in ("plugins", "external_plugins"):
        subdir = marketplace_path / subdir_name
        if not subdir.is_dir():
            continue
        for plugin_dir in sorted(subdir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            pname = plugin_dir.name
            plugin_skills = plugin_dir / "skills"
            if not plugin_skills.is_dir():
                continue
            for skill_dir in sorted(plugin_skills.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                meta, body = _parse_frontmatter(skill_md)
                items.append(
                    DiscoveredItem(
                        name=meta.get("name", skill_dir.name),
                        source_name=source_name,
                        item_type=ItemType.SKILL,
                        path=skill_dir,
                        description=meta.get("description", ""),
                        frontmatter=meta,
                        plugin_name=pname,
                    )
                )

    return items


# ── GitHub source ─────────────────────────────────────────────


# ── Auto-discovery via `claude plugin` CLI ────────────────────


_cc_data: dict | None = None  # cached result of fetch_claude_code_data()


def fetch_claude_code_data() -> dict:
    """Call Claude Code CLI once and return both marketplaces and plugins.

    Returns {"marketplaces": [...], "plugins": [...]}.
    Cached for the session lifetime; call invalidate_cache() to reset.
    """
    global _cc_data
    if _cc_data is not None:
        return _cc_data

    import subprocess

    def _call(args):
        try:
            result = subprocess.run(
                ["claude"] + args,
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
        return []

    _cc_data = {
        "marketplaces": _call(["plugin", "marketplace", "list", "--json"]),
        "plugins": _call(["plugin", "list", "--json"]),
    }
    return _cc_data


def auto_discover_plugin_sources() -> dict[str, SourceConfig]:
    """Discover sources from Claude Code CLI data."""
    data = fetch_claude_code_data()
    sources: dict[str, SourceConfig] = {}

    # 1. Marketplaces
    for mp in data.get("marketplaces", []):
        mp_name = mp.get("name", "")
        install_loc = mp.get("installLocation", "")
        if not mp_name or not install_loc:
            continue
        mp_path = Path(install_loc)
        if not mp_path.exists():
            continue
        sources[f"mp:{mp_name}"] = SourceConfig(
            path=mp_path,
            type=SourceType.MARKETPLACE,
        )

    # 2. Installed plugins (deduplicated by installPath)
    seen_paths: set[str] = set()
    for entry in data.get("plugins", []):
        plugin_id = entry.get("id", "")
        install_path = entry.get("installPath", "")
        if not plugin_id or not install_path:
            continue

        p = Path(install_path)
        if not p.exists() or install_path in seen_paths:
            continue
        seen_paths.add(install_path)

        parts = plugin_id.split("@")
        plugin_name = parts[0]
        marketplace = parts[1] if len(parts) > 1 else "unknown"
        version = entry.get("version", "")

        source_key = f"plugin:{plugin_name}@{marketplace}"
        if source_key in sources:
            source_key = f"plugin:{plugin_name}@{marketplace}#{version[:8]}"
        sources[source_key] = SourceConfig(
            path=p,
            type=SourceType.PLUGIN,
            scope=entry.get("scope", "user"),
            version=version,
            project_path=entry.get("projectPath", ""),
        )

    return sources


def load_plugin_install_entries() -> list[dict]:
    """Get ALL plugin install entries from cached CLI data."""
    data = fetch_claude_code_data()
    entries_out: list[dict] = []

    for entry in data.get("plugins", []):
        plugin_id = entry.get("id", "")
        install_path = entry.get("installPath", "")
        if not plugin_id or not install_path:
            continue

        parts = plugin_id.split("@")
        plugin_name = parts[0]
        marketplace = parts[1] if len(parts) > 1 else "unknown"

        entries_out.append({
            "plugin_name": plugin_name,
            "marketplace": marketplace,
            "install_path": install_path,
            "scope": entry.get("scope", "user"),
            "version": entry.get("version", ""),
            "project_path": entry.get("projectPath", ""),
        })

    return entries_out


_HOME = str(Path.home())


def _expand(s: str) -> str:
    """Expand ~ to home dir."""
    return s.replace("~", _HOME) if s.startswith("~") else s


def _has_glob(s: str) -> bool:
    return any(c in s for c in ("*", "?", "["))


def resolve_glob(pattern: str) -> list[Path]:
    """Resolve a glob pattern to a list of existing directories.

    - No glob chars → the path itself (if it exists)
    - Contains * / ** / ? / [...] → expand via Path.glob()
    """
    expanded = _expand(pattern)
    if not _has_glob(expanded):
        p = Path(expanded)
        return [p] if p.is_dir() else []

    # Split into base (non-glob prefix) + glob part
    parts = Path(expanded).parts
    base_parts = []
    for part in parts:
        if _has_glob(part):
            break
        base_parts.append(part)
    base = Path(*base_parts) if base_parts else Path(".")
    glob_part = str(Path(expanded).relative_to(base))

    if not base.exists():
        return []

    results = []
    for p in sorted(base.glob(glob_part)):
        if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("_"):
            results.append(p)
    return results


_source_scan_cache: dict[str, dict[str, SourceConfig]] = {}
_target_scan_cache: dict[str, dict[str, TargetConfig]] = {}


def resolve_adoption_destinations(patterns: list[str]) -> dict[str, SourceConfig]:
    """Resolve source_paths patterns to all potential adoption destinations.

    A destination is a source root — the parent directory where skills live.
    Mirrors the semantics of auto_discover_source_paths but includes dirs with
    no skills yet:

      - no-glob 'a'   → 'a' itself
      - glob 'a/*'    → 'a' (parent of the glob results)
      - glob 'a/*/*'  → every 'a/X' dir (parent of depth-2 results)
      - '**' patterns → all dirs within depth 2 of the non-glob base

    Directories whose name starts with '.' are excluded.
    """
    sources: dict[str, SourceConfig] = {}
    home = Path.home()

    def _add(p: Path) -> None:
        if p.name.startswith("."):
            return
        try:
            name = str(p.relative_to(home)).replace("/", ":")
        except ValueError:
            name = p.name
        key = f"auto:{name}"
        if key not in sources:
            sources[key] = SourceConfig(path=p, type=SourceType.SKILL, recursive=False)

    for pattern in patterns:
        expanded = _expand(pattern)

        if not _has_glob(expanded):
            p = Path(expanded)
            if p.is_dir():
                _add(p)
            continue

        # Compute the non-glob base prefix
        parts = list(Path(expanded).parts)
        base_parts: list[str] = []
        for part in parts:
            if _has_glob(part):
                break
            base_parts.append(part)
        base = Path(*base_parts) if base_parts else Path(".")

        if "**" in expanded:
            # Recursive glob: destinations are dirs within depth 2 of base
            for scan_path in resolve_glob(pattern):
                if scan_path.name.startswith("."):
                    continue
                try:
                    if len(scan_path.relative_to(base).parts) > 2:
                        continue
                except ValueError:
                    pass
                _add(scan_path)
            continue

        # Non-recursive glob: destinations are the *parents* of the glob results,
        # i.e., dirs matched by the pattern with its last glob segment removed.
        # This naturally includes empty dirs (e.g. skills-library with no skills yet).
        parent_parts = parts[:]
        for i in range(len(parent_parts) - 1, -1, -1):
            if _has_glob(parent_parts[i]):
                parent_parts = parent_parts[:i]
                break

        if not parent_parts:
            continue

        parent_expanded = str(Path(*parent_parts))

        if not _has_glob(parent_expanded):
            # Parent is a plain path (e.g. a/* → parent = a)
            p = Path(parent_expanded)
            if p.is_dir():
                _add(p)
        else:
            # Parent is still a glob (e.g. a/*/* → parent = a/*)
            parent_base_parts: list[str] = []
            for part in parent_parts:
                if _has_glob(part):
                    break
                parent_base_parts.append(part)
            parent_base = Path(*parent_base_parts) if parent_base_parts else Path(".")
            parent_glob = str(Path(parent_expanded).relative_to(parent_base))
            if parent_base.exists():
                for p in sorted(parent_base.glob(parent_glob)):
                    if p.is_dir() and not p.name.startswith("."):
                        _add(p)

    return dict(sorted(sources.items()))


def auto_discover_source_paths(patterns: list[str]) -> dict[str, SourceConfig]:
    """Scan glob patterns for directories containing SKILL.md files.

    Semantics:
      - No-glob pattern 'a': 'a' is a source root; scan a/*/SKILL.md.
      - Glob pattern 'a/*': each resolved dir may be a skill; check SKILL.md
        directly inside it; the source root is its parent.
      - Glob 'a/*/*': same — the resolved a/X/Y dirs may be skills, source = a/X.

    In short, glob wildcards mark where skill directories are expected to live.
    """
    cache_key = "src|" + "|".join(patterns)
    if cache_key in _source_scan_cache:
        return _source_scan_cache[cache_key]

    sources: dict[str, SourceConfig] = {}
    seen_dirs: set[str] = set()
    home = Path.home()

    def _add_source(source_dir: Path) -> None:
        key = str(source_dir.resolve())
        if key in seen_dirs:
            return
        seen_dirs.add(key)
        try:
            name = str(source_dir.relative_to(home)).replace("/", ":")
        except ValueError:
            name = source_dir.name
        sources[f"auto:{name}"] = SourceConfig(
            path=source_dir, type=SourceType.SKILL, recursive=False,
        )

    for pattern in patterns:
        expanded = _expand(pattern)
        has_glob = _has_glob(expanded)
        for scan_path in resolve_glob(pattern):
            if has_glob:
                # The glob designates potential skill dirs; SKILL.md lives directly
                # inside each resolved path, and the source root is its parent.
                if (scan_path / "SKILL.md").exists():
                    _add_source(scan_path.parent)
            else:
                # Exact path: it is the source root; skills are its subdirectories.
                for skill_md in sorted(scan_path.glob("*/SKILL.md")):
                    _add_source(skill_md.parent.parent)

    _source_scan_cache[cache_key] = sources
    return sources


def auto_discover_target_paths(patterns: list[str]) -> dict[str, TargetConfig]:
    """Resolve glob patterns and look for .claude/ dirs in each resolved directory.

    Each resolved path is checked for .claude/ directly (not recursively).
    """
    cache_key = "tgt|" + "|".join(patterns)
    if cache_key in _target_scan_cache:
        return _target_scan_cache[cache_key]

    targets: dict[str, TargetConfig] = {}

    for pattern in patterns:
        for scan_path in resolve_glob(pattern):
            claude_dir = scan_path / ".claude"
            if not claude_dir.is_dir():
                continue
            home = Path.home()
            project_name = "user" if scan_path.resolve() == home.resolve() else (scan_path.name or "user")
            skills_dir = claude_dir / "skills"
            commands_dir = claude_dir / "commands"
            if project_name not in targets:
                targets[project_name] = TargetConfig(
                    path=skills_dir,
                    commands_path=commands_dir if commands_dir.exists() else None,
                )

    _target_scan_cache[cache_key] = targets
    return targets


# ── Main discovery ────────────────────────────────────────────


def discover_source(source_name: str, source: SourceConfig) -> list[DiscoveredItem]:
    """Discover all items in a single source."""
    match source.type:
        case SourceType.MARKETPLACE:
            return _scan_marketplace(source_name, source.path)
        case SourceType.PLUGIN:
            return _scan_plugin(
                source_name, source.path,
                scope=source.scope, version=source.version,
                project_path=source.project_path,
            )
        case _:
            return _scan_skills(source_name, source.path, source.recursive)


def _resolved_paths(sources: dict[str, SourceConfig]) -> set[str]:
    """Get the set of resolved (real) paths from existing sources."""
    paths: set[str] = set()
    for src in sources.values():
        if src.path and src.path != Path():
            try:
                paths.add(str(src.path.resolve()))
            except OSError:
                pass
    return paths


_sources_cache: dict[int, dict[str, SourceConfig]] = {}
_targets_cache: dict[int, dict[str, TargetConfig]] = {}


def resolve_all_sources(config: SmConfig) -> dict[str, SourceConfig]:
    """Build all sources: plugins + glob-discovered skills."""
    cache_key = id(config)
    if cache_key in _sources_cache:
        return _sources_cache[cache_key]

    all_sources: dict[str, SourceConfig] = {}
    known_paths: set[str] = set()

    def _add(name, src):
        if name in all_sources:
            return
        try:
            resolved = str(src.path.resolve())
        except OSError:
            resolved = str(src.path)
        if resolved not in known_paths:
            all_sources[name] = src
            known_paths.add(resolved)

    # 1. Claude Code plugins
    if config.plugins:
        for name, src in auto_discover_plugin_sources().items():
            _add(name, src)

    # 2. Skill sources (glob patterns → scan for SKILL.md)
    if config.source_paths:
        for name, src in auto_discover_source_paths(config.source_paths).items():
            _add(name, src)

    _sources_cache[cache_key] = all_sources
    return all_sources


def resolve_all_targets(config: SmConfig) -> dict[str, TargetConfig]:
    """Resolve glob patterns and discover targets with .claude/ dirs."""
    cache_key = id(config)
    if cache_key in _targets_cache:
        return _targets_cache[cache_key]

    all_targets: dict[str, TargetConfig] = {}
    known_paths: set[str] = set()

    if config.target_paths:
        for name, tgt in auto_discover_target_paths(config.target_paths).items():
            if name in all_targets:
                continue
            try:
                resolved = str(tgt.path.resolve())
            except OSError:
                resolved = str(tgt.path)
            if resolved not in known_paths:
                all_targets[name] = tgt
                known_paths.add(resolved)

    _targets_cache[cache_key] = all_targets
    return all_targets


def invalidate_cache() -> None:
    """Clear discovery caches (call after config reload)."""
    global _cc_data
    _cc_data = None
    _sources_cache.clear()
    _targets_cache.clear()
    _source_scan_cache.clear()
    _target_scan_cache.clear()
    from skill_manager.core.deployer import invalidate_installs_cache
    invalidate_installs_cache()


def discover_all(config: SmConfig) -> list[DiscoveredItem]:
    """Discover all items from all sources."""
    all_items: list[DiscoveredItem] = []
    all_sources = resolve_all_sources(config)

    for source_name, source in all_sources.items():
        items = discover_source(source_name, source)
        all_items.extend(items)

    return all_items


