"""Parse and check scripts/deps/manifest.txt (SPEC §7.15). Standard library only.

Used by check-manifest.py (pre-commit) and refresh-hashes.py (maintainers). The shell and batch
scripts parse the same file with `IFS='|' read` and `for /f "delims=|"`. `for /f` collapses
empty fields, so every field must be non-empty, and URLs avoid characters cmd.exe treats
specially (`%`, `!`, `^`, `&`).

A tool needs a row for every supported platform, unless a coverage line limits it:

    #!platforms cpp|llvm linux-x64,linux-arm64,macos-arm64

The scripts skip it like any other comment; only this checker reads it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace

FIELDS = ("group", "tool", "version", "platform", "url", "sha256", "archive", "bin_dir")
PLATFORMS = ("windows-x64", "linux-x64", "linux-arm64", "macos-arm64", "macos-x64")
ANY_PLATFORM = "any"
ARCHIVES = ("zip", "tar.gz", "tar.xz", "file")

_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_URL = re.compile(r"^https://[A-Za-z0-9._~/:+-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BIN_DIR = re.compile(r"^(\.|[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)*)$")


@dataclass(frozen=True)
class Row:
    group: str
    tool: str
    version: str
    platform: str
    url: str
    sha256: str
    archive: str
    bin_dir: str
    line: int = 0

    def render(self) -> str:
        return "|".join(getattr(self, name) for name in FIELDS)

    def with_values(self, **changes: str) -> Row:
        return replace(self, **changes)


def parse(text: str) -> tuple[list[Row], list[str]]:
    """Return the rows of a manifest and the problems found in individual lines."""
    rows: list[Row] = []
    errors: list[str] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        where = f"line {number}"
        if line != line.strip() or any(c.isspace() for c in line):
            errors.append(f"{where}: whitespace is not allowed in manifest rows")
            continue
        fields = line.split("|")
        if len(fields) != len(FIELDS):
            errors.append(f"{where}: expected {len(FIELDS)} fields ({'|'.join(FIELDS)})")
            continue
        row = Row(*fields, line=number)
        errors += [f"{where}: {problem}" for problem in _row_problems(row)]
        rows.append(row)
    return rows, errors


def _row_problems(row: Row) -> list[str]:
    problems: list[str] = []
    for name in FIELDS:
        if not getattr(row, name):
            problems.append(f"{name} is empty")
    if problems:
        return problems
    if not _NAME.match(row.group):
        problems.append(f"group {row.group!r} must match {_NAME.pattern}")
    if not _NAME.match(row.tool):
        problems.append(f"tool {row.tool!r} must match {_NAME.pattern}")
    if not _VERSION.match(row.version):
        problems.append(f"version {row.version!r} must match {_VERSION.pattern}")
    if row.platform not in (*PLATFORMS, ANY_PLATFORM):
        problems.append(f"platform {row.platform!r} must be one of {', '.join(PLATFORMS)}, any")
    if not _URL.match(row.url):
        problems.append(
            "url must be https:// and use only letters, digits and ._~/:+- "
            "(no query strings, % or other characters cmd.exe treats specially)"
        )
    if not _SHA256.match(row.sha256):
        problems.append("sha256 must be 64 lowercase hex digits")
    if row.archive not in ARCHIVES:
        problems.append(f"archive {row.archive!r} must be one of {', '.join(ARCHIVES)}")
    if row.platform.startswith("windows-") and row.archive == "tar.xz":
        problems.append("Windows rows can't use tar.xz (the built-in tar.exe may not read it)")
    if not _BIN_DIR.match(row.bin_dir) or ".." in row.bin_dir.split("/"):
        problems.append(f"bin_dir {row.bin_dir!r} must be '.' or a relative path without '..'")
    return problems


COVERAGE_PREFIX = "#!platforms "
Coverage = dict[tuple[str, str], tuple[str, ...]]


def parse_coverage(text: str) -> tuple[Coverage, list[str]]:
    """Read the ``#!platforms group|tool p1,p2`` lines and the problems found in them."""
    coverage: Coverage = {}
    errors: list[str] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.removesuffix("\r")
        if not line.startswith(COVERAGE_PREFIX):
            continue
        where = f"line {number}"
        parts = line[len(COVERAGE_PREFIX) :].split()
        name = parts[0].split("|") if parts else []
        if len(parts) != 2 or len(name) != 2:
            errors.append(f"{where}: expected '{COVERAGE_PREFIX}group|tool platform,platform'")
            continue
        platforms = tuple(parts[1].split(","))
        unknown = [p for p in platforms if p not in PLATFORMS]
        if unknown:
            errors.append(f"{where}: unknown platform {', '.join(unknown)}")
            continue
        key = (name[0], name[1])
        if key in coverage:
            errors.append(f"{where}: {name[0]}|{name[1]} already has a coverage line")
        coverage[key] = platforms
    return coverage, errors


def check(rows: list[Row], coverage: Coverage | None = None) -> list[str]:
    """Problems across rows: duplicates, mixed versions, missing platforms."""
    coverage = coverage or {}
    errors: list[str] = []
    by_tool: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        by_tool[(row.group, row.tool)].append(row)
    for group, tool in sorted(set(coverage) - set(by_tool)):
        errors.append(f"{group}|{tool}: a coverage line names a tool with no rows")
    for (group, tool), tool_rows in sorted(by_tool.items()):
        name = f"{group}|{tool}"
        platforms = [r.platform for r in tool_rows]
        duplicates = sorted({p for p in platforms if platforms.count(p) > 1})
        if duplicates:
            errors.append(f"{name}: more than one row for {', '.join(duplicates)}")
        versions = sorted({r.version for r in tool_rows})
        if len(versions) > 1:
            errors.append(f"{name}: rows pin different versions ({', '.join(versions)})")
        if ANY_PLATFORM in platforms:
            if len(tool_rows) > 1:
                errors.append(f"{name}: an 'any' row must be the tool's only row")
            continue
        expected = coverage.get((group, tool), PLATFORMS)
        missing = [p for p in expected if p not in platforms]
        if missing:
            errors.append(f"{name}: no row for {', '.join(missing)}")
        extra = sorted({p for p in platforms if p not in expected})
        if extra:
            errors.append(f"{name}: its coverage line excludes {', '.join(extra)}")
    return errors
