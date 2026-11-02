"""The bundled Claude Code skill and `codecoremorph skill install` (D-41)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.main import get_command
from typer.testing import CliRunner

from codecoremorph.cli import app
from codecoremorph.cli.skill import bundled_skill, installed_command

runner = CliRunner()


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md starts with YAML frontmatter"
    return yaml.safe_load(match.group(1))


def test_the_bundled_skill_is_well_formed() -> None:
    skill = bundled_skill()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    meta = _frontmatter(text)
    assert meta["name"] == "codecoremorph"
    assert "C++" in meta["description"]
    assert len(meta["description"]) <= 1024
    assert len(text.splitlines()) < 500
    for link in re.findall(r"\]\(([^)#]+)\)", text):
        assert (skill / link).is_file(), f"SKILL.md links to a missing file: {link}"


def test_every_command_the_skill_names_exists() -> None:
    """The skill must not drift from the CLI: each `codecoremorph <group> <command>` exists."""
    root = get_command(app)
    groups = root.commands  # type: ignore[attr-defined]
    skill = bundled_skill()
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(skill.rglob("*.md"))) + (
        bundled_skill().parent.parent / "prompts" / "v1" / "host_system.md"
    ).read_text(encoding="utf-8")
    named = set(re.findall(r"`codecoremorph ([a-z]+)(?: ([a-z]+))?", text))
    named |= {("unit", c) for c in re.findall(r"`unit ([a-z]+)", text)}
    assert ("unit", "submit") in named
    for group, command in sorted(named):
        assert group in groups, f"unknown command group: codecoremorph {group}"
        sub = getattr(groups[group], "commands", None)
        if sub is not None and command:
            assert command in sub, f"unknown command: codecoremorph {group} {command}"


def test_install_into_a_project_then_update(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "install", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    target = tmp_path / ".claude" / "skills" / "codecoremorph"
    assert (target / "SKILL.md").read_bytes() == (bundled_skill() / "SKILL.md").read_bytes()
    assert (target / "references" / "manifest.md").is_file()
    assert "installed the skill" in result.output

    again = runner.invoke(app, ["skill", "install", "--project", str(tmp_path)])
    assert again.exit_code == 0
    assert "up to date" in again.output

    # command.txt records this installation; another installation's path is simply replaced.
    command = (target / "command.txt").read_text(encoding="utf-8")
    assert command == installed_command() + "\n"
    (target / "command.txt").write_text("C:/elsewhere/codecoremorph.exe\n", encoding="utf-8")
    moved = runner.invoke(app, ["skill", "install", "--project", str(tmp_path)])
    assert moved.exit_code == 0, moved.output
    assert "installed the skill" in moved.output
    assert (target / "command.txt").read_text(encoding="utf-8") == command

    (target / "SKILL.md").write_text("my own notes\n", encoding="utf-8")
    refused = runner.invoke(app, ["skill", "install", "--project", str(tmp_path)])
    assert refused.exit_code == 2
    assert "--force" in refused.output
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "my own notes\n"

    forced = runner.invoke(app, ["skill", "install", "--project", str(tmp_path), "--force"])
    assert forced.exit_code == 0
    assert (target / "SKILL.md").read_bytes() == (bundled_skill() / "SKILL.md").read_bytes()
    assert (target / "command.txt").read_text(encoding="utf-8") == command


def test_the_recorded_command_runs_this_codecoremorph() -> None:
    """Regression: a skill installed from a virtual environment couldn't find `codecoremorph`
    in a Claude Code session whose PATH lacked the environment."""
    command = installed_command()
    assert " " not in Path(command).name
    launcher = Path(command.removesuffix(" -m codecoremorph"))
    assert launcher.is_absolute()
    assert launcher.is_file()
    assert "\\" not in command  # forward slashes work in Bash and PowerShell


def test_install_for_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    result = runner.invoke(app, ["skill", "install", "--user"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "claude" / "skills" / "codecoremorph" / "SKILL.md").is_file()
