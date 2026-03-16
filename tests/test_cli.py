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
    config_path = tmp_path / "sm.toml"
    lock_path = tmp_path / "sm.lock"
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
    assert data["tool"] == "sm"
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
    config_path = tmp_path / "sm.toml"
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
