#!/usr/bin/env python3
"""Create a sandbox environment and launch csm tui for demo recording.

Usage:
    uv run python scripts/demo-sandbox.py
    # or with asciinema:
    asciinema rec --cols 120 --rows 36 --command "uv run python scripts/demo-driver.py" docs/demo.cast
"""

import tempfile
from pathlib import Path


def create_sandbox() -> Path:
    """Create a realistic-looking sandbox with fake skills and projects."""
    base = Path(tempfile.mkdtemp(prefix="csm-demo-"))

    # ── Skill libraries ──────────────────────────────────────
    skills = {
        "data-tools": {
            "backtest": "Run backtesting on financial strategies",
            "fetch-data": "Fetch market data from multiple sources",
            "detect-regime": "Detect market regime changes",
            "risk-monitor": "Real-time portfolio risk monitoring",
        },
        "productivity": {
            "meeting-prep": "Prepare meeting agendas and context",
            "dream-journal": "Record and analyze dream patterns",
            "weekly-digest": "Generate weekly activity digest",
        },
        "dev-tools": {
            "code-review": "Automated code review with best practices",
            "deploy-helper": "Guide deployment to staging and production",
            "db-migrate": "Database migration assistant",
        },
    }

    for lib, lib_skills in skills.items():
        for name, desc in lib_skills.items():
            d = base / "skills" / lib / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n\n{desc}\n"
            )

    # ── Projects with .claude ────────────────────────────────
    projects = {
        "quant-lab": ["backtest", "fetch-data", "detect-regime"],
        "my-website": ["deploy-helper"],
        "daily-planner": ["meeting-prep"],
        "research-notes": [],
    }

    for proj, installed_skills in projects.items():
        claude_dir = base / "projects" / proj / ".claude" / "skills"
        claude_dir.mkdir(parents=True)
        for skill_name in installed_skills:
            for lib in skills:
                src = base / "skills" / lib / skill_name
                if src.exists():
                    (claude_dir / skill_name).symlink_to(src)
                    break

    # Add an orphan skill in quant-lab
    orphan = base / "projects" / "quant-lab" / ".claude" / "skills" / "old-strategy"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text("---\nname: old-strategy\n---\nLegacy strategy.\n")

    # User scope
    user_claude = base / "home" / ".claude" / "skills"
    user_claude.mkdir(parents=True)
    src = base / "skills" / "productivity" / "meeting-prep"
    (user_claude / "meeting-prep").symlink_to(src)

    # ── Config ───────────────────────────────────────────────
    config_dir = base / "config"
    config_dir.mkdir()
    config = config_dir / "csm.toml"
    config.write_text(f"""\
plugins = false
source_paths = ["{base}/skills/*"]
target_paths = ["{base}/home", "{base}/projects/*"]
""")

    return base, config


# ── Demo captions (shown as notifications) ───────────────────

DEMO_CAPTIONS = [
    "j/k to browse skills",
    "Tab → targets panel",
    "Space → select empty target",
    "x → toggle install",
    "Pending changes",
    "a → apply, Enter → confirm",
    "? → help",
    "s → settings",
]


def main():
    base, config_path = create_sandbox()

    import skill_manager.core.config as cfg
    cfg.DEFAULT_CONFIG_PATH = config_path
    cfg.DEFAULT_CONFIG_DIR = config_path.parent

    home = base / "home"
    from unittest.mock import patch
    with patch.object(Path, "home", return_value=home):
        from skill_manager.tui.app import SkillManagerApp

        class DemoApp(SkillManagerApp):
            """SkillManagerApp with driver-triggered captions in the header.

            Send Ctrl+G (\\x07, BEL) to advance to the next caption.
            """

            CSS = SkillManagerApp.CSS + """
            HeaderTitle { color: $warning; text-style: bold; }
            #help-container { max-height: 30; }
            #settings-container { max-height: 30; }
            """

            _caption_idx = 0

            def on_mount(self) -> None:
                super().on_mount()
                self.title = ""

            def on_key(self, event) -> None:
                if event.key == "ctrl+g":
                    event.prevent_default()
                    event.stop()
                    if self._caption_idx < len(DEMO_CAPTIONS):
                        self.title = f"▸ {DEMO_CAPTIONS[self._caption_idx]}"
                        self._caption_idx += 1
                    return
                super().on_key(event)

        app = DemoApp()
        app.run(mouse=False)


if __name__ == "__main__":
    main()
