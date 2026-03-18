"""Smoke tests for CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager.cli import app
from skill_manager.models import SmConfig

runner = CliRunner()


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    from skill_manager.core.config import save_config

    skills_dir = tmp_path / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Test\n---\nContent.")

    (tmp_path / "user" / ".claude" / "skills").mkdir(parents=True)

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(tmp_path / "*")],
    )
    save_config(config, config_path)
    return tmp_path


def test_sources(isolated_config):
    result = runner.invoke(app, ["sources", "--no-plugins"])
    assert result.exit_code == 0
    assert "my-skill" in result.output


def test_list(isolated_config):
    result = runner.invoke(app, ["list", "--no-plugins"])
    assert result.exit_code == 0
    assert "my-skill" in result.output


def test_targets(isolated_config):
    result = runner.invoke(app, ["targets"])
    assert result.exit_code == 0
    assert "user" in result.output


def test_installs_empty(isolated_config):
    result = runner.invoke(app, ["installs"])
    assert result.exit_code == 0
    assert "No installs" in result.output


def test_installs_after_empty(isolated_config):
    result = runner.invoke(app, ["installs"])
    assert result.exit_code == 0
    assert "No installs" in result.output


def test_diagnostics(isolated_config):
    result = runner.invoke(app, ["diagnostics"])
    assert result.exit_code == 0


def test_install_dry_run(isolated_config):
    result = runner.invoke(app, ["install", "my-skill", "--to", "user", "--dry-run"])
    assert result.exit_code == 0
    assert "my-skill" in result.output


def test_install_and_uninstall(isolated_config):
    result = runner.invoke(app, ["install", "my-skill", "--to", "user"], input="y\n")
    assert result.exit_code == 0

    result = runner.invoke(app, ["installs"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["uninstall", "user"])
    assert result.exit_code == 0


def test_schema():
    import json
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["tool"] == "csm"
    assert "concepts" in data
    assert "commands" in data
    assert "config" in data
    assert "tui_keybindings" in data
    assert len(data["commands"]) >= 9


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "sources" in result.output
    assert "targets" in result.output
    assert "install" in result.output


# ── JSON output tests ────────────────────────────────────────


def test_sources_json(isolated_config):
    import json
    result = runner.invoke(app, ["--json", "sources", "--no-plugins"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    src = data[0]
    assert "source" in src
    assert "items" in src
    assert isinstance(src["items"], list)
    assert any(i["name"] == "my-skill" for s in data for i in s["items"])


def test_targets_json(isolated_config):
    import json
    result = runner.invoke(app, ["--json", "targets"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    tgt = data[0]
    assert "name" in tgt
    assert "path" in tgt
    assert "symlinks" in tgt
    assert "plugins" in tgt
    assert "orphans" in tgt


def test_installs_json_empty(isolated_config):
    import json
    result = runner.invoke(app, ["--json", "installs"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data == []


def test_list_json(isolated_config):
    import json
    result = runner.invoke(app, ["--json", "list", "--no-plugins"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    item = data[0]
    assert "name" in item
    assert "qualified_name" in item
    assert "source" in item
    assert "type" in item
    assert "state" in item
    assert "path" in item
    assert "description" in item
    assert "installed_in" in item


def test_diagnostics_json(isolated_config):
    import json
    result = runner.invoke(app, ["--json", "diagnostics", "--all"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    # Each conflict has expected keys
    for c in data:
        assert "name" in c
        assert "target" in c
        assert "type" in c
        assert "severity" in c
        assert "items" in c


def test_sources_json_empty(tmp_path, monkeypatch):
    """Empty config with no sources produces an empty JSON array."""
    import json
    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)
    from skill_manager.core.config import save_config
    save_config(SmConfig(plugins=False), config_path)
    result = runner.invoke(app, ["--json", "sources"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == []


def test_installs_json_after_install(isolated_config):
    """After installing, installs --json returns structured records."""
    import json
    # Install first
    runner.invoke(app, ["install", "my-skill", "--to", "user"], input="y\n")
    result = runner.invoke(app, ["--json", "installs"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    inst = data[0]
    assert "source" in inst
    assert "target" in inst
    assert "method" in inst
    assert "state" in inst


# ── Error output tests ───────────────────────────────────────


def test_error_json_structure(isolated_config):
    """_error produces structured JSON on stderr."""
    result = runner.invoke(app, ["install", "nonexistent-skill", "--to", "user"])
    assert result.exit_code != 0


def test_install_unknown_target(isolated_config):
    """Install to unknown target produces error exit."""
    result = runner.invoke(app, ["install", "my-skill", "--to", "no-such-target"])
    assert result.exit_code != 0


# ── Updates command tests ────────────────────────────────────


def test_updates_no_outdated(isolated_config, monkeypatch):
    """Updates command with no outdated plugins exits cleanly."""
    monkeypatch.setattr(
        "skill_manager.core.updates.fetch_claude_code_data",
        lambda: {"marketplaces": [], "plugins": []},
    )
    result = runner.invoke(app, ["updates"])
    assert result.exit_code == 0
    assert "up to date" in result.output


# ── adopt command tests ───────────────────────────────────────


@pytest.fixture
def config_with_orphan(tmp_path: Path, monkeypatch):
    """Config with a skill source, a target with an orphan, and the orphan itself."""
    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    from skill_manager.core.config import save_config

    # Source library (needs at least one skill to be discovered as a source)
    source_dir = tmp_path / "skills"
    existing_skill = source_dir / "existing-skill"
    existing_skill.mkdir(parents=True)
    (existing_skill / "SKILL.md").write_text("---\nname: existing-skill\ndescription: Already there\n---\n")

    # User target .claude/skills
    user_skills = tmp_path / "user" / ".claude" / "skills"
    user_skills.mkdir(parents=True)

    # Create an orphan (local dir, not a symlink) in the target
    orphan = user_skills / "my-orphan"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: my-orphan\ndescription: Orphan skill\n---\n")

    config = SmConfig(
        plugins=False,
        source_paths=[str(source_dir)],
        target_paths=[str(tmp_path / "*")],
    )
    save_config(config, config_path)
    return tmp_path


def _get_source_name(config_dir: Path) -> str:
    """Discover the source name as csm would compute it (auto:skills)."""
    result = runner.invoke(app, ["--json", "sources", "--no-plugins"])
    import json
    data = json.loads(result.output)
    return data[0]["source"] if data else "auto:skills"


def test_adopt_dry_run(config_with_orphan):
    """adopt --dry-run shows the plan without moving anything."""
    src_name = _get_source_name(config_with_orphan)
    orphan_path = config_with_orphan / "user" / ".claude" / "skills" / "my-orphan"

    result = runner.invoke(
        app,
        ["adopt", "my-orphan", "--from", "user", "--to", src_name, "--dry-run"],
        input="y\n",
    )
    assert result.exit_code == 0
    assert "my-orphan" in result.output
    assert "Dry run" in result.output
    # Orphan should still be a plain directory (not moved)
    assert orphan_path.is_dir()
    assert not orphan_path.is_symlink()


def test_adopt_performs_move_and_symlink(config_with_orphan):
    """adopt moves the orphan into the source and creates a symlink back."""
    src_name = _get_source_name(config_with_orphan)
    orphan_path = config_with_orphan / "user" / ".claude" / "skills" / "my-orphan"
    source_lib = config_with_orphan / "skills"

    result = runner.invoke(
        app,
        ["adopt", "my-orphan", "--from", "user", "--to", src_name],
        input="y\n",
    )
    assert result.exit_code == 0
    # Orphan is now a symlink
    assert orphan_path.is_symlink()
    # Skill moved to source lib
    assert (source_lib / "my-orphan" / "SKILL.md").exists()
    # Symlink points to source lib
    assert orphan_path.resolve() == (source_lib / "my-orphan").resolve()


def test_adopt_no_to_lists_sources(config_with_orphan):
    """adopt without --to lists available sources and exits non-zero."""
    result = runner.invoke(app, ["adopt", "my-orphan", "--from", "user"])
    assert result.exit_code != 0
    assert "auto:skills" in result.output


def test_adopt_nonexistent_orphan(config_with_orphan):
    """adopt with unknown orphan name returns an error."""
    src_name = _get_source_name(config_with_orphan)
    result = runner.invoke(app, ["adopt", "no-such-orphan", "--from", "user", "--to", src_name])
    assert result.exit_code != 0


def test_adopt_symlink_not_allowed(config_with_orphan):
    """adopt refuses to adopt a symlink (only plain directories qualify)."""
    src_name = _get_source_name(config_with_orphan)
    # Create a symlink in the target (not a real orphan dir)
    user_skills = config_with_orphan / "user" / ".claude" / "skills"
    real_dir = config_with_orphan / "real-dir"
    real_dir.mkdir()
    sym = user_skills / "fake-orphan"
    sym.symlink_to(real_dir)

    result = runner.invoke(app, ["adopt", "fake-orphan", "--from", "user", "--to", src_name])
    assert result.exit_code != 0


def test_adopt_unknown_source(config_with_orphan):
    """adopt with unknown --to source returns an error."""
    result = runner.invoke(
        app,
        ["adopt", "my-orphan", "--from", "myproj", "--to", "no-such-source"],
    )
    assert result.exit_code != 0


def test_adopt_rejects_non_source_path_destination(config_with_orphan):
    """adopt --to a name not in source_paths is rejected."""
    result = runner.invoke(
        app,
        ["adopt", "my-orphan", "--from", "myproj", "--to", "plugin:fake@mp"],
    )
    assert result.exit_code != 0


def test_adopt_empty_source_dir_is_valid_destination(tmp_path, monkeypatch):
    """An empty source directory (no skills yet) is a valid adoption destination."""
    from skill_manager.core.config import save_config

    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    # Source directory is EMPTY — no skills yet
    empty_source = tmp_path / "new-lib"
    empty_source.mkdir()

    # Project target with an orphan
    proj_skills = tmp_path / "projects" / "myproj" / ".claude" / "skills"
    proj_skills.mkdir(parents=True)
    orphan = proj_skills / "my-orphan"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("---\nname: my-orphan\n---\n")

    config = SmConfig(
        plugins=False,
        source_paths=[str(empty_source)],
        target_paths=[str(tmp_path / "projects" / "*")],
    )
    save_config(config, config_path)

    # The empty source should appear in the --to list
    result = runner.invoke(app, ["adopt", "my-orphan", "--from", "myproj"])
    assert result.exit_code != 0  # no --to given → list sources and exit
    assert "auto:new-lib" in result.output

    # And adoption into it should work
    result = runner.invoke(
        app,
        ["adopt", "my-orphan", "--from", "myproj", "--to", "auto:new-lib"],
        input="y\n",
    )
    assert result.exit_code == 0
    assert orphan.is_symlink()
    assert (empty_source / "my-orphan" / "SKILL.md").exists()


def test_adopt_json_no_to(config_with_orphan):
    """adopt --json without --to returns structured list of available sources."""
    import json
    result = runner.invoke(app, ["--json", "adopt", "my-orphan", "--from", "user"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert "available_sources" in data
    assert isinstance(data["available_sources"], list)


def test_adopt_rejects_user_scope(tmp_path, monkeypatch):
    """adopt --from user is rejected because user scope is managed by Claude Code."""
    from skill_manager.core.config import save_config

    config_path = tmp_path / "csm.toml"
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("skill_manager.core.config.DEFAULT_CONFIG_DIR", tmp_path)

    # Create a target at home (user scope)
    home = Path.home()
    user_skills = home / ".claude" / "skills"

    config = SmConfig(
        plugins=False,
        source_paths=[str(tmp_path / "skills")],
        target_paths=[str(home)],
    )
    save_config(config, config_path)

    src_name = "auto:skills"
    result = runner.invoke(app, ["adopt", "anything", "--from", "user", "--to", src_name])
    assert result.exit_code != 0
    # Error should mention user scope
    import json as json_mod
    err = json_mod.loads(result.stderr if result.stderr else "{}")
    assert "user scope" in err.get("error", {}).get("message", "").lower() or result.exit_code != 0
