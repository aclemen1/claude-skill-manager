"""Advanced discovery tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_manager.core.discovery import (
    discover_all,
    resolve_glob,
    auto_discover_source_paths,
    auto_discover_target_paths,
    invalidate_cache,
)
from skill_manager.models import SmConfig


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def skill_tree(tmp_path: Path) -> Path:
    for name in ("alpha", "beta", "gamma"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}\n---\n")
    return tmp_path


# ── resolve_glob ─────────────────────────────────────────────


def test_resolve_glob_exact(tmp_path):
    (tmp_path / "mydir").mkdir()
    result = resolve_glob(str(tmp_path / "mydir"))
    assert len(result) == 1
    assert result[0] == tmp_path / "mydir"


def test_resolve_glob_star(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / ".hidden").mkdir()
    result = resolve_glob(str(tmp_path / "*"))
    names = {p.name for p in result}
    assert "a" in names
    assert "b" in names
    assert ".hidden" not in names  # hidden dirs excluded


def test_resolve_glob_double_star(tmp_path):
    (tmp_path / "x" / "y" / "z").mkdir(parents=True)
    result = resolve_glob(str(tmp_path / "**"))
    # Should find x, x/y, x/y/z (recursive)
    assert len(result) >= 3


def test_resolve_glob_pattern(tmp_path):
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "other").mkdir()
    result = resolve_glob(str(tmp_path / "proj-*"))
    names = {p.name for p in result}
    assert names == {"proj-a", "proj-b"}


def test_resolve_glob_nonexistent():
    result = resolve_glob("/nonexistent/path/*")
    assert result == []


# ── auto_discover_source_paths ───────────────────────────────


def test_discover_skills(skill_tree):
    # skills/ contains alpha/, beta/, gamma/ each with SKILL.md
    config = SmConfig(plugins=False, source_paths=[str(skill_tree / "skills")])
    items = discover_all(config)
    assert len(items) == 3
    assert {i.name for i in items} == {"alpha", "beta", "gamma"}


def test_source_glob_star(tmp_path):
    """source_paths with * resolves to multiple directories."""
    for lib in ("lib-a", "lib-b"):
        for skill in ("s1", "s2"):
            d = tmp_path / lib / skill
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {skill}\n---\n")

    sources = auto_discover_source_paths([str(tmp_path / "*")])
    # lib-a and lib-b each have skills
    assert len(sources) >= 2


def test_auto_discover_source_exact(tmp_path):
    """Exact path (no glob) scans direct children for SKILL.md."""
    (tmp_path / "skill-x").mkdir()
    (tmp_path / "skill-x" / "SKILL.md").write_text("---\nname: skill-x\n---\n")
    sources = auto_discover_source_paths([str(tmp_path)])
    assert len(sources) == 1


# ── auto_discover_target_paths ───────────────────────────────


def test_target_exact(tmp_path):
    """Exact path finds .claude/ in that directory."""
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    targets = auto_discover_target_paths([str(tmp_path)])
    assert len(targets) == 1


def test_target_glob_star(tmp_path):
    """target_paths with * finds .claude/ in child directories."""
    (tmp_path / "proj-a" / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "proj-b" / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "no-claude").mkdir()
    targets = auto_discover_target_paths([str(tmp_path / "*")])
    assert "proj-a" in targets
    assert "proj-b" in targets
    assert "no-claude" not in targets


def test_target_glob_deep(tmp_path):
    """target_paths with ** finds .claude/ at any depth."""
    (tmp_path / "a" / "b" / ".claude" / "skills").mkdir(parents=True)
    targets = auto_discover_target_paths([str(tmp_path / "**")])
    assert "b" in targets


# ── Additional resolve_glob tests ─────────────────────────────


def test_resolve_glob_nonexistent_exact():
    """Exact path to a non-existent directory returns empty."""
    result = resolve_glob("/this/does/not/exist")
    assert result == []


def test_resolve_glob_file_not_dir(tmp_path):
    """Exact path to a file (not a dir) returns empty."""
    f = tmp_path / "afile.txt"
    f.write_text("hello")
    result = resolve_glob(str(f))
    assert result == []


def test_resolve_glob_underscore_excluded(tmp_path):
    """Directories starting with _ are excluded from glob results."""
    (tmp_path / "_internal").mkdir()
    (tmp_path / "visible").mkdir()
    result = resolve_glob(str(tmp_path / "*"))
    names = {p.name for p in result}
    assert "visible" in names
    assert "_internal" not in names


def test_resolve_glob_tilde_expansion(tmp_path, monkeypatch):
    """Tilde is expanded to home directory."""
    from skill_manager.core import discovery
    monkeypatch.setattr(discovery, "_HOME", str(tmp_path))
    (tmp_path / "mydir").mkdir()
    result = resolve_glob("~/mydir")
    assert len(result) == 1
    assert result[0] == tmp_path / "mydir"


def test_resolve_glob_question_mark(tmp_path):
    """? matches a single character."""
    (tmp_path / "ab").mkdir()
    (tmp_path / "ac").mkdir()
    (tmp_path / "abc").mkdir()
    result = resolve_glob(str(tmp_path / "a?"))
    names = {p.name for p in result}
    assert names == {"ab", "ac"}


def test_resolve_glob_nested_pattern(tmp_path):
    """Pattern like parent/*/sub matches nested structure."""
    (tmp_path / "proj-a" / "backend").mkdir(parents=True)
    (tmp_path / "proj-b" / "backend").mkdir(parents=True)
    (tmp_path / "proj-c" / "frontend").mkdir(parents=True)
    result = resolve_glob(str(tmp_path / "*/backend"))
    names = {p.parent.name for p in result}
    assert "proj-a" in names
    assert "proj-b" in names
    assert "proj-c" not in names


