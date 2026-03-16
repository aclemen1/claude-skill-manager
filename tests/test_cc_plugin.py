"""Tests for Claude Code plugin install/uninstall delegation."""

from __future__ import annotations

import pytest

from skill_manager.core.deployer import _parse_plugin_ref


def test_parse_plugin_ref_basic():
    ref = _parse_plugin_ref("plugin:productivity@aclemen1-marketplace:gmail-adapter")
    assert ref == "productivity@aclemen1-marketplace"


def test_parse_plugin_ref_with_version():
    ref = _parse_plugin_ref("plugin:productivity@aclemen1-marketplace#2.6.1:gmail-adapter")
    assert ref == "productivity@aclemen1-marketplace"


def test_parse_plugin_ref_no_at():
    ref = _parse_plugin_ref("plugin:something:skill")
    assert ref is None


def test_parse_plugin_ref_mp():
    ref = _parse_plugin_ref("mp:marketplace:skill")
    assert ref is None


def test_parse_plugin_ref_local():
    ref = _parse_plugin_ref("lib:my-skill")
    assert ref is None
