"""Maven's output as diagnostics (SPEC §11.2): javac's messages and Maven's own errors.

javac, through the compiler plugin, prints ``[ERROR] <path>:[<line>,<col>] <message>`` and
``[WARNING] …`` lines, with indented ``symbol:`` and ``location:`` lines after some; Maven
repeats the errors in its failure summary, so each is kept once. A failed build without
compiler errors (a POM it can't read, a dependency it can't resolve, a plugin that failed)
gives one build-system diagnostic with Maven's own error lines.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from codecoremorph.core.models import Diagnostic, DiagnosticOrigin, Severity

_JAVAC = re.compile(
    r"^\[(?P<level>ERROR|WARNING)\]\s+(?P<path>.+?\.java):\[(?P<line>\d+),(?P<col>\d+)\]\s?"
    r"(?P<message>.*)$"
)
_DETAIL = re.compile(r"^(?:\[ERROR\]|\[WARNING\])?\s{2,}(?P<text>\S.*)$")
_ERROR = re.compile(r"^\[(?:ERROR|FATAL)\]\s?(?P<text>.*)$")
_POM_PROBLEM = re.compile(r"@ line (?P<line>\d+), column (?P<col>\d+)")
_BOILERPLATE = (
    "-> [Help",
    "To see the full stack trace",
    "Re-run Maven using",
    "For more information about the errors",
    "[Help 1]",
    "After correcting the problems",
    "mvn <args> -rf",
)
MAX_BUILD_LINES = 15


class PathMap:
    """Maps the paths Maven prints in the mirror back to worktree paths."""

    def __init__(self, roots: Sequence[str], *, case_insensitive: bool):
        self.case_insensitive = case_insensitive
        self.roots = [self._norm(r).rstrip("/") + "/" for r in roots if r]

    def _norm(self, path: str) -> str:
        text = path.replace("\\", "/")
        if re.match(r"^/[A-Za-z]:/", text):
            text = text[1:]  # javac's /E:/x on Windows
        return text.lower() if self.case_insensitive else text

    def relative(self, path: str) -> str | None:
        text = path.replace("\\", "/")
        if re.match(r"^/[A-Za-z]:/", text):
            text = text[1:]
        probe = text.lower() if self.case_insensitive else text
        for root in self.roots:
            if probe.startswith(root):
                return text[len(root) :]
        return None


def parse_maven_output(output: str, paths: PathMap) -> list[Diagnostic]:
    """javac's diagnostics in ``output``, once each, in order. Paths outside the mirror (a
    generated source in ``target/``) keep the absolute path."""
    found: list[Diagnostic] = []
    seen: set[tuple[str | None, int, int, str]] = set()
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        match = _JAVAC.match(lines[i].rstrip())
        i += 1
        if match is None:
            continue
        details: list[str] = []
        while i < len(lines) and (detail := _DETAIL.match(lines[i].rstrip())) is not None:
            if _JAVAC.match(lines[i].rstrip()):
                break
            details.append(detail.group("text"))
            i += 1
        message = match.group("message").strip()
        if details:
            message = message + "\n" + "\n".join(details)
        rel = paths.relative(match.group("path"))
        line, column = int(match.group("line")), int(match.group("col"))
        key = (rel or match.group("path"), line, column, message)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Diagnostic(
                file_path=rel or match.group("path"),
                line=max(line, 1),
                column=max(column, 1),
                severity=Severity.ERROR if match.group("level") == "ERROR" else Severity.WARNING,
                message=message,
                origin=DiagnosticOrigin.COMPILER,
            )
        )
    return found


def maven_errors(output: str) -> list[str]:
    """Maven's own ``[ERROR]`` lines, without its boilerplate and the compiler errors."""
    kept: list[str] = []
    for line in output.splitlines():
        match = _ERROR.match(line.rstrip())
        if match is None:
            continue
        text = match.group("text").strip()
        if not text or any(b in text for b in _BOILERPLATE) or _JAVAC.match(line.rstrip()):
            continue
        if text.startswith(("symbol:", "location:", "required:", "found:", "reason:")):
            continue
        if text not in kept:
            kept.append(text)
    return kept


def build_failure(output: str, step: str, log: str) -> Diagnostic:
    """One build-system diagnostic for a failed Maven run, with Maven's error lines (the POM
    line when Maven names one)."""
    errors = maven_errors(output)
    shown = errors[:MAX_BUILD_LINES]
    if len(errors) > len(shown):
        shown.append(f"[{len(errors) - len(shown)} more lines in {log}]")
    pom_line: int | None = None
    for text in errors:
        if (where := _POM_PROBLEM.search(text)) is not None:
            pom_line = int(where.group("line"))
            break
    message = f"Maven's {step} failed" + (":\n" + "\n".join(shown) if shown else f"; see {log}")
    return Diagnostic(
        file_path="pom.xml" if pom_line is not None else None,
        line=pom_line,
        severity=Severity.ERROR,
        message=message,
        origin=DiagnosticOrigin.BUILD_SYSTEM,
    )
