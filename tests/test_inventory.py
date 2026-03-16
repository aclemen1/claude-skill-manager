"""Tests for inventory helpers."""

from __future__ import annotations

from skill_manager.core.inventory import is_plugin_source, is_auto_source


def test_is_plugin_source():
    assert is_plugin_source("plugin:foo@bar")
    assert is_plugin_source("mp:bar")
    assert not is_plugin_source("lib")
    assert not is_plugin_source("auto:proj/skills")


def test_is_auto_source():
    assert is_auto_source("auto:proj/skills")
    assert not is_auto_source("lib")
    assert not is_auto_source("plugin:foo@bar")
