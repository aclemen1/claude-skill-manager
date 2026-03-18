"""CLI entry point for claude-skill-manager."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich import box

from skill_manager.core.config import load_config, save_config, ensure_config_dir
from skill_manager.core.discovery import (
    discover_all, resolve_all_sources, resolve_all_targets,
    resolve_adoption_destinations,
)
from skill_manager.core.deployer import (
    all_installs, check_status, get_install_state,
    get_installs_for_item, get_installs_for_target,
    install_symlink, uninstall_symlink,
    cc_plugin_install, cc_plugin_uninstall,
)
from skill_manager.models import InstallMethod, InstallState, SourceType, TargetConfig
from skill_manager.core.inventory import is_plugin_source
from skill_manager.core.conflicts import detect_conflicts

app = typer.Typer(
    name="csm",
    help=(
        "Claude Skill Manager (csm) — manage Claude Code skills across projects.\n\n"
        "Sources are directories containing SKILL.md files "
        "or Claude Code marketplace plugins. "
        "Targets are projects with a .claude/ directory. "
        "Installs are symlinks (managed by csm) or plugins (managed by Claude Code). "
        "Config: ~/.config/claude-skill-manager/csm.toml\n\n"
        "Use --json on any command for machine-readable output. "
        "Use 'csm schema' for full LLM/agent documentation."
    ),
    invoke_without_command=True,
)
console = Console()

# Global --json flag
_json_output = False


def _version_callback(value: bool):
    if value:
        import importlib.metadata
        print(importlib.metadata.version("claude-skill-manager"))
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _global_options(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON (machine-readable)")] = False,
    version: Annotated[bool, typer.Option("--version", "-V", help="Show version and exit", callback=_version_callback, is_eager=True)] = False,
):
    global _json_output
    _json_output = json_output
    if ctx.invoked_subcommand is None:
        # No subcommand → launch TUI
        from skill_manager.tui.app import SkillManagerApp
        tui_app = SkillManagerApp()
        tui_app.run(mouse=False)


# ── Helpers ───────────────────────────────────────────────────


def _error(message: str, reason: str = "error", code: int = 1) -> None:
    """Print a structured JSON error (always JSON, like GWS) and exit."""
    import json as json_mod
    import sys
    err = json_mod.dumps({"error": {"code": code, "message": message, "reason": reason}}, indent=2)
    sys.stderr.write(err + "\n")
    raise typer.Exit(code)


def _get_target_config(config, all_targets, target_name: str):
    if target_name in all_targets:
        return all_targets[target_name]
    _error(f"Target '{target_name}' not found.", reason="notFound")


def _source_icon(source_name: str, source_cfg=None) -> str:
    if source_cfg:
        match source_cfg.type:
            case "github":
                return "🌐"
            case "url":
                return "🔗"
    if source_name.startswith("mp:"):
        return "🏪"
    if source_name.startswith("plugin:"):
        return "🔌"
    if source_name.startswith("auto:"):
        return "🔍"
    return "📂"


def _source_label(source_name: str) -> str:
    for prefix in ("mp:", "plugin:", "auto:"):
        if source_name.startswith(prefix):
            return source_name[len(prefix):]
    return source_name


# ── sources ───────────────────────────────────────────────────


@app.command()
def sources(
    show_plugins: Annotated[bool, typer.Option("--plugins/--no-plugins", "-p/-P")] = True,
):
    """List all discovered sources: local skill directories and Claude Code marketplace plugins."""
    import json as json_mod
    config = load_config()
    items = discover_all(config)
    all_sources = resolve_all_sources(config)

    if not items:
        if _json_output:
            console.print_json("[]")
            return
        console.print("[dim]No items discovered.[/dim]")
        raise typer.Exit()

    by_source: dict[str, list] = {}
    for item in items:
        by_source.setdefault(item.source_name, []).append(item)

    if _json_output:
        result = []
        for src_name in sorted(by_source.keys()):
            if not show_plugins and is_plugin_source(src_name):
                continue
            src_cfg = all_sources.get(src_name)
            result.append({
                "source": src_name,
                "type": str(src_cfg.type) if src_cfg else "unknown",
                "path": str(src_cfg.path) if src_cfg else "",
                "items": [
                    {"name": i.name, "type": str(i.item_type), "qualified_name": i.qualified_name, "path": str(i.path)}
                    for i in sorted(by_source[src_name], key=lambda i: i.name)
                ],
            })
        console.print_json(json_mod.dumps(result))
        return

    tree = Tree("[bold]Sources (S)[/bold]")
    for src_name in sorted(by_source.keys()):
        if not show_plugins and is_plugin_source(src_name):
            continue
        src_items = by_source[src_name]
        src_cfg = all_sources.get(src_name)
        icon = _source_icon(src_name, src_cfg)
        label = _source_label(src_name)
        path_str = f" [dim]{src_cfg.path}[/dim]" if src_cfg else ""
        branch = tree.add(f"{icon} [bold cyan]{label}[/bold cyan] ({len(src_items)}){path_str}")
        for item in sorted(src_items, key=lambda i: i.name):
            scope = f" [yellow][{item.plugin_scope}][/yellow]" if item.plugin_scope else ""
            item_icon = "📁" if item.item_type == "skill" else "📄"
            if is_plugin_source(item.source_name):
                item_icon = "🔌"
            branch.add(f"{item_icon} {item.name}{scope}")

    console.print(tree)
    n_user = sum(1 for i in items if not is_plugin_source(i.source_name))
    n_plugin = sum(1 for i in items if is_plugin_source(i.source_name))
    console.print(f"\n[dim]{len(by_source)} sources, {n_user} user items, {n_plugin} plugin items[/dim]")


# ── targets ───────────────────────────────────────────────────


@app.command()
def targets():
    """List all discovered targets (projects with .claude/ directory), with install counts per target."""
    import json as json_mod
    config = load_config()

    items = discover_all(config)
    all_targets = resolve_all_targets(config)
    all_src = resolve_all_sources(config)

    from skill_manager.core.deployer import all_installs, get_installs_for_target
    unified = all_installs(all_targets, all_src, items)

    all_names: dict[str, TargetConfig | None] = {n: t for n, t in all_targets.items()}
    for inst in unified:
        if inst.target not in all_names:
            all_names[inst.target] = None

    def _target_info(name):
        tgt = all_names[name]
        target_inst = get_installs_for_target(name, all_targets, all_src, items)
        n_sym = sum(1 for i in target_inst if i.method == InstallMethod.SYMLINK)
        n_plug = sum(1 for i in target_inst if i.method == InstallMethod.PLUGIN)
        n_orphan = sum(1 for i in target_inst if i.method == InstallMethod.ORPHAN)
        if tgt:
            project_dir = tgt.path.parent.parent if tgt.path.name == "skills" else tgt.path.parent
            path_str = str(project_dir)
        elif target_inst:
            path_str = next((i.project_path for i in target_inst if i.project_path), "-")
        else:
            path_str = "-"
        return {"name": name, "path": path_str, "symlinks": n_sym, "plugins": n_plug, "orphans": n_orphan}

    data = [_target_info(n) for n in sorted(all_names.keys())]

    if _json_output:
        console.print_json(json_mod.dumps(data))
        return

    table = Table(title="Targets (T)", box=box.ROUNDED)
    table.add_column("Name", style="bold")
    table.add_column("Project")
    table.add_column("Installs", justify="right")

    for d in data:
        parts = []
        if d["symlinks"]:
            parts.append(f"[green]{d['symlinks']} sym[/green]")
        if d["plugins"]:
            parts.append(f"[blue]{d['plugins']} plug[/blue]")
        if d["orphans"]:
            parts.append(f"[yellow]{d['orphans']} orphan[/yellow]")
        count = " + ".join(parts) if parts else "[dim]0[/dim]"
        table.add_row(d["name"], d["path"], count)

    console.print(table)


# ── installs ──────────────────────────────────────────────────


@app.command()
def installs():
    """List all current installs: symlinks (managed by csm), plugins (managed by Claude Code), and orphans (unmanaged)."""
    import json as json_mod
    config = load_config()
    items = discover_all(config)
    all_targets = resolve_all_targets(config)
    results = check_status(all_targets, resolve_all_sources(config), items)

    if not results:
        if _json_output:
            console.print_json("[]")
            return
        console.print("[dim]No installs.[/dim]")
        raise typer.Exit()

    if _json_output:
        data = [
            {"source": inst.source, "target": inst.target, "name": inst.name,
             "method": str(inst.method), "state": str(state),
             "origin": str(inst.origin) if inst.origin != Path() else "",
             "symlink": str(inst.symlink) if inst.symlink != Path() else ""}
            for inst, state in results
        ]
        console.print_json(json_mod.dumps(data))
        return

    table = Table(title="Installs", box=box.ROUNDED)
    table.add_column("", width=3)
    table.add_column("Method", width=6)
    table.add_column("Source (s)", style="bold")
    table.add_column("Target (t)")
    table.add_column("Link", style="dim")

    n_sym = 0
    n_plug = 0
    for inst, state in results:
        icon = "[green]✓[/green]" if state == InstallState.INSTALLED else "[red]✗[/red]"
        if inst.method == InstallMethod.PLUGIN:
            method = "[blue]plugin[/blue]"
            link = str(inst.origin.name) if inst.origin != Path() else ""
            n_plug += 1
        else:
            method = "symlink"
            link = str(inst.symlink.name) if inst.symlink != Path() else ""
            n_sym += 1
        table.add_row(icon, method, inst.source, inst.target, link)

    console.print(table)
    console.print(f"\n[dim]{n_sym} symlinks + {n_plug} plugins = {len(results)} installs[/dim]")


# ── install ───────────────────────────────────────────────────


@app.command()
def install(
    what: Annotated[str, typer.Argument(help="Skill name, qualified name, or set name")],
    to: Annotated[str, typer.Option("--to", "-t", help="Target name")] = "user",
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
):
    """Install a local skill into a target by creating a symlink in .claude/skills/."""
    config = load_config()
    
    items = discover_all(config)
    all_targets = resolve_all_targets(config)

    to_install_items = [i for i in items if i.qualified_name == what or i.name == what]

    if not to_install_items:
        _error(f"No items matched '{what}'.", reason="notFound")

    target_cfg = _get_target_config(config, all_targets, to)

    console.print("[bold]Install:[/bold]")
    for item in to_install_items:
        console.print(f"  [green]+[/green] {item.name} -> {to}")

    if dry_run:
        console.print("\n[dim]Dry run.[/dim]")
        raise typer.Exit()

    if not typer.confirm("Proceed?"):
        raise typer.Abort()

    for item in to_install_items:
        ok, msg = install_symlink(item, to, target_cfg)
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} {msg}")


# ── uninstall ─────────────────────────────────────────────────


@app.command()
def uninstall(
    what: Annotated[str, typer.Argument(help="Skill name to uninstall")],
    frm: Annotated[str, typer.Option("--from", "-f", help="Target to uninstall from")] = "user",
):
    """Remove a skill symlink from a target's .claude/skills/ directory."""
    config = load_config()
    all_targets = resolve_all_targets(config)
    target_cfg = _get_target_config(config, all_targets, frm)

    ok, msg = uninstall_symlink(what, target_cfg)
    if ok:
        console.print(f"  [red]✓[/red] {msg}")
    else:
        console.print(f"[dim]{msg}[/dim]")


# ── list ──────────────────────────────────────────────────────


@app.command(name="list")
def list_items(
    source: Annotated[Optional[str], typer.Option("--source", "-s")] = None,
    show_plugins: Annotated[bool, typer.Option("--plugins/--no-plugins", "-p/-P")] = True,
):
    """Unified inventory of all discovered skills with their install state (installed/available/broken)."""
    import json as json_mod
    config = load_config()
    items = discover_all(config)
    all_targets = resolve_all_targets(config)
    all_src = resolve_all_sources(config)

    if source:
        items = [i for i in items if i.source_name == source or _source_label(i.source_name) == source]
    if not show_plugins:
        items = [i for i in items if not is_plugin_source(i.source_name)]

    if _json_output:
        data = []
        for item in sorted(items, key=lambda i: (i.source_name, i.name)):
            state = get_install_state(item, all_targets, all_src, items)
            item_installs = get_installs_for_item(item, all_targets, all_src, items)
            data.append({
                "name": item.name, "qualified_name": item.qualified_name,
                "source": item.source_name, "type": str(item.item_type),
                "state": str(state), "path": str(item.path),
                "description": item.description,
                "installed_in": [i.target for i in item_installs],
            })
        console.print_json(json_mod.dumps(data))
        return

    table = Table(title="Inventory", box=box.ROUNDED)
    table.add_column("", width=3)
    table.add_column("Type", width=6)
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Installed in")
    table.add_column("Description", max_width=35)

    for item in sorted(items, key=lambda i: (i.source_name, i.name)):
        state = get_install_state(item, all_targets, all_src, items)
        state_icon = {"installed": "[green]✓[/green]", "available": "[dim]○[/dim]", "broken": "[red]✗[/red]"}[state]
        type_label = "plugin" if is_plugin_source(item.source_name) else "skill"
        item_installs = get_installs_for_item(item, all_targets, all_src, items)
        targets_str = ", ".join(i.target for i in item_installs) if item_installs else ""
        table.add_row(state_icon, type_label, item.name, _source_label(item.source_name), targets_str, item.description[:35])

    console.print(table)


# ── conflicts ─────────────────────────────────────────────────


@app.command()
def diagnostics(
    all_: Annotated[bool, typer.Option("--all", "-a")] = False,
):
    """Detect per-target name collisions (user-user, user-plugin, cross-marketplace, orphan-plugin) and stale cache."""
    import json as json_mod
    config = load_config()
    items = discover_all(config)
    all_targets = resolve_all_targets(config)
    all_sources = resolve_all_sources(config)
    from skill_manager.core.deployer import all_installs as get_all_installs
    installs_list = get_all_installs(all_targets, all_sources, items)
    found = detect_conflicts(items, installs_list)

    if not all_:
        found = [c for c in found if c.severity != "info"]

    if _json_output:
        data = [
            {"name": c.name, "target": c.target, "type": c.conflict_type,
             "severity": str(c.severity), "items": c.items}
            for c in found
        ]
        console.print_json(json_mod.dumps(data))
        return

    if not found:
        if _json_output:
            console.print_json("[]")
            return
        console.print("[green]No conflicts.[/green]")
        raise typer.Exit()

    severity_style = {"error": ("bold red", "ERROR"), "warning": ("yellow", "WARN"), "info": ("dim", "INFO")}

    for sev in ("error", "warning", "info"):
        group = [c for c in found if c.severity == sev]
        if not group:
            continue
        style, label = severity_style[sev]
        console.print(f"\n[{style}]── {label} ({len(group)}) ──[/{style}]")
        for c in group:
            target_str = f" [dim]in {c.target}[/dim]" if c.target else ""
            console.print(f"  [{style}]{c.name}[/{style}] [dim]({c.conflict_type})[/dim]{target_str}")
            for item in c.items:
                console.print(f"    - {item}")

    n_err = sum(1 for c in found if c.severity == "error")
    n_warn = sum(1 for c in found if c.severity == "warning")
    n_info = sum(1 for c in found if c.severity == "info")
    parts = []
    if n_err:
        parts.append(f"[red]{n_err} errors[/red]")
    if n_warn:
        parts.append(f"[yellow]{n_warn} warnings[/yellow]")
    if n_info:
        parts.append(f"[dim]{n_info} info[/dim]")
    console.print(f"\n{', '.join(parts)}")


# ── updates ───────────────────────────────────────────────────


@app.command()
def updates():
    """Detect stale plugin cache: plugins installed with multiple versions in the same scope/project."""
    from skill_manager.core.updates import detect_outdated, update_plugin

    config = load_config()
    all_targets = resolve_all_targets(config)

    # Build target reverse map
    target_by_path: dict[str, str] = {}
    for tname, tcfg in all_targets.items():
        if tcfg and tcfg.path:
            if tcfg.path.name == "skills" and tcfg.path.parent.name == ".claude":
                pdir = tcfg.path.parent.parent
            else:
                pdir = tcfg.path.parent
            try:
                target_by_path[str(pdir.resolve())] = tname
            except OSError:
                pass

    outdated = detect_outdated(target_by_path)
    if not outdated:
        console.print("[green]All plugins up to date.[/green]")
        raise typer.Exit()

    table = Table(title="Outdated Plugins", box=box.ROUNDED)
    table.add_column("Plugin")
    table.add_column("Target")
    table.add_column("Current")
    table.add_column("Latest")
    table.add_column("Scope")

    for o in outdated:
        table.add_row(
            f"{o.plugin_name}@{o.marketplace}",
            o.target,
            o.current_version[:12],
            f"[green]{o.latest_version[:12]}[/green]",
            o.scope,
        )

    console.print(table)

    if typer.confirm(f"\nUpdate {len(outdated)} plugin(s)?"):
        for o in outdated:
            ok, msg = update_plugin(o.plugin_id, o.scope, o.project_path)
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"  {icon} {msg}")


# ── adopt ─────────────────────────────────────────────────────


@app.command()
def adopt(
    orphan: Annotated[str, typer.Argument(help="Orphan skill name to adopt")],
    frm: Annotated[str, typer.Option("--from", "-f", help="Target name where the orphan lives")],
    to: Annotated[Optional[str], typer.Option("--to", "-t", help="Source library name to move the orphan into")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
):
    """Adopt an orphan skill: move it to a source library and create a symlink back.

    The orphan directory is moved into the source library, then a symlink is created
    at the original location pointing to its new home in the source.
    """
    config = load_config()
    all_targets = resolve_all_targets(config)
    all_sources = resolve_all_sources(config)

    # Validate target
    target_cfg = _get_target_config(config, all_targets, frm)

    # Guard: user scope is managed by Claude Code — orphans cannot exist there
    if target_cfg and target_cfg.path:
        project_dir = (
            target_cfg.path.parent.parent
            if target_cfg.path.name == "skills" and target_cfg.path.parent.name == ".claude"
            else target_cfg.path.parent
        )
        try:
            if project_dir.resolve() == Path.home().resolve():
                _error(
                    "User scope is managed by Claude Code and cannot contain orphans. "
                    "Specify a project target with --from.",
                    reason="invalidOperation",
                )
        except OSError:
            pass

    orphan_path = target_cfg.path / orphan

    if not orphan_path.exists():
        _error(f"'{orphan}' not found in target '{frm}'.", reason="notFound")
    if orphan_path.is_symlink():
        _error(f"'{orphan}' is a symlink, not an orphan directory.", reason="invalidOperation")

    # If --to not given, list available sources and abort
    skill_sources = resolve_adoption_destinations(config.source_paths)
    if to is None:
        if _json_output:
            import json as json_mod
            console.print_json(json_mod.dumps({"available_sources": list(skill_sources.keys())}))
        else:
            console.print("[yellow]Specify --to SOURCE. Available local skill sources:[/yellow]")
            home = str(Path.home())
            for name, cfg in sorted(skill_sources.items()):
                path_str = str(cfg.path)
                short = f"~{path_str[len(home):]}" if path_str.startswith(home) else path_str
                console.print(f"  [cyan]{name}[/cyan]  [dim]{short}[/dim]")
        raise typer.Exit(1)

    if to not in skill_sources:
        _error(
            f"Source '{to}' not found in configured source_paths. "
            "Only local skill sources managed by csm are valid adoption destinations.",
            reason="notFound",
        )

    src_cfg = skill_sources[to]
    dest = src_cfg.path / orphan

    home = str(Path.home())
    orphan_short = str(orphan_path)
    dest_short = str(dest)
    if orphan_short.startswith(home):
        orphan_short = f"~{orphan_short[len(home):]}"
    if dest_short.startswith(home):
        dest_short = f"~{dest_short[len(home):]}"

    console.print(f"[bold]Adopt orphan:[/bold] [dark_orange]{orphan}[/dark_orange]")
    console.print(f"  [dim]move[/dim]  {orphan_short}")
    console.print(f"        → {dest_short}")
    console.print(f"  [dim]link[/dim]  {orphan_short} → {dest_short}")

    if dry_run:
        console.print("\n[dim]Dry run — no changes made.[/dim]")
        raise typer.Exit()

    if not typer.confirm("Proceed?"):
        raise typer.Abort()

    from skill_manager.core.deployer import adopt_orphan
    ok, msg = adopt_orphan(orphan_path, src_cfg.path)
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"  {icon} {msg}")
    if not ok:
        raise typer.Exit(1)


# ── tui ───────────────────────────────────────────────────────


@app.command()
def tui():
    """Launch the interactive terminal UI (sources/targets panels, pending changes, diagnostics)."""
    from skill_manager.tui.app import SkillManagerApp
    tui_app = SkillManagerApp()
    tui_app.run(mouse=False)


# ── init ──────────────────────────────────────────────────────


@app.command()
def init():
    """Create a default ~/.config/claude-skill-manager/csm.toml config file with example glob patterns."""
    config_dir = ensure_config_dir()
    config_path = config_dir / "csm.toml"

    if config_path.exists():
        console.print(f"[yellow]Already exists: {config_path}[/yellow]")
        if not typer.confirm("Overwrite?", default=False):
            raise typer.Abort()

    config_path.write_text("""\
# Skill Manager configuration
# Docs: https://github.com/aclemen1/claude-skill-manager
#
# Paths support glob patterns:
#   ~              exact (this directory only)
#   ~/code/*       direct children
#   ~/vaults/**    recursive
#   ~/code/*/lib   pattern matching

# Enable Claude Code marketplace plugin discovery
# plugins = true

# Glob patterns for skill source directories (each scanned for */SKILL.md)
source_paths = ["~/skills-library"]

# Glob patterns for target directories (each checked for .claude/ presence)
target_paths = ["~"]
""")
    console.print(f"[green]Created {config_path}[/green]")


@app.command()
def schema():
    """Output a JSON schema describing all csm commands, concepts, and config format — designed for LLM/agent consumption."""
    import json
    import importlib.metadata

    schema = {
        "tool": "csm",
        "version": importlib.metadata.version("claude-skill-manager"),
        "description": (
            "Claude Skill Manager (csm) manages Claude Code skills across projects. "
            "It discovers skills from local directories and Claude Code marketplace plugins, "
            "and installs them into target projects via symlinks or plugin install. "
            "All query commands support --json for machine-readable output."
        ),
        "global_flags": {
            "--json / -j": "Output as JSON (works on: sources, targets, installs, list, diagnostics, updates)",
            "--help": "Show help for any command",
        },
        "concepts": {
            "source": "A directory containing skill subdirectories (each with SKILL.md) or a Claude Code marketplace plugin.",
            "target": "A project directory containing .claude/ — skills are installed into .claude/skills/ via symlinks.",
            "install": "A (source, target) pair. Symlinks are managed by csm; plugins are managed by Claude Code CLI.",
            "orphan": "A skill directory in .claude/skills/ that is not a symlink and not from any known source.",
            "SKILL.md": "A markdown file with YAML frontmatter (name, description) that defines a skill. Must be in a subdirectory.",
            "glob_pattern": "Config paths support glob: ~ (exact), ~/code/* (children), ~/vaults/** (recursive), ~/code/*/backend (pattern).",
        },
        "config": {
            "path": "~/.config/claude-skill-manager/csm.toml",
            "format": "TOML",
            "keys": {
                "plugins": {"type": "bool", "default": True, "description": "Enable Claude Code marketplace plugin discovery."},
                "source_paths": {"type": "list[str]", "description": "Glob patterns → directories scanned for */SKILL.md."},
                "target_paths": {"type": "list[str]", "description": "Glob patterns → directories checked for .claude/ presence."},
            },
            "example": (
                'source_paths = ["~/skills-library", "~/code/my-org/*"]\n'
                'target_paths = ["~", "~/code/my-org/*"]'
            ),
        },
        "commands": [
            {
                "name": "sources", "usage": "csm sources [--no-plugins] [--json]",
                "description": "List all discovered sources with their skills.",
                "examples": ["csm sources", "csm sources --no-plugins", "csm --json sources"],
            },
            {
                "name": "targets", "usage": "csm targets [--json]",
                "description": "List all discovered targets with install counts.",
                "examples": ["csm targets", "csm --json targets"],
            },
            {
                "name": "installs", "usage": "csm installs [--json]",
                "description": "List all current installs (symlinks, plugins, orphans) with their state.",
                "examples": ["csm installs", "csm --json installs"],
            },
            {
                "name": "install", "usage": "csm install SKILL_NAME --to TARGET [--dry-run]",
                "description": "Install a local skill into a target via symlink. Checks for name conflicts before applying.",
                "examples": ["csm install my-skill --to user", "csm install my-skill --to GEMM --dry-run"],
            },
            {
                "name": "uninstall", "usage": "csm uninstall TARGET",
                "description": "Remove all skill symlinks from a target's .claude/skills/.",
                "examples": ["csm uninstall user", "csm uninstall GEMM"],
            },
            {
                "name": "adopt", "usage": "csm adopt ORPHAN_NAME --from TARGET --to SOURCE [--dry-run]",
                "description": (
                    "Adopt an orphan skill: move the directory from .claude/skills/ into a source library, "
                    "then create a symlink at the original location. "
                    "Omit --to to list available sources."
                ),
                "examples": [
                    "csm adopt my-skill --from myproj --to auto:skills",
                    "csm adopt my-skill --from myproj --to auto:skills --dry-run",
                ],
            },
            {
                "name": "list", "usage": "csm list [--source NAME] [--no-plugins] [--json]",
                "description": "Unified inventory with install state (installed/available/broken) per item.",
                "examples": ["csm list", "csm list --no-plugins", "csm --json list"],
            },
            {
                "name": "diagnostics", "usage": "csm diagnostics [--all] [--json]",
                "description": "Detect per-target name collisions and issues. --all includes INFO-level.",
                "examples": ["csm diagnostics", "csm diagnostics --all", "csm --json diagnostics"],
            },
            {
                "name": "updates", "usage": "csm updates",
                "description": "Detect stale plugin cache (multiple versions in same scope/project). Interactive update prompt.",
                "examples": ["csm updates"],
            },
            {
                "name": "tui", "usage": "csm tui",
                "description": "Launch the interactive terminal UI with sources/targets panels.",
                "examples": ["csm tui"],
            },
            {
                "name": "init", "usage": "csm init",
                "description": "Create a default csm.toml config file.",
                "examples": ["csm init"],
            },
            {
                "name": "schema", "usage": "csm schema",
                "description": "Output this JSON schema for LLM/agent consumption.",
                "examples": ["csm schema"],
            },
        ],
        "diagnostics_types": [
            {"type": "user-user", "severity": "ERROR", "description": "Two local skills with the same name installed in the same target."},
            {"type": "user-plugin", "severity": "WARNING", "description": "A local skill shadows a Claude Code plugin skill in the same target."},
            {"type": "cross-marketplace", "severity": "WARNING", "description": "Same skill name from different marketplaces in the same target."},
            {"type": "orphan-plugin", "severity": "WARNING", "description": "An unmanaged skill copy coexists with a plugin skill in the same target."},
            {"type": "mp-cache", "severity": "INFO", "description": "Same skill in marketplace catalog and its installed cache (normal)."},
        ],
        "tui_keybindings": {
            "navigation": {"j/k": "Move down/up", "Enter": "Expand/collapse (fold)", "l": "Expand", "h": "Collapse or parent", "L": "Expand all", "H": "Collapse all"},
            "selection": {"Space": "Select (switch to toggle mode on other panel)", "Tab/Shift+Tab": "Cycle panels"},
            "install": {"x": "Toggle install/uninstall", "A": "Adopt orphan (move to source library)"},
            "preview": {"p": "Preview SKILL.md", "e": "Edit (in preview modal, local skills only)"},
            "actions": {"a": "Apply pending changes", "d": "Delete pending change", "Esc": "Cancel all pending", "r": "Refresh", "q": "Quit"},
            "modals": {"s": "Settings", "D": "Diagnostics", "?": "Help"},
        },
    }

    console.print_json(json.dumps(schema, indent=2))
