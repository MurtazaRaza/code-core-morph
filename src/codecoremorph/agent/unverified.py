"""Code the build doesn't compile: another platform's sources, disabled targets (D-42).

The build and the tests can't check these files, so a unit can break them unnoticed: the
standard bump on Linux fixes a compile error in ``util/env_posix.cc`` while the identical line
in ``util/env_windows.cc`` stays broken. Two cheap, deterministic checks narrow the gap:

- *echoes*: lines of uncompiled files identical to lines that failed to compile. The agent is
  told about them with the build errors, and a unit that ends with echoes left gets a warning;
- the unit's changes to uncompiled files, which are reported as unverified.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.agent.context import read_text
from codecoremorph.core.models import Diagnostic, Severity

# Shorter lines (`}`, `return 0;`) repeat everywhere and say nothing about the error.
MIN_ECHO_CHARS = 12
MAX_SHOWN = 20


@dataclass(frozen=True)
class Echo:
    file: str
    line: int
    text: str


def error_lines(diagnostics: Iterable[Diagnostic], worktree: Path) -> list[str]:
    """The source lines the build's errors point at, stripped, in order, without repeats."""
    found: list[str] = []
    texts: dict[str, list[str] | None] = {}
    for d in diagnostics:
        if d.severity is not Severity.ERROR or d.file_path is None or d.line is None:
            continue
        if d.file_path not in texts:
            text = read_text(worktree, d.file_path)
            texts[d.file_path] = None if text is None else text.splitlines()
        lines = texts[d.file_path]
        if lines is None or not 1 <= d.line <= len(lines):
            continue
        line = lines[d.line - 1].strip()
        if len(line) >= MIN_ECHO_CHARS and line not in found:
            found.append(line)
    return found


def echoes(lines: Iterable[str], files: Iterable[str], worktree: Path) -> list[Echo]:
    """Lines of ``files`` identical (stripped) to one of ``lines``."""
    wanted = {line for line in lines if len(line) >= MIN_ECHO_CHARS}
    if not wanted:
        return []
    found: list[Echo] = []
    for path in sorted(files):
        text = read_text(worktree, path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped in wanted:
                found.append(Echo(path, number, stripped))
    return found


def echo_feedback(found: Sequence[Echo]) -> str:
    """The section the agent gets with build errors that repeat in uncompiled files."""
    shown = [f"- {e.file}:{e.line}: `{e.text}`" for e in found[:MAX_SHOWN]]
    if len(found) > MAX_SHOWN:
        shown.append(f"- … and {len(found) - MAX_SHOWN} more")
    return "\n".join(
        [
            "### The same code in files this build doesn't compile",
            "",
            "These lines are identical to lines that failed to compile above, in files this "
            "build configuration never compiles (another platform's sources, for example). "
            "The build can't check them, so make the same fix there and say in review_notes "
            "that those changes are unverified.",
            "",
            *shown,
        ]
    )


def unverified_warnings(
    changed: Iterable[str], uncompiled: Sequence[str], left: Sequence[Echo]
) -> list[str]:
    """Warnings for a unit that succeeded: what its verification couldn't check."""
    warnings: list[str] = []
    missed = sorted(set(changed) & set(uncompiled))
    if missed:
        warnings.append(
            f"changed files this build doesn't compile, so their changes are unverified: "
            f"{', '.join(missed)}"
        )
    if left:
        shown = ", ".join(f"{e.file}:{e.line}" for e in left[:10])
        more = f" and {len(left) - 10} more" if len(left) > 10 else ""
        warnings.append(
            f"files this build doesn't compile still have lines that failed to compile "
            f"elsewhere in this unit: {shown}{more}. Fix them the same way, or check them on a "
            "platform that builds them"
        )
    return warnings
