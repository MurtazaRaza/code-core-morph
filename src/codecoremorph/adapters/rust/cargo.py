"""Cargo for the Rust adapter: crates, JSON diagnostics, nextest's JUnit report, the crate
scaffold and its Corrosion block in CMake (SPEC §11.5, D-56). Pure functions; the adapter runs
the commands."""

from __future__ import annotations

import json
import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import cast

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    Diagnostic,
    DiagnosticOrigin,
    Severity,
    TestCaseResult,
    TestStatus,
)

# The standards of the Rust adapter are its editions, oldest first (D-38's order).
EDITIONS = ("rust2015", "rust2018", "rust2021", "rust2024")
CORROSION_BEGIN = "# >>> CodeCoreMorph: the Rust crates (SPEC §11.5)"
CORROSION_END = "# <<< CodeCoreMorph: the Rust crates"
NEXTEST_PROFILE = "codecoremorph"
_SKIP_DIRS = {".git", ".codecoremorph", "target", "build", "out", "node_modules"}
_CRATE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_DTD = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def edition(standard: str) -> str:
    """``rust2021`` → ``2021``."""
    if standard not in EDITIONS:
        raise ValueError(f"{standard!r} is not a Rust edition; use one of {', '.join(EDITIONS)}")
    return standard.removeprefix("rust")


def valid_crate_name(name: str) -> bool:
    return _CRATE_NAME.match(name) is not None


_WORKSPACE = re.compile(r"(?m)^\s*\[workspace\]\s*$")
_PACKAGE = re.compile(r"(?m)^\s*\[package\]\s*$")


def _manifests(worktree: Path, exclude: Sequence[str]) -> list[tuple[str, str]]:
    """Each ``Cargo.toml`` outside build trees and excluded paths: (directory, text)."""
    skip = PathMatcher(exclude)
    found: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(worktree):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        if "Cargo.toml" in files:
            rel = Path(root).relative_to(worktree).as_posix()
            if not skip.matches(f"{rel}/Cargo.toml"):
                try:
                    text = (Path(root) / "Cargo.toml").read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                found.append((rel, text))
    return found


def _inside(path: str, directory: str) -> bool:
    return directory == "." or path.startswith(f"{directory}/")


def find_crates(worktree: Path, exclude: Sequence[str] = ()) -> list[str]:
    """What Cargo builds, as worktree-relative directories, sorted: each workspace root, and
    each package outside a workspace. A workspace's members build through it (D-58)."""
    manifests = _manifests(worktree, exclude)
    workspaces = [d for d, text in manifests if _WORKSPACE.search(text)]
    return [
        d
        for d, _ in manifests
        if d in workspaces or not any(_inside(d, w) for w in workspaces if w != d)
    ]


def find_packages(worktree: Path, exclude: Sequence[str] = ()) -> list[str]:
    """Every package (a manifest with ``[package]``), workspace members included, sorted."""
    return [d for d, text in _manifests(worktree, exclude) if _PACKAGE.search(text)]


def nested_manifest_dirs(worktree: Path, package: str) -> list[str]:
    """The directories below a package's that have a manifest of their own (another package or
    workspace, excluded ones too): their files aren't the package's."""
    return [d for d, _ in _manifests(worktree, ()) if d != package and _inside(d, package)]


# --------------------------------------------------------------------------- diagnostics

_LEVELS = {
    "error": Severity.ERROR,
    "error: internal compiler error": Severity.ERROR,
    "warning": Severity.WARNING,
    "note": Severity.NOTE,
    "help": Severity.NOTE,
    "failure-note": Severity.NOTE,
}


def _relative(file_name: str, crate_dir: str, roots: Sequence[str]) -> str:
    """A span's file as a worktree path: Cargo names it relative to the package, or
    absolute (in the sandbox's spelling) for a path outside it."""
    name = file_name.replace("\\", "/")
    for root in roots:
        prefix = root.replace("\\", "/").rstrip("/") + "/"
        if name.lower().startswith(prefix.lower()):
            return name[len(prefix) :]
    if name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
        return name
    return posixpath.normpath(posixpath.join(crate_dir, name)) if crate_dir != "." else name


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _diagnostic(
    message: dict[str, object], crate_dir: str, roots: Sequence[str], *, analyzer: bool
) -> Diagnostic | None:
    level = str(message.get("level", ""))
    severity = _LEVELS.get(level)
    if severity is None:
        return None
    spans = [cast(dict[str, object], s) for s in _list(message.get("spans")) if isinstance(s, dict)]
    primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else None)
    code_field = message.get("code")
    code = (
        str(cast(dict[str, object], code_field).get("code"))
        if isinstance(code_field, dict)
        else None
    )
    notes = [
        n
        for child in _list(message.get("children"))
        if isinstance(child, dict)
        for n in [_diagnostic(cast(dict[str, object], child), crate_dir, roots, analyzer=analyzer)]
        if n is not None
    ]
    origin = (
        DiagnosticOrigin.ANALYZER
        if analyzer and code is not None and code.startswith("clippy::")
        else DiagnosticOrigin.COMPILER
    )
    file_path = None
    line = column = None
    if primary is not None and primary.get("file_name"):
        file_path = _relative(str(primary["file_name"]), crate_dir, roots)
        line = _int(primary.get("line_start"))
        column = _int(primary.get("column_start"))
    return Diagnostic(
        file_path=file_path,
        line=line,
        column=column,
        severity=severity,
        code=code,
        message=str(message.get("message", "")),
        notes=notes,
        origin=origin,
    )


def parse_cargo_messages(
    stdout: str, crate_dir: str, roots: Sequence[str], *, analyzer: bool = False
) -> list[Diagnostic]:
    """The compiler (or clippy) messages of ``cargo --message-format=json`` output, with
    their files relative to the worktree. Duplicates (a crate built twice) appear once."""
    found: list[Diagnostic] = []
    seen: set[tuple[object, ...]] = set()
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("reason") != "compiler-message":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        diagnostic = _diagnostic(message, crate_dir, roots, analyzer=analyzer)
        if diagnostic is None or diagnostic.message.startswith("aborting due to"):
            continue
        key = (diagnostic.file_path, diagnostic.line, diagnostic.column, diagnostic.message)
        if key not in seen:
            seen.add(key)
            found.append(diagnostic)
    return found


# --------------------------------------------------------------------------- tests


def nextest_config(store_dir: str, junit_name: str = "junit.xml") -> str:
    """A nextest config whose profile writes the JUnit report CodeCoreMorph reads, to
    ``<store_dir>/codecoremorph/<junit_name>``. nextest keeps its store in the crate's own
    ``target/`` unless told otherwise, whatever ``--target-dir`` says, and that is inside the
    worktree."""
    if "'" in store_dir or "\n" in store_dir:
        raise ValueError(f"can't put {store_dir!r} in a TOML literal string")
    return (
        f"[store]\ndir = '{store_dir}'\n\n"
        f"[profile.{NEXTEST_PROFILE}]\nfail-fast = false\n\n"
        f'[profile.{NEXTEST_PROFILE}.junit]\npath = "{junit_name}"\n'
    )


def parse_nextest_junit(xml: str) -> list[TestCaseResult]:
    """nextest's JUnit report: one case per test, as ``nextest:<binary>::<test path>``."""
    # The report holds the output of code under test; refuse entity tricks.
    if _DTD.search(xml):
        raise ValueError("test reports must not contain a DTD")
    root = ET.fromstring(xml)  # noqa: S314 - DTDs rejected above
    cases: list[TestCaseResult] = []
    for case in root.iter("testcase"):
        binary = case.get("classname") or "crate"
        name = case.get("name") or "?"
        status = TestStatus.PASSED
        message = None
        for child in case:
            if child.tag in ("failure", "error"):
                status = TestStatus.FAILED if child.tag == "failure" else TestStatus.ERROR
                message = (child.get("message") or child.text or "").strip() or None
            elif child.tag == "skipped":
                status = TestStatus.SKIPPED
        cases.append(
            TestCaseResult(
                test_id=f"nextest:{binary}::{name}",
                status=status,
                duration_s=float(case.get("time") or 0.0),
                message=message,
            )
        )
    return cases


# --------------------------------------------------------------------------- the scaffold


def crate_files(name: str, edition_year: str, *, doc: str) -> dict[str, str]:
    """A staticlib crate's files, relative to its directory. ``Cargo.lock`` is written here,
    so a crate without dependencies builds ``--locked`` without running Cargo first."""
    return {
        "Cargo.toml": (
            "[package]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n'
            f'edition = "{edition_year}"\n'
            "publish = false\n\n"
            "[lib]\n"
            'crate-type = ["staticlib"]\n'
        ),
        "src/lib.rs": f"//! {doc}\n",
        "Cargo.lock": (
            "# This file is automatically @generated by Cargo.\n"
            "# It is not intended for manual editing.\n"
            "version = 4\n\n"
            "[[package]]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n'
        ),
    }


_ADD_LIBRARY = re.compile(r"\badd_library\s*\(\s*([A-Za-z0-9_.+-]+)([^)]*)\)", re.IGNORECASE)


def library_compiling(cmake_files: Iterable[tuple[str, str]], source: str) -> str | None:
    """The CMake library target that lists ``source`` (a worktree path), or None."""
    for path, text in cmake_files:
        directory = posixpath.dirname(path)
        for match in _ADD_LIBRARY.finditer(text):
            for token in match.group(2).split():
                rel = posixpath.normpath(posixpath.join(directory, token.strip('"')))
                if rel == source:
                    return match.group(1)
    return None


def corrosion_block(
    manifests: Sequence[str],
    links: Sequence[tuple[str, str]],
    includes: Sequence[tuple[str, str]] = (),
) -> list[str]:
    """The lines of the marked block: import each crate, give each C++ target its bridge
    headers' directory, link each (target, crate) pair."""
    # FROZEN: Cargo neither rewrites Cargo.lock in the worktree nor reaches the network.
    lines = [CORROSION_BEGIN, "find_package(Corrosion REQUIRED)"]
    lines += [f"corrosion_import_crate(MANIFEST_PATH {m} FROZEN)" for m in manifests]
    lines += [
        f"target_include_directories({target} PUBLIC {directory})" for target, directory in includes
    ]
    lines += [f"target_link_libraries({target} PUBLIC {crate})" for target, crate in links]
    return [*lines, CORROSION_END]


def without_corrosion_block(text: str) -> tuple[str, list[str]]:
    """``text`` without the marked block, and the block's inner lines (empty without one)."""
    start = text.find(CORROSION_BEGIN)
    if start < 0:
        return text, []
    end = text.find(CORROSION_END, start)
    end = len(text) if end < 0 else end + len(CORROSION_END)
    inner = text[start + len(CORROSION_BEGIN) : text.find(CORROSION_END, start)]
    for newline in ("\r\n", "\n"):
        if text.startswith(newline, end):
            end += len(newline)
            break
    head = text[:start].rstrip("\r\n")
    head = head + ("\r\n" if "\r\n" in text else "\n") if head else head
    return head + text[end:], [line.strip() for line in inner.splitlines() if line.strip()]


def with_corrosion_block(
    text: str,
    manifest: str,
    link: tuple[str, str] | None,
    include: tuple[str, str] | None = None,
) -> str:
    """``text`` (the top-level ``CMakeLists.txt``) with ``manifest`` imported, linked to its C++
    target, and that target given ``include`` (target, directory), in the marked block at the
    end; earlier crates stay."""
    newline = "\r\n" if "\r\n" in text else "\n"
    base, inner = without_corrosion_block(text)
    manifests = [
        m.split("MANIFEST_PATH", 1)[1].strip(" )").split()[0] for m in inner if "MANIFEST_PATH" in m
    ]
    links: list[tuple[str, str]] = []
    includes: list[tuple[str, str]] = []
    for line in inner:
        found = re.match(r"target_link_libraries\((\S+) PUBLIC (\S+)\)", line)
        if found:
            links.append((found.group(1), found.group(2)))
        found = re.match(r"target_include_directories\((\S+) PUBLIC (\S+)\)", line)
        if found:
            includes.append((found.group(1), found.group(2)))
    if manifest not in manifests:
        manifests.append(manifest)
    if link is not None and link not in links:
        links.append(link)
    if include is not None and include not in includes:
        includes.append(include)
    if base and not base.endswith(("\n", "\r\n")):
        base += newline
    block = newline.join(corrosion_block(manifests, links, includes))
    return f"{base}{newline}{block}{newline}"


_LOCK_PACKAGE = re.compile(r"^\[\[package\]\]\s*$", re.MULTILINE)
_LOCK_FIELD = re.compile(r'^(name|version|source)\s*=\s*"([^"]*)"\s*$', re.MULTILINE)


def fetched_packages(lock_text: str) -> list[str]:
    """The packages a ``Cargo.lock`` takes from a registry or git (``name version``): what
    ``cargo fetch`` downloads before an offline build. Path packages (the crate itself) don't
    count."""
    found: list[str] = []
    for block in _LOCK_PACKAGE.split(lock_text)[1:]:
        fields = dict(_LOCK_FIELD.findall(block.split("\n[", 1)[0]))
        if "source" in fields:
            found.append(f"{fields.get('name', '?')} {fields.get('version', '?')}")
    return found


def cbindgen_config() -> str:
    """cbindgen's settings for comparing a crate's C API with a header (D-56)."""
    return 'language = "C"\nusize_is_size_t = true\nstyle = "both"\n'
