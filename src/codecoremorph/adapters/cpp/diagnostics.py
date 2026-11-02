"""Parsers for C/C++ build output: clang and gcc, the linkers (lld, GNU ld, MSVC link), CMake
and, on a best-effort basis, MSVC (SPEC §11.1).

Every parser takes the raw text of a log and returns :class:`Diagnostic` objects with paths
made relative to the worktree. Notes and "In file included from" lines attach to the
diagnostic they belong to. Locations outside the worktree (system headers) keep their path in
the message, because ``file_path`` is always worktree-relative.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.core.models import Diagnostic, DiagnosticOrigin, Severity

_CLANG = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)? "
    r"(?P<sev>fatal error|error|warning|note|remark): (?P<msg>.*?)"
    r"(?: \[(?P<code>[^\]\s]+)\])?$"
)
_INCLUDED = re.compile(r"^(?:In file included from|\s+from) (?P<file>.+?):(?P<line>\d+)[:,]?$")
_DRIVER = re.compile(
    r"^(?:[\w./\\:-]*[/\\])?(?:clang|clang\+\+|gcc|g\+\+|cc|c\+\+|clang-tidy)(?:-\d+)?"
    r"(?:\.exe)?: (?P<sev>fatal error|error|warning): (?P<msg>.*)$"
)
_MSVC = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+)(?:,(?P<col>\d+))?\)\s*: "
    r"(?P<sev>fatal error|error|warning|note) (?P<code>[A-Z]+\d+): (?P<msg>.*)$"
)
_MSVC_LINK = re.compile(
    r"^(?P<obj>.+?) : (?P<sev>fatal error|error|warning) (?P<code>LNK\d+): (?P<msg>.*)$"
)
_LLD = re.compile(r"^(?:[\w./\\:-]*[/\\])?(?:ld\.lld|lld|ld64\.lld|lld-link)(?:\.exe)?: "
                  r"(?P<sev>error|warning): (?P<msg>.*)$")  # fmt: skip
_GNU_LD_REF = re.compile(
    r"^(?P<file>[^:\s][^:]*?):(?:\([^)]*\)|(?P<line>\d+)):\s*"
    r"(?P<msg>(?:undefined reference to|multiple definition of) .*)$"
)
_GNU_LD = re.compile(r"^(?:[\w./\\:-]*[/\\])?(?:ld|ld\.bfd|ld\.gold|collect2)(?:\.exe)?: "
                     r"(?P<msg>.*)$")  # fmt: skip
_CMAKE = re.compile(
    r"^CMake (?P<sev>Error|Warning|Deprecation Warning)(?: \(dev\))?"
    r"(?: at (?P<file>.+?):(?P<line>\d+)(?: \((?P<command>[^)]*)\))?)?:?\s*(?P<msg>.*)$"
)

_SEVERITY = {
    "fatal error": Severity.ERROR,
    "error": Severity.ERROR,
    "warning": Severity.WARNING,
    "deprecation warning": Severity.WARNING,
    "note": Severity.NOTE,
    "remark": Severity.NOTE,
}


def _code(raw: str | None) -> str | None:
    """``-Werror,-Wunused`` → ``-Wunused``; clang-tidy check names pass through."""
    if not raw:
        return None
    parts = [p for p in raw.split(",") if p and p != "-Werror"]
    return parts[-1] if parts else raw


class _Builder:
    def __init__(self, mapper: PathMapper, origin: DiagnosticOrigin, base: str | None):
        self.mapper = mapper
        self.origin = origin
        self.base = base
        self.out: list[Diagnostic] = []
        self.pending_includes: list[Diagnostic] = []
        self._seen: set[tuple[object, ...]] = set()
        # True: notes attach to the last diagnostic; False: it was a duplicate, drop them;
        # None: no diagnostic yet, so a note stands alone.
        self._attach_notes: bool | None = None

    def location(
        self, file: str | None, line: str | None, col: str | None, message: str
    ) -> tuple[str | None, int | None, int | None, str]:
        if file is None:
            return None, None, None, message
        rel = self.mapper.relative(file, self.base)
        line_no = int(line) if line else None
        col_no = int(col) if col else None
        if rel is None:
            where = f"{file}:{line}" + (f":{col}" if col else "")
            return None, None, None, f"{where}: {message}"
        return rel, line_no, col_no, message

    def add(self, diagnostic: Diagnostic) -> None:
        if diagnostic.severity is Severity.NOTE and self._attach_notes is not None:
            if self._attach_notes:
                last = self.out[-1]
                self.out[-1] = last.model_copy(update={"notes": [*last.notes, diagnostic]})
            return
        if self.pending_includes:
            diagnostic = diagnostic.model_copy(
                update={"notes": [*self.pending_includes, *diagnostic.notes]}
            )
            self.pending_includes = []
        key = (
            diagnostic.file_path,
            diagnostic.line,
            diagnostic.column,
            diagnostic.severity,
            diagnostic.message,
        )
        if key in self._seen:  # the same header error, reported from several translation units
            self._attach_notes = False  # and drop its notes, which were reported too
            return
        self._seen.add(key)
        self.out.append(diagnostic)
        self._attach_notes = diagnostic.severity is not Severity.NOTE


def parse_compiler_output(
    text: str,
    mapper: PathMapper,
    *,
    origin: DiagnosticOrigin = DiagnosticOrigin.COMPILER,
    base: str | None = None,
) -> list[Diagnostic]:
    """clang/gcc (and clang-tidy) diagnostics, linker errors and MSVC messages in ``text``."""
    builder = _Builder(mapper, origin, base)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line:
            continue
        if m := _INCLUDED.match(line):
            file, ln, _, message = builder.location(
                m["file"], m["line"], None, "included from here"
            )
            builder.pending_includes.append(
                Diagnostic(
                    file_path=file,
                    line=ln,
                    severity=Severity.NOTE,
                    message=message,
                    origin=builder.origin,
                )
            )
            continue
        if m := _CLANG.match(line):
            severity = _SEVERITY[m["sev"]]
            code = _code(m["code"])
            diag_origin = builder.origin
            if code and not code.startswith("-W") and builder.origin is DiagnosticOrigin.COMPILER:
                diag_origin = DiagnosticOrigin.ANALYZER
            file, ln, col, message = builder.location(m["file"], m["line"], m["col"], m["msg"])
            builder.add(
                Diagnostic(
                    file_path=file,
                    line=ln,
                    column=col,
                    severity=severity,
                    code=code,
                    message=message,
                    origin=diag_origin,
                )
            )
            continue
        if m := _MSVC.match(line):
            file, ln, col, message = builder.location(m["file"], m["line"], m["col"], m["msg"])
            builder.add(
                Diagnostic(
                    file_path=file,
                    line=ln,
                    column=col,
                    severity=_SEVERITY[m["sev"]],
                    code=m["code"],
                    message=message,
                    origin=DiagnosticOrigin.COMPILER,
                )
            )
            continue
        if m := _MSVC_LINK.match(line):
            builder.add(
                Diagnostic(
                    file_path=None,
                    severity=_SEVERITY[m["sev"]],
                    code=m["code"],
                    message=m["msg"],
                    origin=DiagnosticOrigin.LINKER,
                )
            )
            continue
        if m := _LLD.match(line):
            message = m["msg"]
            notes: list[Diagnostic] = []
            while i < len(lines) and lines[i].startswith(">>>"):
                notes.append(
                    Diagnostic(
                        file_path=None,
                        severity=Severity.NOTE,
                        message=lines[i][3:].strip(),
                        origin=DiagnosticOrigin.LINKER,
                    )
                )
                i += 1
            builder.add(
                Diagnostic(
                    file_path=None,
                    severity=_SEVERITY[m["sev"]],
                    code=_linker_code(message),
                    message=message,
                    notes=notes,
                    origin=DiagnosticOrigin.LINKER,
                )
            )
            continue
        if m := _GNU_LD_REF.match(line):
            file, ln, _, message = builder.location(m["file"], m["line"], None, m["msg"])
            builder.add(
                Diagnostic(
                    file_path=file,
                    line=ln,
                    severity=Severity.ERROR,
                    code=_linker_code(message),
                    message=message,
                    origin=DiagnosticOrigin.LINKER,
                )
            )
            continue
        if m := _DRIVER.match(line):
            message = m["msg"]
            if message.startswith(("linker command failed", "ld returned")):
                continue  # a summary of linker errors reported above
            builder.add(
                Diagnostic(
                    file_path=None,
                    severity=_SEVERITY[m["sev"]],
                    message=message,
                    origin=DiagnosticOrigin.COMPILER,
                )
            )
            continue
        if m := _GNU_LD.match(line):
            message = m["msg"]
            if "ld returned" in message or message.endswith(":") or "in function" in message:
                continue
            code = _linker_code(message)
            if message.startswith(("error:", "warning:")) or code is not None:
                severity = Severity.WARNING if message.startswith("warning:") else Severity.ERROR
                if message.startswith(("error:", "warning:")):
                    message = message.split(":", 1)[1].strip()
                builder.add(
                    Diagnostic(
                        file_path=None,
                        severity=severity,
                        code=_linker_code(message),
                        message=message,
                        origin=DiagnosticOrigin.LINKER,
                    )
                )
    return builder.out


def _linker_code(message: str) -> str | None:
    lowered = message.lower()
    if "undefined symbol" in lowered or "undefined reference" in lowered:
        return "undefined-reference"
    if "duplicate symbol" in lowered or "multiple definition" in lowered:
        return "duplicate-symbol"
    return None


def parse_cmake_output(text: str, mapper: PathMapper, source_dir: str) -> list[Diagnostic]:
    """``CMake Error at CMakeLists.txt:12 (add_executable):`` blocks and their indented text."""
    out: list[Diagnostic] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _CMAKE.match(lines[i])
        i += 1
        if m is None:
            continue
        body: list[str] = [m["msg"].strip()] if m["msg"].strip() else []
        while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
            if lines[i].strip():
                body.append(lines[i].strip())
            elif body and i + 1 < len(lines) and not lines[i + 1].startswith(" "):
                break
            i += 1
        severity = Severity.ERROR if m["sev"] == "Error" else Severity.WARNING
        message = " ".join(body) or f"CMake {m['sev'].lower()}"
        file_path: str | None = None
        line_no: int | None = None
        if m["file"]:
            file_path = mapper.relative(m["file"], source_dir)
            line_no = int(m["line"])
            if file_path is None:
                message = f"{m['file']}:{m['line']}: {message}"
                line_no = None
        if m["command"]:
            message = f"{message} (in {m['command']})"
        out.append(
            Diagnostic(
                file_path=file_path,
                line=line_no,
                severity=severity,
                code=None,
                message=message,
                origin=DiagnosticOrigin.BUILD_SYSTEM,
            )
        )
    return out


def errors_only(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diagnostics if d.severity is Severity.ERROR]
