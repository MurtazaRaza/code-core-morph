"""Rust tests as test reports name them, with their checks (D-60): the test map's target IDs
and the cross-language assertion guard (I-4).

A scanner like the symbol index's: it masks comments and literals, follows inline modules and
finds each ``#[test]`` function, its test ID (``nextest:<binary>::<module path>::<name>``, as
``cargo nextest``'s JUnit report spells it) and its checks: the ``assert!`` family,
``panic!``, ``unwrap_err``/``expect_err`` and ``#[should_panic]``.

Binaries: a crate's library (``src/**``) is ``<package>``; an integration test
``tests/<name>.rs`` (or ``tests/<name>/main.rs``) is ``<package>::<name>``; a program
(``src/main.rs``, ``src/bin/<name>.rs``) is ``<package>::bin/<name>``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from codecoremorph.adapters.rust.symbols import mask, module_path

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSERT_MACROS = frozenset(
    {"assert", "assert_eq", "assert_ne", "assert_matches", "debug_assert", "panic", "unreachable"}
)
_EXPECTING = frozenset({"unwrap_err", "expect_err"})
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class RustTest:
    path: str  # inside its binary: module::name
    line: int
    checks: tuple[str, ...]


def _close(masked: str, opening: int) -> int:
    """The index of the bracket that closes the one at ``opening``, or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener, closer = masked[opening], pairs[masked[opening]]
    depth = 0
    for i in range(opening, len(masked)):
        if masked[i] == opener:
            depth += 1
        elif masked[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _skip_space(masked: str, i: int) -> int:
    while i < len(masked) and masked[i].isspace():
        i += 1
    return i


def _checks(text: str, masked: str, start: int, end: int, should_panic: bool) -> list[str]:
    found: list[str] = ["#[should_panic]"] if should_panic else []
    i = start
    while (match := _IDENT.search(masked, i, end)) is not None:
        name = match.group()
        after = _skip_space(masked, match.end())
        before = masked[match.start() - 1] if match.start() > 0 else " "
        if before.isalnum() or before == "_":
            i = match.end()
            continue
        if name in _ASSERT_MACROS and masked.startswith("!", after):
            opening = _skip_space(masked, after + 1)
            closing = _close(masked, opening) if masked[opening : opening + 1] in "([{" else -1
            if closing < 0 or closing > end:
                i = match.end()
                continue
            found.append(_SPACE.sub(" ", text[match.start() : closing + 1]).strip())
            i = closing + 1
        elif name in _EXPECTING and before == "." and masked.startswith("(", after):
            found.append(f".{name}()")
            i = after + 1
        else:
            i = match.end()
    return found


def scan_tests(text: str) -> list[RustTest]:
    """The ``#[test]`` functions of one file, with their module path inside it."""
    masked = mask(text)
    tests: list[RustTest] = []
    scopes: list[tuple[str | None, int]] = []  # (module name or None, closing brace index)
    attributes: list[str] = []
    i, n = 0, len(masked)
    while i < n:
        while scopes and i > scopes[-1][1]:
            scopes.pop()
        c = masked[i]
        if c == "#" and masked.startswith("[", _skip_space(masked, i + 1)):
            opening = _skip_space(masked, i + 1)
            closing = _close(masked, opening)
            if closing < 0:
                break
            attributes.append(_SPACE.sub("", masked[opening + 1 : closing]))
            i = closing + 1
            continue
        match = _IDENT.match(masked, i)
        if match is None or (i > 0 and (masked[i - 1].isalnum() or masked[i - 1] == "_")):
            if c == "{":
                closing = _close(masked, i)
                scopes.append((None, closing if closing >= 0 else n))
                attributes = []
            elif c in ";}":
                attributes = []
            i += 1
            continue
        word = match.group()
        if word == "mod":
            name = _IDENT.match(masked, _skip_space(masked, match.end()))
            if name is not None:
                brace = _skip_space(masked, name.end())
                if masked.startswith("{", brace):
                    closing = _close(masked, brace)
                    scopes.append((name.group(), closing if closing >= 0 else n))
                    attributes = []
                    i = brace + 1
                    continue
        if word == "fn":
            name = _IDENT.match(masked, _skip_space(masked, match.end()))
            brace = masked.find("{", match.end())
            semicolon = masked.find(";", match.end())
            if name is not None and brace >= 0 and (semicolon < 0 or brace < semicolon):
                closing = _close(masked, brace)
                closing = closing if closing >= 0 else n
                if "test" in attributes:
                    modules = [m for m, _ in scopes if m is not None]
                    should_panic = any(a.startswith("should_panic") for a in attributes)
                    tests.append(
                        RustTest(
                            path="::".join([*modules, name.group()]),
                            line=text.count("\n", 0, match.start()) + 1,
                            checks=tuple(_checks(text, masked, brace, closing, should_panic)),
                        )
                    )
                attributes = []
                i = closing + 1
                continue
        i = match.end()
    return tests


def _bin_names(manifest: str, package: str) -> dict[str, str]:
    """Program source files (relative to the package) and their names, from ``[[bin]]``
    entries and Cargo's defaults."""
    names = {"src/main.rs": package}
    try:
        data = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        return names
    bins: object = data.get("bin", [])
    for entry in cast(list[object], bins) if isinstance(bins, list) else []:
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, object], entry)
        name, path = fields.get("name"), fields.get("path")
        if isinstance(name, str):
            key = path if isinstance(path, str) else f"src/bin/{name}.rs"
            names[str(PurePosixPath(key))] = name
    return names


def test_binary(package: str, manifest: str, rel_to_package: str) -> tuple[str, str] | None:
    """``(binary ID, module prefix)`` of a package file: where its tests run, and the module
    path their names start with. None for files no test binary compiles as a root."""
    parts = PurePosixPath(rel_to_package).parts
    if not parts or not parts[-1].endswith(".rs"):
        return None
    if parts[0] == "tests":
        if len(parts) == 2:
            return f"{package}::{parts[1].removesuffix('.rs')}", ""
        if len(parts) == 3 and parts[2] == "main.rs":
            return f"{package}::{parts[1]}", ""
        return None
    bins = _bin_names(manifest, package)
    if rel_to_package in bins:
        return f"{package}::bin/{bins[rel_to_package]}", ""
    if len(parts) == 3 and parts[:2] == ("src", "bin"):
        return f"{package}::bin/{parts[2].removesuffix('.rs')}", ""
    crate = package.replace("-", "_")
    module = module_path(crate, rel_to_package)
    if module is None:
        return None
    return package, module.removeprefix(crate).removeprefix("::")


def test_id(binary: str, prefix: str, path: str) -> str:
    return f"nextest:{binary}::{prefix + '::' if prefix else ''}{path}"
