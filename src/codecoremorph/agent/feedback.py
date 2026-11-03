"""Feedback the agent gets after a failed attempt (FR-4.2, FR-4.6, SPEC §7.6).

Compiler feedback:
- deduplicates on (file, line, code);
- lists errors before warnings, and the first error of each file before the rest, because
  later errors are usually cascades;
- shows each diagnostic's notes and the source around it (±3 lines);
- caps the list at 20 items and about 4k tokens, always stating the total;
- lists errors in files outside the unit separately, as likely caused by the unit's changes.

Everything here is deterministic (it becomes part of replayed prompts) and uses worktree-
relative paths only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.models import (
    Diagnostic,
    RuleSite,
    Severity,
    TestCaseResult,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile

MAX_ITEMS = 20
MAX_CHARS = 16_000  # about 4k tokens
CONTEXT_LINES = 3
MAX_TEST_MESSAGE = 1_500


@dataclass(frozen=True)
class _Source:
    worktree: Path
    cache: dict[str, list[str] | None]
    roots: tuple[str, ...] = ()

    def lines(self, path: str) -> list[str] | None:
        if path not in self.cache:
            try:
                text = TextFile.decode((self.worktree / path).read_bytes()).text
                lines = text.replace("\r\n", "\n").split("\n")
                if lines and lines[-1] == "":
                    lines.pop()  # the file's final newline, not a line
                self.cache[path] = lines
            except (OSError, BinaryFileError):
                self.cache[path] = None
        return self.cache[path]

    def snippet(self, path: str, line: int) -> str | None:
        lines = self.lines(path)
        if lines is None or not 1 <= line <= len(lines):
            return None
        first = max(1, line - CONTEXT_LINES)
        last = min(len(lines), line + CONTEXT_LINES)
        width = len(str(last))
        return "\n".join(
            f"{'>' if n == line else ' '} {n:>{width}} | {lines[n - 1]}"
            for n in range(first, last + 1)
        )


# An absolute path (Windows or POSIX) of at least two components.
_ABSOLUTE_PATH = re.compile(
    r"(?<![\w./\\])(?:[A-Za-z]:[\\/]|/)(?:[^\s:'\"<>|\\/]+[\\/])*"
    r"([^\s:'\"<>|\\/]+[\\/][^\s:'\"<>|\\/]+)"
)


def scrub_paths(text: str, roots: Sequence[str] = ()) -> str:
    """Make paths under ``roots`` (the worktree, as the host and the sandbox see it) relative,
    and shorten every other absolute path (toolchain headers) to its last two parts.

    Prompts then hold no user names or machine layouts, and stay the same across machines
    and sandboxes, which replayed cassettes rely on.
    """
    for root in roots:
        for spelling in {root.rstrip("/\\"), root.rstrip("/\\").replace("\\", "/")}:
            if spelling:
                text = text.replace(spelling + "/", "").replace(spelling + "\\", "")
    return _ABSOLUTE_PATH.sub(lambda m: f"<system>/{m.group(1)}", text)


def _warnings(count: int) -> str:
    if not count:
        return ""
    return f" and {count} warning{'s' if count != 1 else ''}"


def _where(d: Diagnostic) -> str:
    if d.file_path is None:
        return d.origin.value
    column = f":{d.column}" if d.column else ""
    return f"{d.file_path}:{d.line or '?'}{column}"


def _render(d: Diagnostic, source: _Source) -> str:
    code = f" [{d.code}]" if d.code else ""
    parts = [f"{_where(d)}: {d.severity.value}: {scrub_paths(d.message, source.roots)}{code}"]
    for note in d.notes[:5]:
        parts.append(f"  note: {_where(note)}: {scrub_paths(note.message, source.roots)}")
    if d.file_path is not None and d.line is not None:
        snippet = source.snippet(d.file_path, d.line)
        if snippet is not None:
            parts.append(f"```\n{snippet}\n```")
    return "\n".join(parts)


def _position(d: Diagnostic) -> tuple[object, ...]:
    """Sort key: by file (diagnostics without one, such as the linker's, last), line and
    column; of duplicates, the one with the most notes first."""
    notes = tuple((n.file_path or "", n.line or 0, n.message) for n in d.notes)
    return (
        d.file_path is None,
        d.file_path or "",
        d.line or 0,
        d.column or 0,
        d.code or "",
        d.message,
        -len(notes),
        notes,
    )


def order_diagnostics(diagnostics: Sequence[Diagnostic]) -> list[Diagnostic]:
    """Errors (each file's first error first), then warnings; deduplicated.

    The order doesn't depend on the build's: a parallel build prints each compile's output as
    it finishes, and an error in a header comes from whichever compile including it finished
    first, with that compile in its "included from" note. Diagnostics are sorted by place
    before deduplicating, so the same build gives the same feedback every time."""
    seen: set[tuple[str | None, int | None, str | None, str]] = set()
    unique: list[Diagnostic] = []
    for d in sorted(diagnostics, key=_position):
        if d.severity is Severity.NOTE:
            continue
        key = (d.file_path, d.line, d.code, d.severity.value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    errors = [d for d in unique if d.severity is Severity.ERROR]
    warnings = [d for d in unique if d.severity is Severity.WARNING]
    firsts: list[Diagnostic] = []
    files: set[str | None] = set()
    for d in errors:
        if d.file_path not in files:
            files.add(d.file_path)
            firsts.append(d)
    rest = [d for d in errors if d not in firsts]
    return firsts + rest + warnings


def compiler_feedback(
    diagnostics: Sequence[Diagnostic],
    *,
    unit_files: Sequence[str],
    worktree: Path,
    changed_files: Sequence[str] = (),
    step: str = "build",
    roots: Sequence[str] = (),
) -> str:
    unit = set(unit_files)
    ordered = order_diagnostics(diagnostics)
    # Warnings only matter in the unit's own files.
    ordered = [d for d in ordered if d.severity is Severity.ERROR or (d.file_path or "") in unit]
    inside = [d for d in ordered if d.file_path is None or d.file_path in unit]
    outside = [d for d in ordered if d.file_path is not None and d.file_path not in unit]
    errors = sum(1 for d in ordered if d.severity is Severity.ERROR)
    source = _Source(worktree, {}, (str(worktree), *roots))
    sections: list[str] = []
    shown = 0
    used = 0

    def add(items: list[Diagnostic], heading: str) -> None:
        nonlocal shown, used
        if not items:
            return
        rendered: list[str] = []
        for d in items:
            if shown >= MAX_ITEMS:
                break
            text = _render(d, source)
            if used + len(text) > MAX_CHARS and shown > 0:
                break
            rendered.append(text)
            shown += 1
            used += len(text)
        if rendered:
            sections.append(heading + "\n\n" + "\n\n".join(rendered))

    add(inside, "### In the unit's files")
    changed = ", ".join(changed_files[:5]) or "the unit's files"
    add(outside, f"### In other files, likely caused by your changes to {changed}")
    header = (
        f"The {step} failed with {errors} error{'s' if errors != 1 else ''}"
        f"{_warnings(len(ordered) - errors)}."
    )
    if shown < len(ordered):
        header += f" Showing the first {shown} of {len(ordered)}; fix these first."
    if not ordered:
        header = f"The {step} failed without a diagnostic CodeCoreMorph could read."
    return "\n\n".join([header, *sections])


def regression_feedback(
    regressions: Sequence[TestCaseResult],
    *,
    missing: Sequence[str] = (),
    roots: Sequence[str] = (),
) -> str:
    lines = [
        f"{len(regressions) + len(missing)} test(s) that passed before the migration fail now. "
        "Fix the code, not the tests."
    ]
    for case in regressions[:MAX_ITEMS]:
        lines.append(f"\n### {case.test_id}: {case.status.value}")
        if case.message:
            lines.append(scrub_paths(case.message.strip(), roots)[:MAX_TEST_MESSAGE])
        if case.stack:
            stack = scrub_paths(case.stack.strip(), roots)[:MAX_TEST_MESSAGE]
            lines.append(f"```\n{stack}\n```")
    if len(regressions) > MAX_ITEMS:
        lines.append(f"\n[{len(regressions) - MAX_ITEMS} more failing tests]")
    if missing:
        lines.append("\n### No longer run: " + ", ".join(sorted(missing)[:MAX_ITEMS]))
    return "\n".join(lines)


def sites_feedback(rule_id: str, sites: Sequence[RuleSite]) -> str:
    lines = [f"{len(sites)} {rule_id} site(s) remain in the unit's files:"]
    lines += [f"- {s.file}:{s.line}: {s.text}" for s in sites[: MAX_ITEMS * 2]]
    if len(sites) > MAX_ITEMS * 2:
        lines.append(f"[{len(sites) - MAX_ITEMS * 2} more]")
    return "\n".join(lines)
