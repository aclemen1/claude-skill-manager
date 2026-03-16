"""Tests for budget estimation."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.budget import estimate_item_budget, estimate_total_budget, DEFAULT_BUDGET_CHARS
from skill_manager.models import DiscoveredItem, ItemType


@pytest.fixture
def skill_with_content(tmp_path: Path) -> DiscoveredItem:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A short description\n---\n\n" + "x" * 500
    )
    return DiscoveredItem(
        name="my-skill", source_name="lib", item_type=ItemType.SKILL,
        path=skill_dir, description="A short description",
    )


def test_estimate_item_budget(skill_with_content):
    entry = estimate_item_budget(skill_with_content)
    assert entry.qualified_name == "lib:my-skill"
    assert entry.description_chars == len("A short description")
    assert entry.content_chars > 500
    assert entry.estimated_tokens > 0


def test_estimate_total_budget(skill_with_content):
    entries, total, limit = estimate_total_budget([skill_with_content])
    assert len(entries) == 1
    assert total > 0
    assert limit == DEFAULT_BUDGET_CHARS


def test_empty_budget():
    entries, total, limit = estimate_total_budget([])
    assert entries == []
    assert total == 0
