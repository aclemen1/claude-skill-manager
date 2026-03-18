"""Right panel — Targets (T). Unified list, no cc/filesystem split."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree, Label

from skill_manager.core.budget import get_token_estimate
from skill_manager.core.deployer import all_installs, get_installs_for_target, get_installs_for_item, InstallMethod
from skill_manager.models import DiscoveredItem, Install, TargetConfig

_HOME = str(Path.home())


def _short(p: str | Path) -> str:
    s = str(p)
    return f"~{s[len(_HOME):]}" if s.startswith(_HOME) else s


def _target_display(inst: Install) -> str:
    return inst.target


class _NavTree(Tree):
    """Tree with navigation bindings shown in the footer."""
    BINDINGS = [
        Binding("j", "noop", "↓", key_display="j/k", show=True),
        Binding("k", "noop", "↑", show=False),
        Binding("enter", "select_cursor", "Fold", key_display="⏎", show=True),
        Binding("space", "toggle_node", "Select", key_display="space", show=True),
        Binding("x", "noop", "Toggle", show=True),
        Binding("l", "noop", "Expand", show=True),
        Binding("h", "noop", "Collapse", show=True),
        Binding("A", "noop", "Adopt", show=True),
    ]

    def action_noop(self) -> None:
        pass  # handled by panel's on_key


class TargetPanel(Static):
    class TargetSelected(Message):
        def __init__(self, target_name: str) -> None:
            super().__init__()
            self.target_name = target_name

    class ToggleInstall(Message):
        def __init__(self, source_qname: str, source_name: str, target: str, currently_installed: bool) -> None:
            super().__init__()
            self.source_qname = source_qname
            self.source_name = source_name
            self.target = target
            self.currently_installed = currently_installed

    class AdoptOrphan(Message):
        def __init__(self, orphan_name: str, orphan_path: Path, target: str) -> None:
            super().__init__()
            self.orphan_name = orphan_name
            self.orphan_path = orphan_path
            self.target = target

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(" Targets", classes="panel-title")
            t = _NavTree("", id="tgt-tree")
            t.show_root = False
            yield t

    _selected_target: str = ""
    _last_rebuild: float = 0

    def refresh_data(self, all_targets, all_sources, items=None):
        self._targets = all_targets
        self._all_sources = all_sources
        self._items = items
        self._active_source = None
        self._build_tree()

    def refresh_preserving_state(self, pending=None) -> None:
        """Re-render with updated counts but preserve expand state."""
        import time
        from skill_manager.tui.widgets.tree_utils import save_expand_state, restore_expand_state
        tree = self.query_one("#tgt-tree", Tree)
        state = save_expand_state(tree)
        if self._active_source:
            from skill_manager.core.deployer import get_installs_for_item
            installs = get_installs_for_item(self._active_source, self._targets, self._all_sources, self._items)
            self.show_for_source(self._active_source, installs, pending)
        else:
            self._build_tree(pending)
        restore_expand_state(tree, state)
        self._last_rebuild = time.monotonic()

    def _tokens_for_target(self, installs: list[Install]) -> int:
        if not self._items:
            return 0
        items_by_qn = {i.qualified_name: i for i in self._items}
        total = 0
        seen: set[str] = set()
        for inst in installs:
            skill_name = inst.source.rsplit(":", 1)[-1]
            if skill_name in seen:
                continue
            seen.add(skill_name)
            item = items_by_qn.get(inst.source)
            if item:
                total += get_token_estimate(item)
        return total

    def _resolve_project_path(self, name: str, tgt, ti: list) -> str:
        """Get the project directory path for a target."""
        if tgt:
            # skills path could be .claude/skills (go up 2) or just skills (go up 1)
            if tgt.path.name == "skills" and tgt.path.parent.name == ".claude":
                p = tgt.path.parent.parent
            elif tgt.path.name == "skills":
                p = tgt.path.parent
            else:
                p = tgt.path.parent
            return str(p)
        if ti:
            pp = next((i.project_path for i in ti if i.project_path), "")
            if pp:
                return pp
        return str(Path.home())

    def _build_path_tree(self, all_names, pending=None) -> dict:
        """Build a nested dict: path_component → children or leaf targets."""
        items, targets = self._items, self._targets
        home = str(Path.home())

        # Collect (relative_path_parts, name, tgt) for each target
        entries = []
        for name in sorted(all_names.keys()):
            tgt = all_names[name]
            ti = get_installs_for_target(name, targets, self._all_sources, items)
            proj = self._resolve_project_path(name, tgt, ti)
            rel = proj[len(home):].lstrip("/") if proj.startswith(home) else proj
            parts = tuple(rel.split("/")) if rel else ()
            entries.append((parts, name, tgt))

        # Group by path depth: find which path prefixes have multiple targets
        # to decide where to create intermediate nodes
        from collections import defaultdict
        by_prefix: dict[tuple, list] = defaultdict(list)
        for parts, name, tgt in entries:
            # Register at each prefix level
            for i in range(len(parts)):
                by_prefix[parts[:i+1]].append(name)
            if not parts:
                by_prefix[()].append(name)

        return entries

    def _build_tree(self, pending=None) -> None:
        items, targets = self._items, self._targets
        tree = self.query_one("#tgt-tree", Tree)
        tree.clear()
        tree.root.expand()

        all_names: dict[str, TargetConfig | None] = {n: t for n, t in targets.items()}
        unified = all_installs(targets, self._all_sources, items)
        for inst in unified:
            if inst.target not in all_names:
                all_names[inst.target] = None

        # Pre-compute which targets have orphans
        has_orphans: set[str] = set()
        for inst in unified:
            if inst.method == InstallMethod.ORPHAN:
                has_orphans.add(inst.target)

        home = str(Path.home())

        # Separate "user" target (home dir) from filesystem targets
        user_target: str | None = None
        entries: list[tuple[tuple[str, ...], str]] = []
        for name in sorted(all_names.keys()):
            tgt = all_names[name]
            ti = get_installs_for_target(name, targets, self._all_sources, items)
            proj = self._resolve_project_path(name, tgt, ti)
            if proj == home or proj == home + "/":
                user_target = name
                continue
            rel = proj[len(home):].lstrip("/") if proj.startswith(home) else proj
            parts = tuple(rel.split("/")) if rel else ()
            parent_parts = parts[:-1] if parts else ()
            entries.append((parent_parts, name))

        def _counts(names):
            all_inst = []
            for n in names:
                all_inst.extend(get_installs_for_target(n, targets, self._all_sources, items))
            ni = len(all_inst)
            tok = self._tokens_for_target(all_inst)
            tok_str = f" [dim]{tok}t[/dim]" if tok else ""
            cnt = f" [green]{ni}[/green]" if ni else ""
            return cnt, tok_str

        def _all_names_under(node):
            names = list(node.get("_targets", []))
            for k, v in node.items():
                if k != "_targets" and isinstance(v, dict):
                    names.extend(_all_names_under(v))
            return names

        def _siblings_need_pad(names):
            return any(n in has_orphans for n in names)

        def _add_node(parent_tree_node, label, trie_node, expand=True):
            all_n = _all_names_under(trie_node)
            cnt, tok = _counts(all_n)
            tree_node = parent_tree_node.add(f"[bold]{label}[/bold]{cnt}{tok}", expand=expand)

            # Add direct targets — pad if any sibling has orphans
            direct = trie_node.get("_targets", [])
            pad = _siblings_need_pad(direct)
            for name in direct:
                self._target_leaf(tree_node, name, all_names.get(name), pending, pad=pad)

            # Add child directories
            children = {k: v for k, v in trie_node.items() if k != "_targets" and isinstance(v, dict)}
            for child_key in sorted(children.keys()):
                child = children[child_key]

                # Collapse single-child chains: if a dir has no targets and only one subdir, merge
                collapsed_key = child_key
                collapsed_child = child
                while (not collapsed_child.get("_targets")
                       and sum(1 for k in collapsed_child if k != "_targets" and isinstance(collapsed_child[k], dict)) == 1):
                    sub_key = next(k for k in collapsed_child if k != "_targets" and isinstance(collapsed_child[k], dict))
                    collapsed_key = f"{collapsed_key}/{sub_key}"
                    collapsed_child = collapsed_child[sub_key]

                _add_node(tree_node, collapsed_key, collapsed_child)

        # User scope — single line at root
        if user_target:
            self._target_leaf(tree.root, user_target, all_names.get(user_target), pending,
                              display_override="User scope (~/.claude)")

        # Build trie for project targets
        trie: dict = {"_targets": []}
        for parts, name in entries:
            node = trie
            for p in parts:
                if p not in node:
                    node[p] = {"_targets": []}
                node = node[p]
            node["_targets"].append(name)

        # Project scope — label + children directly under it
        all_project_names = _all_names_under(trie)
        if all_project_names:
            proj_cnt, proj_tok = _counts(all_project_names)
            proj_icon = "[green]●[/green]" if proj_cnt else "[dim]○[/dim]"
            proj_node = tree.root.add(f"{proj_icon} [bold]Project scope[/bold]{proj_cnt}{proj_tok}",
                                      expand=True, allow_expand=False)

            direct = trie.get("_targets", [])
            pad = _siblings_need_pad(direct)
            for name in direct:
                self._target_leaf(proj_node, name, all_names.get(name), pending, pad=pad)

            children = {k: v for k, v in trie.items() if k != "_targets" and isinstance(v, dict)}
            for child_key in sorted(children.keys()):
                child = children[child_key]
                collapsed_key = child_key
                collapsed_child = child
                while (not collapsed_child.get("_targets")
                       and sum(1 for k in collapsed_child if k != "_targets" and isinstance(collapsed_child[k], dict)) == 1):
                    sub_key = next(k for k in collapsed_child if k != "_targets" and isinstance(collapsed_child[k], dict))
                    collapsed_key = f"{collapsed_key}/{sub_key}"
                    collapsed_child = collapsed_child[sub_key]

                _add_node(proj_node, collapsed_key, collapsed_child)

    def _display_name_for(self, name, tgt, ti):
        """Get display name for a target."""
        proj = self._resolve_project_path(name, tgt, ti)
        folder = Path(proj).name
        return folder if folder and folder != name else name

    def _target_leaf(self, parent, name, tgt, pending=None, display_override=None, pad=False):
        ti = get_installs_for_target(name, self._targets, self._all_sources, self._items)
        display_name = display_override or self._display_name_for(name, tgt, ti)

        n_s = sum(1 for i in ti if i.method == InstallMethod.SYMLINK)
        n_p = sum(1 for i in ti if i.method == InstallMethod.PLUGIN)
        n_o = sum(1 for i in ti if i.method == InstallMethod.ORPHAN)

        # Count pending changes for this target
        n_pend_this = sum(1 for c in (pending.installs if pending else []) if c.target == name)
        n_pend_this += sum(1 for c in (pending.uninstalls if pending else []) if c.target == name)

        parts = []
        if n_s:
            parts.append(f"[green]{n_s}[/green]")
        if n_p:
            parts.append(f"[blue]{n_p}[/blue]")
        if n_o:
            parts.append(f"[yellow]{n_o}?[/yellow]")
        count = f" {'+'.join(parts)}" if parts else ""

        # Show pending indicator
        pend_mark = ""
        if n_pend_this:
            n_add = sum(1 for c in pending.installs if c.target == name)
            n_rm = sum(1 for c in pending.uninstalls if c.target == name)
            pend_parts = []
            if n_add:
                pend_parts.append(f"[green]+{n_add}[/green]")
            if n_rm:
                pend_parts.append(f"[red]-{n_rm}[/red]")
            pend_mark = f" {' '.join(pend_parts)}"

        tok = self._tokens_for_target(ti)
        tok_str = f" [dim]{tok}t[/dim]" if tok else ""

        icon = "[green]●[/green]" if (len(ti) > 0 or n_pend_this) else "[dim]○[/dim]"
        sel = "[bold reverse] " if name == self._selected_target else ""
        sel_end = " [/bold reverse]" if sel else ""
        label = f"{icon} {sel}{display_name}{sel_end}{count}{pend_mark}{tok_str}"

        orphans = [i for i in ti if i.method == InstallMethod.ORPHAN]
        if orphans:
            node = parent.add(label, data=("target", name), expand=False)
            for o in sorted(orphans, key=lambda i: i.name):
                is_dir_orphan = o.symlink == Path()
                adopt_hint = " [dim]A=adopt[/dim]" if is_dir_orphan else ""
                node.add_leaf(
                    f"[dark_orange]? {o.name}[/dark_orange]{adopt_hint}",
                    data=("orphan", o.name, o.origin, o.symlink, name),
                )
        else:
            prefix = "  " if pad else ""
            parent.add_leaf(f"{prefix}{label}", data=("target", name))

    # ── Source selected → toggle mode ─────────────────────────

    def show_for_source(self, item, installs, pending=None):
        self._active_source = item
        installed = {i.target for i in installs}

        tree = self.query_one("#tgt-tree", Tree)
        tree.clear()
        tree.root.expand()

        all_names: dict[str, TargetConfig | None] = {n: t for n, t in self._targets.items()}
        unified = all_installs(self._targets, self._all_sources, self._items)
        for inst in unified:
            if inst.target not in all_names:
                all_names[inst.target] = None

        def _sec_cnt(names):
            n_eff = 0
            has_p = False
            for name in names:
                is_i = name in installed
                pa = pending.is_pending(item.qualified_name, name) if pending else None
                if pa:
                    has_p = True
                if pa == "install" or (not pa and is_i):
                    n_eff += 1
            color = "yellow" if has_p else ("green" if n_eff else "dim")
            return f" [{color}]{n_eff}/{len(names)}[/{color}]"

        hint = "[dim]space/x toggle[/dim]"

        # Separate USER target and build trie
        home = str(Path.home())
        user_target: str | None = None
        trie: dict = {"_targets": []}
        for name in sorted(all_names.keys()):
            tgt_cfg = all_names[name]
            ti = get_installs_for_target(name, self._targets, self._all_sources, self._items)
            proj = self._resolve_project_path(name, tgt_cfg, ti)
            if proj == home or proj == home + "/":
                user_target = name
                continue
            rel = proj[len(home):].lstrip("/") if proj.startswith(home) else proj
            parts = rel.split("/") if rel else []
            parent_parts = parts[:-1] if parts else []
            node = trie
            for p in parent_parts:
                if p not in node:
                    node[p] = {"_targets": []}
                node = node[p]
            node["_targets"].append(name)

        def _all_under(n):
            names = list(n.get("_targets", []))
            for k, v in n.items():
                if k != "_targets" and isinstance(v, dict):
                    names.extend(_all_under(v))
            return names

        def _add(parent_node, label, trie_node):
            all_n = _all_under(trie_node)
            section = parent_node.add(f"[bold]{label}[/bold]{_sec_cnt(all_n)} {hint}", expand=True)
            for name in trie_node.get("_targets", []):
                is_inst = name in installed
                pa = pending.is_pending(item.qualified_name, name) if pending else None
                self._toggle_leaf(section, name, all_names[name], is_inst, pa)
            children = {k: v for k, v in trie_node.items() if k != "_targets" and isinstance(v, dict)}
            for ck in sorted(children.keys()):
                cc = children[ck]
                ckey = ck
                while not cc.get("_targets") and sum(1 for k in cc if k != "_targets" and isinstance(cc[k], dict)) == 1:
                    sk = next(k for k in cc if k != "_targets" and isinstance(cc[k], dict))
                    ckey = f"{ckey}/{sk}"
                    cc = cc[sk]
                _add(section, ckey, cc)

        # User scope — single line at root
        if user_target:
            is_inst = user_target in installed
            pa = pending.is_pending(item.qualified_name, user_target) if pending else None
            self._toggle_leaf(tree.root, user_target, all_names.get(user_target), is_inst, pa,
                              display_override="User scope (~/.claude)")

        # Project scope — label + children directly under it
        all_proj = _all_under(trie)
        if all_proj:
            proj_has = any(n in installed for n in all_proj) or (pending and any(pending.is_pending(item.qualified_name, n) == "install" for n in all_proj))
            proj_icon = "[green]●[/green]" if proj_has else "[dim]○[/dim]"
            proj_sec = tree.root.add(f"{proj_icon} [bold]Project scope[/bold]{_sec_cnt(all_proj)} {hint}",
                                     expand=True, allow_expand=False)
            for name in trie.get("_targets", []):
                is_inst = name in installed
                pa = pending.is_pending(item.qualified_name, name) if pending else None
                self._toggle_leaf(proj_sec, name, all_names[name], is_inst, pa)
            children = {k: v for k, v in trie.items() if k != "_targets" and isinstance(v, dict)}
            for ck in sorted(children.keys()):
                cc = children[ck]
                ckey = ck
                while not cc.get("_targets") and sum(1 for k in cc if k != "_targets" and isinstance(cc[k], dict)) == 1:
                    sk = next(k for k in cc if k != "_targets" and isinstance(cc[k], dict))
                    ckey = f"{ckey}/{sk}"
                    cc = cc[sk]
                _add(proj_sec, ckey, cc)

    def _toggle_leaf(self, parent, name, tgt, is_installed, pending_action, display_override=None):
        ti = get_installs_for_target(name, self._targets, self._all_sources, self._items)
        display_name = display_override or self._display_name_for(name, tgt, ti)

        tok = self._tokens_for_target(ti)
        tok_str = f" [dim]{tok}t[/dim]" if tok else ""

        if pending_action == "install":
            cb = "[on green] ● [/on green]"
            src_tok = get_token_estimate(self._active_source) if self._active_source else 0
            mark = f" [green]← install +{src_tok}t[/green]" if src_tok else " [green]← install[/green]"
        elif pending_action == "uninstall":
            cb = "[on red] ○ [/on red]"
            src_tok = get_token_estimate(self._active_source) if self._active_source else 0
            mark = f" [red]← uninstall -{src_tok}t[/red]" if src_tok else " [red]← uninstall[/red]"
        elif is_installed:
            cb = "[green] ● [/green]"
            mark = ""
        else:
            cb = "[dim] ○ [/dim]"
            mark = ""

        sel = "[bold reverse] " if name == self._selected_target else ""
        sel_end = " [/bold reverse]" if sel else ""
        parent.add_leaf(f"{cb} {sel}{display_name}{sel_end}{tok_str}{mark}", data=("toggle", name, is_installed))

    # ── Events ────────────────────────────────────────────────

    def _fire_toggle(self, data) -> None:
        if self._active_source:
            item = self._active_source
            # For CC plugins, use plugin name instead of skill name
            from skill_manager.core.inventory import is_plugin_source
            if is_plugin_source(item.source_name) or item.source_name.startswith("mp:"):
                display = item.plugin_name or item.name
            else:
                display = item.name
            self.post_message(self.ToggleInstall(
                source_qname=item.qualified_name,
                source_name=display,
                target=data[1], currently_installed=data[2],
            ))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Enter is handled by on_key — nothing to do here."""
        pass

    def on_key(self, event) -> None:
        tree = self.query_one("#tgt-tree", Tree)

        # A → adopt orphan (only on real-directory orphan nodes)
        if event.key == "A":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "orphan":
                _, name, origin, symlink, target = node.data
                if symlink == Path():  # real directory, not a broken symlink
                    event.prevent_default()
                    event.stop()
                    self.post_message(self.AdoptOrphan(name, origin, target))
                    return

        # x → toggle install/uninstall (toggle mode only)
        if event.key == "x":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] == "toggle" and self._active_source:
                event.prevent_default()
                event.stop()
                self._fire_toggle(node.data)
                return

        # Space → select/navigate (switch to toggle mode on other panel)
        if event.key == "space":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple):
                data = node.data
                if data[0] in ("target", "toggle"):
                    event.prevent_default()
                    event.stop()
                    self._selected_target = data[1]
                    self.post_message(self.TargetSelected(data[1]))
                return

        # Enter → expand/collapse (only on nodes that allow it)
        if event.key == "enter":
            node = tree.cursor_node
            if node and node.allow_expand:
                event.prevent_default()
                event.stop()
                if node.is_expanded:
                    node.collapse()
                else:
                    node.expand()
                return

        # L → expand all under cursor
        if event.key == "L":
            node = tree.cursor_node
            if node:
                event.prevent_default()
                event.stop()
                node.expand_all()
                return

        # H → collapse all under cursor (only if collapsable)
        if event.key == "H":
            node = tree.cursor_node
            if node and node.allow_expand:
                event.prevent_default()
                event.stop()
                node.collapse_all()
                return

        # l → expand
        if event.key == "l":
            node = tree.cursor_node
            if node and node.children:
                event.prevent_default()
                event.stop()
                node.expand()
                return

        # h → collapse if expanded (and collapsable), otherwise go to parent
        if event.key == "h":
            node = tree.cursor_node
            if node and node.is_expanded and node.children and node.allow_expand:
                event.prevent_default()
                event.stop()
                node.collapse()
                return
            event.prevent_default()
            event.stop()
            tree.action_cursor_parent()
            return

        vi = {"j": "cursor_down", "k": "cursor_up"}
        if event.key in vi:
            event.prevent_default()
            event.stop()
            getattr(tree, f"action_{vi[event.key]}")()
