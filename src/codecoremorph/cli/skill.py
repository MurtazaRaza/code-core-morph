"""``codecoremorph skill``: install the Claude Code skill that drives CodeCoreMorph (D-41).

The skill ships inside the package (``codecoremorph/skills/codecoremorph/``). Installing copies
it into a project's ``.claude/skills/`` or, with ``--user``, the user's Claude Code directory
(``$CLAUDE_CONFIG_DIR`` or ``~/.claude``). Claude Code then uses CodeCoreMorph as a tool, on the
user's own Claude plan, with no API key.

Installing also writes ``command.txt`` beside ``SKILL.md``: the absolute path of the
``codecoremorph`` that installed the skill. The skill falls back to it when ``codecoremorph``
isn't on the PATH, so a CodeCoreMorph in a virtual environment works without a global install.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stdout
from codecoremorph.core.errors import UsageError

SKILL_NAME = "codecoremorph"
COMMAND_FILE = "command.txt"

skill_app = typer.Typer(
    help="Install the Claude Code skill that runs CodeCoreMorph with Claude Code as its agent.",
    no_args_is_help=True,
)


def bundled_skill() -> Path:
    """The skill directory inside the installed package."""
    root = resources.files("codecoremorph").joinpath("skills", SKILL_NAME)
    path = Path(str(root))
    if not (path / "SKILL.md").is_file():
        raise UsageError(
            f"the bundled skill is missing from this installation ({path})",
            hint="Reinstall CodeCoreMorph.",
        )
    return path


def claude_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def installed_command() -> str:
    """How to run this CodeCoreMorph from anywhere: its launcher's absolute path, with forward
    slashes (valid in Bash and PowerShell alike)."""
    launcher = "codecoremorph.exe" if sys.platform == "win32" else "codecoremorph"
    beside = Path(sys.executable).parent / launcher
    if beside.is_file():
        return beside.resolve().as_posix()
    found = shutil.which("codecoremorph")
    if found is not None:
        return Path(found).resolve().as_posix()
    return f"{Path(sys.executable).resolve().as_posix()} -m codecoremorph"


def _same_tree(a: Path, b: Path) -> bool:
    compared = filecmp.dircmp(a, b, ignore=[*filecmp.DEFAULT_IGNORES, COMMAND_FILE])
    if compared.left_only or compared.right_only or compared.diff_files or compared.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, compared.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(_same_tree(a / d, b / d) for d in compared.common_dirs)


def install_skill(target_root: Path, *, force: bool) -> tuple[Path, bool]:
    """Copy the skill to ``<target_root>/skills/codecoremorph`` and record this installation's
    command in its ``command.txt``; True if anything changed."""
    source = bundled_skill()
    target = target_root / "skills" / SKILL_NAME
    if target.exists():
        if target.is_dir() and _same_tree(source, target):
            return target, _write_command(target)
        if not force:
            raise UsageError(
                f"a different {SKILL_NAME} skill is already installed at {target}",
                reason="it was changed, or comes from another CodeCoreMorph version",
                hint="Rerun with --force to replace it.",
            )
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _write_command(target)
    return target, True


def _write_command(target: Path) -> bool:
    path = target / COMMAND_FILE
    command = installed_command() + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == command:
        return False
    path.write_text(command, encoding="utf-8")
    return True


@skill_app.command("install")
@handle_errors
def skill_install(
    user: Annotated[
        bool,
        typer.Option("--user", help="Install for every project (into ~/.claude/skills)."),
    ] = False,
    project: Annotated[
        Path,
        typer.Option("--project", help="Project to install into (its .claude/skills)."),
    ] = Path(),
    force: Annotated[
        bool, typer.Option("--force", help="Replace a different installed version.")
    ] = False,
) -> None:
    """Install the skill so Claude Code can migrate code with CodeCoreMorph."""
    root = claude_dir() if user else project.resolve() / ".claude"
    target, changed = install_skill(root, force=force)
    console = stdout()
    if changed:
        console.print(Text.assemble(status_label("ok"), f" installed the skill at {target}"))
    else:
        console.print(Text.assemble(status_label("ok"), f" the skill is up to date at {target}"))
    command = (target / COMMAND_FILE).read_text(encoding="utf-8").strip()
    console.print(Text(f"It runs CodeCoreMorph as {command} when codecoremorph isn't on the PATH."))
    console.print(
        Text(
            "Claude Code loads it in new sessions. Ask it to migrate the project, for example: "
            '"use codecoremorph to move this project to C++20".'
        )
    )


@skill_app.command("path")
@handle_errors
def skill_path() -> None:
    """Print where the bundled skill is."""
    typer.echo(str(bundled_skill()))
