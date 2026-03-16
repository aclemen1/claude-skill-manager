"""Tree expand state save/restore utilities."""

from __future__ import annotations


def save_expand_state(tree) -> dict[tuple[int, ...], bool]:
    """Save expand state keyed by position path (indices)."""
    state: dict[tuple[int, ...], bool] = {}
    _walk_save(tree.root, (), state)
    return state


def _walk_save(node, path: tuple[int, ...], state: dict[tuple[int, ...], bool]) -> None:
    if node.children:
        state[path] = node.is_expanded
    for i, child in enumerate(node.children):
        _walk_save(child, path + (i,), state)


def restore_expand_state(tree, state: dict[tuple[int, ...], bool]) -> None:
    """Restore expand state from position-based dict."""
    if not state:
        return
    _walk_restore(tree.root, (), state)


def _walk_restore(node, path: tuple[int, ...], state: dict[tuple[int, ...], bool]) -> None:
    if path in state:
        if state[path]:
            node.expand()
        else:
            node.collapse()
    for i, child in enumerate(node.children):
        _walk_restore(child, path + (i,), state)
