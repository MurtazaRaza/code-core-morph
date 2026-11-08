"""A Rust crate as a port's source (D-75): what the planner needs beyond the item scanner.

The scanner of D-58 lists a crate's items, which is all a port *to* Rust needs of the Rust
index. A port *from* Rust also needs the references between files, to order its module units
leaves first (as D-68 orders a C library's translation units by calls), the crate's targets,
and its tests, by file. rust-analyzer's SCIP output gives the references (:mod:`.scip`); the
targets and tests come from the manifest and the test scanner.
"""

from __future__ import annotations

import posixpath
import tomllib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import cast

from codecoremorph.adapters.rust.scip import Document, references
from codecoremorph.adapters.rust.symbols import module_path
from codecoremorph.adapters.rust.tests import bin_names, scan_tests, test_binary, test_id
from codecoremorph.core.models import (
    BuildTarget,
    FileRole,
    ReferenceKind,
    SymbolKind,
    TargetKind,
    TestEntry,
)
from codecoremorph.store.index import ReferenceRow, SymbolRow

SCIP_FILE = "index.scip"


def file_role(rel_to_package: str) -> FileRole:
    """A package file's role in a port from Rust: integration tests are tests, the build
    script and the manifest are the build, the rest is source (a module's ``#[cfg(test)]``
    tests port with their tests unit, D-75)."""
    parts = PurePosixPath(rel_to_package).parts
    if parts and parts[0] == "tests":
        return FileRole.TEST
    if rel_to_package in ("build.rs", "Cargo.toml"):
        return FileRole.BUILD
    return FileRole.SOURCE


@dataclass
class SourceIndex:
    """Rows a Rust source adds to its index."""

    references: list[ReferenceRow] = field(default_factory=list[ReferenceRow])
    symbols: list[SymbolRow] = field(default_factory=list[SymbolRow])
    targets: list[BuildTarget] = field(default_factory=list[BuildTarget])
    tests: list[TestEntry] = field(default_factory=list[TestEntry])


def package_prefix(package: str) -> str:
    return "" if package in ("", ".") else f"{package.rstrip('/')}/"


def local_dependencies(manifest: str) -> list[str]:
    """The packages a manifest's ``[dependencies]`` take by path (a workspace's own), by
    package name."""
    try:
        data = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        return []
    table = data.get("dependencies")
    if not isinstance(table, dict):
        return []
    found: list[str] = []
    for key, spec in cast(dict[str, object], table).items():
        if isinstance(spec, dict) and "path" in spec:
            renamed = cast(dict[str, object], spec).get("package")
            found.append(renamed if isinstance(renamed, str) else key)
    return sorted(found)


def add_references(
    out: SourceIndex,
    documents: list[Document],
    *,
    package: str,
    crate: str,
    crates: Mapping[str, str],
    known: Mapping[str, str],
) -> None:
    """The references of one package's SCIP index, to the scanner's symbols (``known``: item
    path → its file, in every package) or to a module a file defines, which the scanner
    doesn't list: so ``pub mod text;`` and ``use crate::stats;`` tie the files as calls do.
    ``crates`` maps each package of the workspace, as SCIP names it, to its crate, so a
    program's use of its workspace's library ties it too."""
    prefix = package_prefix(package)
    defined, found = references(documents, crates, prefix)
    modules: set[str] = set()
    for path, (file, line, column) in sorted(defined.items()):
        if path in known or module_path(crate, file.removeprefix(prefix)) != path:
            continue
        if path == crate:
            continue  # the crate root: everything is in it
        modules.add(path)
        name = path.rsplit("::", 1)[-1]
        out.symbols.append(
            (
                f"rust:{path}",
                name,
                path,
                SymbolKind.NAMESPACE.value,
                file,
                line,
                column,
                line,
                column,
                None,
                "public",
                1,
                "none",
            )
        )
    # Only references to what the index holds: a field or a parameter ties nothing.
    for ref in found:
        if ref.path in known or ref.path in modules:
            out.references.append(
                (ref.file, None, f"rust:{ref.path}", ReferenceKind.USE.value, ref.line, ref.column)
            )


def add_targets_and_tests(
    out: SourceIndex,
    *,
    package: str,
    name: str,
    manifest: str,
    files: Mapping[str, str],
    local: Collection[str] = (),
) -> None:
    """The package's targets (its library, programs and integration tests, each with the files
    it compiles as far as paths tell) and its tests (``nextest:`` IDs, by the file that has
    them). ``files``: path relative to the package → its text; ``local``: the names of the
    workspace's packages, which its programs and tests may use besides its own library."""
    prefix = package_prefix(package)
    crate = name.replace("-", "_")
    bins = bin_names(manifest, name)
    uses = [d for d in local_dependencies(manifest) if d in local and d != name]
    library: list[str] = []
    programs: dict[str, list[str]] = {}
    integration: dict[str, list[str]] = {}
    for rel in sorted(files):
        parts = PurePosixPath(rel).parts
        if module_path(crate, rel) is not None:
            library.append(prefix + rel)
        elif rel in bins:
            programs.setdefault(bins[rel], []).append(prefix + rel)
        elif len(parts) == 3 and parts[:2] == ("src", "bin"):
            programs.setdefault(posixpath.splitext(parts[2])[0], []).append(prefix + rel)
        elif parts and parts[0] == "tests" and len(parts) >= 2:
            test = posixpath.splitext(parts[1])[0]
            integration.setdefault(test, []).append(prefix + rel)
    if library:
        out.targets.append(BuildTarget(name=name, kind=TargetKind.LIBRARY, files=library))
    for program, sources in sorted(programs.items()):
        # Named as nextest names its binary: a package's library and program share a name.
        deps = ([name] if library else []) + uses
        out.targets.append(
            BuildTarget(
                name=f"{name}::bin/{program}",
                kind=TargetKind.EXECUTABLE,
                files=sources,
                dependencies=deps,
                artifacts=[program],
            )
        )
    for test, sources in sorted(integration.items()):
        deps = ([name] if library else []) + uses
        out.targets.append(
            BuildTarget(
                name=f"{name}::{test}",
                kind=TargetKind.EXECUTABLE,
                files=sources,
                dependencies=deps,
            )
        )
    for rel, text in sorted(files.items()):
        binary = test_binary(name, manifest, rel)
        if binary is None:
            continue
        for test in scan_tests(text):
            out.tests.append(
                TestEntry(
                    test_id=test_id(binary[0], binary[1], test.path),
                    runs=binary[0],
                    file=prefix + rel,
                )
            )


def files_with_tests(entries: Iterable[TestEntry]) -> dict[str, list[str]]:
    """The test IDs of each file that has tests."""
    found: dict[str, list[str]] = {}
    for entry in entries:
        if entry.file is not None:
            found.setdefault(entry.file, []).append(entry.test_id)
    return found
