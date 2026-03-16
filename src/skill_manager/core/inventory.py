"""Unified inventory helpers."""

from __future__ import annotations


def is_plugin_source(source_name: str) -> bool:
    return source_name.startswith("plugin:") or source_name.startswith("mp:")


def is_auto_source(source_name: str) -> bool:
    return source_name.startswith("auto:")
