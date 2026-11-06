"""``dotnet``'s output as diagnostics (SPEC §11.3): MSBuild's canonical error format.

The compiler, analyzers, the SDK, NuGet and MSBuild itself all print
``<origin>(<line>,<col>): error <CODE>: <message> [<project>]`` (or ``<origin> : error …`` with
no position), and MSBuild repeats a warning once per project that sees it. Each is kept once.
A failed command without any error lines gives one build-system diagnostic with its last lines.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from codecoremorph.core.models import Diagnostic, DiagnosticOrigin, Severity

_CANONICAL = re.compile(
    r"^\s*(?P<origin>.+?)(?:\((?P<line>\d+)(?:,(?P<col>\d+))?(?:,\d+(?:,\d+)?)?\))?\s*:\s*"
    r"(?P<level>error|warning)\s+(?P<code>[A-Za-z]+\d+)\s*:\s*(?P<message>.*?)"
    r"(?:\s+\[(?P<project>[^\[\]]+)\])?\s*$"
)
_BUILD_SYSTEM_CODES = ("MSB", "NETSDK", "NU", "BC")
_ANALYZER_CODES = ("CA", "IDE", "RS")
MAX_BUILD_LINES = 15


class PathMap:
    """Maps the paths ``dotnet`` prints in the mirror back to worktree paths."""

    def __init__(self, roots: Sequence[str], *, case_insensitive: bool):
        self.case_insensitive = case_insensitive
        self.roots = [self._norm(r).rstrip("/") + "/" for r in roots if r]

    def _norm(self, path: str) -> str:
        text = path.replace("\\", "/")
        return text.lower() if self.case_insensitive else text

    def relative(self, path: str) -> str | None:
        text = path.replace("\\", "/")
        probe = text.lower() if self.case_insensitive else text
        for root in self.roots:
            if probe.startswith(root):
                return text[len(root) :]
        return None


def _origin(code: str) -> DiagnosticOrigin:
    upper = code.upper()
    if upper.startswith(_BUILD_SYSTEM_CODES):
        return DiagnosticOrigin.BUILD_SYSTEM
    if upper.startswith(_ANALYZER_CODES):
        return DiagnosticOrigin.ANALYZER
    return DiagnosticOrigin.COMPILER  # CS, SYSLIB and the like


def parse_dotnet_output(output: str, paths: PathMap) -> list[Diagnostic]:
    """The diagnostics in ``output``, once each, in order. A position-less origin that is a
    tool's name (``CSC``, ``MSBUILD``) or a project file has no line; one outside the mirror (an
    SDK targets file) keeps its absolute path."""
    found: list[Diagnostic] = []
    seen: set[tuple[str | None, int | None, int | None, str, str]] = set()
    for raw in output.splitlines():
        match = _CANONICAL.match(raw.rstrip())
        if match is None:
            continue
        origin = match.group("origin").strip()
        if not origin or origin.lower().startswith(("build ", "time elapsed")):
            continue
        has_position = match.group("line") is not None
        rel = paths.relative(origin)
        file_path: str | None = rel if rel is not None else origin
        if not has_position and re.fullmatch(r"[A-Za-z]+", origin):
            file_path = None  # "CSC : error CS2001", "MSBUILD : error MSB1009"
        line = int(match.group("line")) if has_position else None
        column = int(match.group("col")) if match.group("col") else None
        code = match.group("code")
        message = match.group("message").strip()
        key = (file_path, line, column, code, message)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Diagnostic(
                file_path=file_path,
                line=max(line, 1) if line is not None else None,
                column=max(column, 1) if column is not None else None,
                severity=Severity.ERROR if match.group("level") == "error" else Severity.WARNING,
                code=code,
                message=message,
                origin=_origin(code),
            )
        )
    return found


def build_failure(output: str, step: str, log: str) -> Diagnostic:
    """One build-system diagnostic for a failed ``dotnet`` command without error lines, with
    its last lines."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    shown = lines[-MAX_BUILD_LINES:]
    message = f"dotnet's {step} failed" + (":\n" + "\n".join(shown) if shown else f"; see {log}")
    if len(lines) > len(shown):
        message += f"\n[the full output is in {log}]"
    return Diagnostic(
        file_path=None,
        severity=Severity.ERROR,
        message=message,
        origin=DiagnosticOrigin.BUILD_SYSTEM,
    )
