"""Tests for skill discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.discovery import discover_all
from skill_manager.models import ItemType, SmConfig


@pytest.fixture
def skill_source(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "time-tracker").mkdir(parents=True)
    (tmp_path / "skills" / "time-tracker" / "SKILL.md").write_text(
        "---\nname: time-tracker\ndescription: Track time\n---\n"
    )
    (tmp_path / "skills" / "shortcuts").mkdir(parents=True)
    (tmp_path / "skills" / "shortcuts" / "SKILL.md").write_text(
        "---\nname: shortcuts\ndescription: Keyboard shortcuts\n---\n"
    )
    return tmp_path


def test_discover_skills(skill_source: Path):
    # source_paths points to the parent; glob resolves "skills" which contains */SKILL.md
    config = SmConfig(
        plugins=False,
        source_paths=[str(skill_source / "skills")],
    )
    items = discover_all(config)
    assert len(items) == 2
    names = {i.name for i in items}
    assert "time-tracker" in names
    assert "shortcuts" in names
    assert all(i.item_type == ItemType.SKILL for i in items)


