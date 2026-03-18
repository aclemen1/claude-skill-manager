"""Left panel — Sources (S). Two sections: Marketplaces, Local."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree, Label

from skill_manager.core.budget import get_token_estimate
from skill_manager.core.deployer import get_install_state
from skill_manager.core.inventory import is_plugin_source
from skill_manager.models import (
    DiscoveredItem, Install, InstallMethod, InstallState, SmConfig, SourceConfig,
)

_HOME = str(Path.home())


def _short(p: str | Path) -> str:
    s = str(p)
    return f"~{s[len(_HOME):]}" if s.startswith(_HOME) else s


def _si(state: InstallState) -> str:
    return {"installed": "[green]●[/green]", "available": "[dim]○[/dim]", "broken": "[red]×[/red]"}[state]


def _cnt(items, all_targets, all_sources, all_items, **kw) -> str:
    n = len(items)
    if not n:
        return ""
    if "target_name" in kw and "installed_qnames" in kw:
        iq = kw["installed_qnames"]
        inames = kw.get("installed_names", set())
        ni = sum(1 for i in items if i.qualified_name in iq or i.name in inames)
        pending = kw.get("pending")
        target_name = kw["target_name"]
        if pending:
            for i in items:
                pa = pending.is_pending(i.qualified_name, target_name)
                if pa == "install" and not (i.qualified_name in iq or i.name in inames):
                    ni += 1
                elif pa == "uninstall" and (i.qualified_name in iq or i.name in inames):
                    ni -= 1
        color = "green" if ni else "dim"
        return f" [{color}]{ni}/{n}[/{color}]"
    return f" [dim]{n}[/dim]"


# ── Shared icon/label helpers ────────────────────────────────


def _strip_version_hash(qname: str) -> str:
    """Strip version hash from a qualified name.

    plugin:name@mp#ver:skill → plugin:name@mp:skill
    """
    if "#" not in qname:
        return qname
    before, after = qname.split("#", 1)
    rest = after.split(":", 1)
    return before + ":" + rest[1] if len(rest) > 1 else before


def _matches_ignoring_version(item_qname: str, installed_qnames: set) -> bool:
    """Check if item matches any install, ignoring version hashes."""
    stripped = _strip_version_hash(item_qname)
    return any(_strip_version_hash(iq) == stripped for iq in installed_qnames)


def _icon_for_toggle(is_installed: bool, pending_action: str | None) -> tuple[str, str]:
    """Return (icon, mark) for a toggle checkbox."""
    if pending_action == "install":
        return "[on green] ● [/on green]", " [green]← install[/green]"
    elif pending_action == "uninstall":
        return "[on red] ○ [/on red]", " [red]← uninstall[/red]"
    elif is_installed:
        return "[green] ● [/green]", ""
    return "[dim] ○ [/dim]", ""


class _NavTree(Tree):
    """Tree with navigation bindings shown in the footer."""
    BINDINGS = [
        Binding("j", "noop", "↓", key_display="j/k", show=True),
        Binding("k", "noop", "↑", show=False),
        Binding("enter", "select_cursor", "Fold", key_display="⏎", show=True),
        Binding("space", "toggle_node", "Select", key_display="space", show=True),
        Binding("x", "noop", "Toggle", show=True),
        Binding("p", "noop", "Preview", show=True),
        Binding("l", "noop", "Expand", show=True),
        Binding("h", "noop", "Collapse", show=True),
    ]

    def action_noop(self) -> None:
        pass  # handled by panel's on_key


class SourcePanel(Static):
    selected_item: DiscoveredItem | None = None
    _selected_qname: str = ""
    _last_rebuild: float = 0  # timestamp of last programmatic rebuild

    class ItemSelected(Message):
        def __init__(self, item: DiscoveredItem) -> None:
            super().__init__()
            self.item = item

    class ToggleInstall(Message):
        def __init__(self, source_qname: str, source_name: str, target: str, currently_installed: bool) -> None:
            super().__init__()
            self.source_qname = source_qname
            self.source_name = source_name
            self.target = target
            self.currently_installed = currently_installed

    class PreviewSkill(Message):
        def __init__(self, path: Path, editable: bool) -> None:
            super().__init__()
            self.path = path
            self.editable = editable

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(" Sources", classes="panel-title")
            t = _NavTree("", id="src-tree")
            t.show_root = False
            yield t

    _active_target: str | None = None

    def refresh_data(self, config, items, all_sources, all_targets):
        self._config = config
        self._items = items
        self._all_sources_ref = all_sources
        self._all_targets = all_targets
        self.selected_item = None
        self._selected_qname = ""
        self._active_target = None
        self._build_tree()

    def refresh_preserving_state(self, pending=None) -> None:
        import time
        from skill_manager.tui.widgets.tree_utils import save_expand_state, restore_expand_state
        tree = self.query_one("#src-tree", Tree)
        state = save_expand_state(tree)
        if self._active_target:
            target_name = self._active_target
            from skill_manager.core.deployer import get_installs_for_target
            installs = get_installs_for_target(target_name, self._all_targets, self._all_sources_ref, self._items)
            self.show_for_target(installs, target_name, pending)
        else:
            self._build_tree()
        restore_expand_state(tree, state)
        self._last_rebuild = time.monotonic()

    def _build_tree(self) -> None:
        items, config = self._items, self._config
        tree = self.query_one("#src-tree", Tree)
        tree.clear()
        tree.root.expand()

        all_sources = self._all_sources_ref or {}
        # Categorize sources
        cc_src = [s for s in all_sources if s.startswith("mp:") or s.startswith("plugin:")]
        local_src = [s for s in all_sources if s.startswith("auto:")]

        by: dict[str, list[DiscoveredItem]] = defaultdict(list)
        for item in items:
            by[item.source_name].append(item)

        hl: set[str] = set()
        if self.selected_item:
            from skill_manager.core.deployer import get_installs_for_item
            for inst in get_installs_for_item(self.selected_item, self._all_targets, self._all_sources_ref, items):
                if inst.source:
                    hl.add(inst.source)

        # Marketplaces
        if cc_src:
            all_cc = [i for s in cc_src for i in by.get(s, [])]
            c = _cnt(all_cc, self._all_targets, self._all_sources_ref, items)
            cc_node = tree.root.add(f"[bold]Marketplaces[/bold]{c}", expand=True)
            self._cc_tree(cc_node, cc_src, by, hl, expand_depth=1)

        # Local
        if local_src:
            all_local = [i for s in local_src for i in by.get(s, [])]
            c = _cnt(all_local, self._all_targets, self._all_sources_ref, items)
            local_node = tree.root.add(f"[bold]Local[/bold]{c}", expand=True)
            for sn in sorted(local_src):
                si = by.get(sn, [])
                if not si:
                    continue
                sc = _cnt(si, self._all_targets, self._all_sources_ref, items)
                has_hl = any(i.qualified_name in hl for i in si)
                snd = local_node.add(f"{_short(sn.removeprefix('auto:'))}{sc}", expand=True)
                self._leaves(snd, si, hl)

    def show_for_target(self, installs, target_name, pending=None):
        self._active_target = target_name
        self.selected_item = None
        self._selected_qname = ""

        items, config = self._items, self._config
        tree = self.query_one("#src-tree", Tree)
        tree.clear()
        tree.root.expand()

        all_sources = self._all_sources_ref or {}
        cc_src = [s for s in all_sources if s.startswith("mp:") or s.startswith("plugin:")]
        local_src = [s for s in all_sources if s.startswith("auto:")]

        by: dict[str, list[DiscoveredItem]] = defaultdict(list)
        for item in items:
            by[item.source_name].append(item)

        # Two separate sets: direct installs for this target, and user-scope plugins
        self._direct_qnames = {i.source for i in installs if i.source}
        self._user_inherited_qnames: set[str] = set()
        if target_name != "user":
            from skill_manager.core.deployer import all_installs as _all_installs
            for inst in _all_installs(self._all_targets, self._all_sources_ref, items):
                if inst.target == "user" and inst.method == InstallMethod.PLUGIN:
                    self._user_inherited_qnames.add(inst.source)

        installed_qnames = self._direct_qnames | self._user_inherited_qnames
        installed_names = {i.source.rsplit(":", 1)[-1] for i in installs if i.source}
        hl = installed_qnames

        kw = dict(pending=pending, target_name=target_name, installed_qnames=hl, installed_names=installed_names)

        hint = "[dim]space/x toggle[/dim]"

        # Marketplaces
        if cc_src:
            all_cc = [i for s in cc_src for i in by.get(s, [])]
            c = _cnt(all_cc, self._all_targets, self._all_sources_ref, items, **kw)
            cc_node = tree.root.add(f"[bold]Marketplaces[/bold]{c} {hint}", expand=True)
            self._cc_tree(cc_node, cc_src, by, hl, toggle_target=target_name, pending=pending, installed_names=installed_names, expand_depth=1)

        # Local
        if local_src:
            all_local = [i for s in local_src for i in by.get(s, [])]
            c = _cnt(all_local, self._all_targets, self._all_sources_ref, items, **kw)
            local_node = tree.root.add(f"[bold]Local[/bold]{c} {hint}", expand=True)
            for sn in sorted(local_src):
                si = by.get(sn, [])
                if not si:
                    continue
                sc = _cnt(si, self._all_targets, self._all_sources_ref, items, **kw)
                has_match = any(i.qualified_name in hl or i.name in installed_names for i in si)
                has_pending = pending and any(pending.is_pending(i.qualified_name, target_name) for i in si)
                snd = local_node.add(f"{_short(sn.removeprefix('auto:'))}{sc}", expand=True)
                self._skill_leaves(snd, si, hl, target_name, pending, installed_names)

    # ── Shared leaf/node renderers ─────────────────────────────

    def _skill_info_leaf(self, parent, item, nav_item=None):
        """Render a skill as info-only child (no toggle, no state icon).

        nav_item: if set, Enter navigates to this item instead (used for plugin children).
        Selection highlight is on the plugin node, not on individual skills.
        """
        tok = get_token_estimate(item)
        tok_str = f" [dim]{tok}t[/dim]" if tok else ""
        target = nav_item or item
        data_type = "select_plugin" if nav_item else "select"
        parent.add_leaf(f"  [dim]·[/dim] {item.name}{tok_str}", data=(data_type, target))

    def _leaves(self, parent, items, hl):
        """Render skill leaves in normal mode (state icon, selectable)."""
        for item in sorted(items, key=lambda i: i.name):
            state = get_install_state(item, self._all_targets, self._all_sources_ref, self._items)
            is_hl = item.qualified_name in hl or item.name in {n.split(":")[-1] for n in hl}
            mark = " [green]◄[/green]" if is_hl else ""
            sel = "[bold reverse] " if item.qualified_name == self._selected_qname else ""
            sel_end = " [/bold reverse]" if sel else ""
            tok = get_token_estimate(item)
            tok_str = f" [dim]{tok}t[/dim]" if tok else ""
            parent.add_leaf(f"{_si(state)} {sel}{item.name}{sel_end}{tok_str}{mark}", data=("select", item))

    def _skill_leaves(self, parent, items, iq, target, pending=None, inames=None):
        """Render skill leaves in toggle mode (checkbox, toggleable)."""
        inames = inames or set()
        direct_qn = getattr(self, "_direct_qnames", set())
        user_inherited = getattr(self, "_user_inherited_qnames", set())
        for item in sorted(items, key=lambda i: i.name):
            is_direct = (
                item.qualified_name in direct_qn
                or _matches_ignoring_version(item.qualified_name, direct_qn)
                or item.qualified_name in iq or item.name in inames
            )
            is_inherited = (
                item.qualified_name in user_inherited
                or _matches_ignoring_version(item.qualified_name, user_inherited)
            )
            tok = get_token_estimate(item)
            tok_str = f" [dim]{tok}t[/dim]" if tok else ""
            sel = "[bold reverse] " if item.qualified_name == self._selected_qname else ""
            sel_end = " [/bold reverse]" if sel else ""
            if is_inherited and not is_direct:
                # Inherited from user scope: blue dot, toggleable to add project scope
                pa = pending.is_pending(item.qualified_name, target) if pending else None
                if pa == "install":
                    i_icon = "[on green] ● [/on green]"
                    i_mark = " [green]← install[/green]"
                else:
                    i_icon = "[blue] ● [/blue]"
                    i_mark = ""
                parent.add_leaf(f"{i_icon} {sel}{item.name}{sel_end}{tok_str}{i_mark}", data=("toggle", item, False, target))
            else:
                is_i = is_direct or is_inherited
                pa = pending.is_pending(item.qualified_name, target) if pending else None
                icon, mark = _icon_for_toggle(is_i, pa)
                parent.add_leaf(f"{icon} {sel}{item.name}{sel_end}{tok_str}{mark}", data=("toggle", item, is_i, target))

    def _plugin_node(self, parent, plugin_name, marketplace_name, plugin_items, hl,
                     toggle_target=None, pending=None, installed_qnames=None,
                     expand=False):
        """Render a plugin node with icon at plugin level, skills as info children.

        Works for both normal mode (toggle_target=None) and toggle mode.
        """
        # Compute scope badge (shown in all modes)
        from skill_manager.core.deployer import get_installs_for_item
        install_targets: set[str] = set()
        for i in plugin_items:
            for inst in get_installs_for_item(i, self._all_targets, self._all_sources_ref, self._items):
                install_targets.add(inst.target)
        has_user = "user" in install_targets
        has_project = len(install_targets - {"user"}) > 0
        if has_user and has_project:
            scope_badge = " [yellow]\\[user+project][/yellow]"
        elif has_user:
            scope_badge = " [dim]\\[user][/dim]"
        elif has_project:
            scope_badge = " [dim]\\[project][/dim]"
        else:
            scope_badge = ""

        if toggle_target and installed_qnames is not None:
            # Check if installed directly in this target vs inherited from user scope
            direct_qn = getattr(self, "_direct_qnames", set())
            user_inherited = getattr(self, "_user_inherited_qnames", set())
            is_direct = any(
                i.qualified_name in direct_qn
                or _matches_ignoring_version(i.qualified_name, direct_qn)
                for i in plugin_items
            )
            is_inherited = any(
                i.qualified_name in user_inherited
                or _matches_ignoring_version(i.qualified_name, user_inherited)
                for i in plugin_items
            )
            is_installed = is_direct or is_inherited

            # Find qname for toggle action
            plugin_qname = ""
            for i in plugin_items:
                if i.source_name.startswith("plugin:"):
                    plugin_qname = i.qualified_name
                    break
            if not plugin_qname:
                plugin_qname = plugin_items[0].qualified_name if plugin_items else ""

            if is_inherited and not is_direct:
                # Inherited from user scope: blue dot, toggleable to add project scope
                pa = pending.is_pending(plugin_qname, toggle_target) if pending else None
                if pa == "install":
                    icon = "[on green] ● [/on green]"
                    mark = " [green]← install[/green]"
                else:
                    icon = "[blue] ● [/blue]"
                    mark = " [dim]via user scope[/dim]"
                data = ("toggle_plugin", plugin_qname, plugin_name, False, toggle_target)
            elif is_direct and is_inherited:
                # Both scopes: green dot, toggleable (for project scope), with badge
                pa = pending.is_pending(plugin_qname, toggle_target) if pending else None
                icon, mark = _icon_for_toggle(True, pa)
                mark = f" [yellow]\\[user+project][/yellow]{mark}"
                data = ("toggle_plugin", plugin_qname, plugin_name, True, toggle_target)
            else:
                # Direct only or not installed: normal toggle
                pa = pending.is_pending(plugin_qname, toggle_target) if pending else None
                icon, mark = _icon_for_toggle(is_installed, pa)
                data = ("toggle_plugin", plugin_qname, plugin_name, is_installed, toggle_target)
        else:
            # Normal mode: is_installed based on install state
            is_installed = any(
                get_install_state(i, self._all_targets, self._all_sources_ref, self._items) == InstallState.INSTALLED
                for i in plugin_items
            )
            icon = _si(InstallState.INSTALLED if is_installed else InstallState.AVAILABLE)
            is_hl = any(i.qualified_name in hl or i.name in {n.split(":")[-1] for n in hl} for i in plugin_items)
            mark = " [green]◄[/green]" if is_hl else ""
            mark = f"{scope_badge}{mark}"
            data = ("select_plugin", plugin_items[0]) if plugin_items else None

        tok = sum(get_token_estimate(i) for i in plugin_items)
        tok_str = f" [dim]{tok}t[/dim]" if tok else ""

        # Use first item as representative for navigation
        nav_item = plugin_items[0] if plugin_items else None

        # Plugin node is selected if any of its items matches _selected_qname
        is_sel = any(i.qualified_name == self._selected_qname for i in plugin_items)
        sel = "[bold reverse] " if is_sel else ""
        sel_end = " [/bold reverse]" if sel else ""

        label = f"{icon} {sel}[bold]{plugin_name}[/bold]{sel_end}{tok_str}{mark}"
        pnd = parent.add(label, data=data, expand=expand)

        for item in sorted(plugin_items, key=lambda i: i.name):
            self._skill_info_leaf(pnd, item, nav_item=nav_item)

    def _cc_tree(self, cc_node, cc_src, by, hl, toggle_target=None, pending=None, installed_names=None, expand_default=False, expand_depth=0):
        items = self._items
        installed_names = installed_names or set()
        kw = dict(pending=pending, target_name=toggle_target, installed_qnames=hl, installed_names=installed_names) if toggle_target else {}

        # Identify marketplaces
        mp_names: dict[str, str] = {}
        for sn in cc_src:
            if sn.startswith("mp:"):
                mp_names[sn[3:]] = sn

        for mn in sorted(mp_names):
            mp_src = mp_names[mn]

            all_items: list[DiscoveredItem] = list(by.get(mp_src, []))
            for sn in cc_src:
                if sn.startswith("plugin:") and f"@{mn}" in sn:
                    all_items.extend(by.get(sn, []))

            seen: set[str] = set()
            deduped: list[DiscoveredItem] = []
            for i in all_items:
                if i.source_name.startswith("plugin:") and i.name not in seen:
                    deduped.append(i)
                    seen.add(i.name)
            for i in all_items:
                if i.source_name.startswith("mp:") and i.name not in seen:
                    deduped.append(i)
                    seen.add(i.name)
            if not deduped:
                continue

            c = _cnt(deduped, self._all_targets, self._all_sources_ref, items, **kw) if kw else _cnt(deduped, self._all_targets, self._all_sources_ref, items)
            has_m = any(i.qualified_name in hl or i.name in installed_names for i in deduped)
            has_p = toggle_target and pending and any(pending.is_pending(i.qualified_name, toggle_target) for i in deduped)
            mp_nd = cc_node.add(f"[bold]{mn}[/bold]{c}", expand=(expand_depth >= 1) or has_m or bool(has_p))

            by_plugin: dict[str, list[DiscoveredItem]] = defaultdict(list)
            for item in deduped:
                pn = ""
                if item.plugin_name:
                    pn = item.plugin_name
                elif item.source_name.startswith("plugin:"):
                    a = item.source_name[7:]
                    if "@" in a:
                        pn = a.split("@", 1)[0]
                by_plugin[pn or "(other)"].append(item)

            for pn in sorted(by_plugin):
                pi = by_plugin[pn]
                pc = _cnt(pi, self._all_targets, self._all_sources_ref, items, **kw) if kw else _cnt(pi, self._all_targets, self._all_sources_ref, items)
                has_pm = any(i.qualified_name in hl or i.name in installed_names for i in pi)
                has_pp = toggle_target and pending and any(pending.is_pending(i.qualified_name, toggle_target) for i in pi)
                should_open = (expand_depth >= 2) or has_pm or bool(has_pp)
                self._plugin_node(mp_nd, pn, mn, pi, hl,
                                  toggle_target=toggle_target, pending=pending,
                                  installed_qnames=hl if toggle_target else None,
                                  expand=should_open)

    # ── Events ────────────────────────────────────────────────

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Enter is handled by on_key — nothing to do here."""
        pass

    def _navigate(self, item):
        self.selected_item = item
        self._selected_qname = item.qualified_name
        self._active_target = None
        self.post_message(self.ItemSelected(item))

    def _fire_toggle(self, data):
        if data[0] == "toggle_plugin":
            self.post_message(self.ToggleInstall(
                source_qname=data[1], source_name=data[2],
                target=data[4], currently_installed=data[3],
            ))
        else:
            self.post_message(self.ToggleInstall(
                source_qname=data[1].qualified_name, source_name=data[1].name,
                target=data[3], currently_installed=data[2],
            ))

    def on_key(self, event) -> None:
        tree = self.query_one("#src-tree", Tree)

        # p → preview SKILL.md
        if event.key == "p":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple):
                item = None
                if node.data[0] in ("select", "select_plugin", "toggle"):
                    item = node.data[1]
                if item and isinstance(item, DiscoveredItem):
                    skill_md = item.path / "SKILL.md"
                    if skill_md.exists():
                        editable = not (
                            item.source_name.startswith("mp:")
                            or item.source_name.startswith("plugin:")
                        )
                        event.prevent_default()
                        event.stop()
                        self.post_message(self.PreviewSkill(skill_md, editable))
                        return

        # x → toggle install/uninstall (toggle mode only)
        if event.key == "x":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple) and node.data[0] in ("toggle", "toggle_plugin"):
                event.prevent_default()
                event.stop()
                self._fire_toggle(node.data)
                return

        # Space → select/navigate (switch to toggle mode on other panel)
        if event.key == "space":
            node = tree.cursor_node
            if node and isinstance(node.data, tuple):
                data = node.data
                if data[0] in ("select", "select_plugin", "toggle"):
                    event.prevent_default()
                    event.stop()
                    self._navigate(data[1])
                elif data[0] == "toggle_plugin":
                    event.prevent_default()
                    event.stop()
                    self._navigate(data[1] if isinstance(data[1], DiscoveredItem) else
                                   next((i for i in self._items if i.qualified_name == data[1]), None))
                return

        # Enter → expand/collapse
        if event.key == "enter":
            node = tree.cursor_node
            if node:
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

        # H → collapse all under cursor
        if event.key == "H":
            node = tree.cursor_node
            if node:
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

        # h → collapse if expanded, otherwise go to parent
        if event.key == "h":
            node = tree.cursor_node
            if node and node.is_expanded and node.children:
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
