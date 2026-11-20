"""A Rust crate as a port's source (D-75): what the planner needs beyond the item scanner.

The scanner of D-58 lists a crate's items, which is all a port *to* Rust needs of the Rust
index. A port *from* Rust also needs the references between files, to order its module units
leaves first (as D-68 orders a C library's translation units by calls), the crate's targets,
and its tests, by file. rust-analyzer's SCIP output gives the references (:mod:`.scip`); the
targets and tests come from the manifest and the test scanner.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import cast

from codecoremorph.adapters.rust.scip import Document, macro_symbol, references
from codecoremorph.adapters.rust.symbols import mask, module_path
from codecoremorph.adapters.rust.tests import bin_names, scan_tests, test_binary, test_id
from codecoremorph.core.models import (
    BuildTarget,
    FileRole,
    MacroKind,
    MacroSite,
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
    workspace's packages, which its programs and tests may use besides its own library. A
    procedural macro package's library is marked (D-93)."""
    prefix = package_prefix(package)
    macros = proc_macro_package(manifest)
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
        out.targets.append(
            BuildTarget(name=name, kind=TargetKind.LIBRARY, files=library, proc_macro=macros)
        )
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


# ------------------------------------------------------------------ procedural macros (D-93)


def proc_macro_package(manifest: str) -> bool:
    """Whether a package's library is a procedural macro crate (``[lib] proc-macro = true``)."""
    try:
        data = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        return bool(re.search(r"(?m)^\s*proc[-_]macro\s*=\s*true\b", manifest))
    lib = data.get("lib")
    if not isinstance(lib, dict):
        return False
    table = cast(dict[str, object], lib)
    return table.get("proc-macro") is True or table.get("proc_macro") is True


_DERIVE = re.compile(r"#\[\s*proc_macro_derive\s*\(\s*(\w+)")
_MACRO_FN = re.compile(
    r"#\[\s*(proc_macro_attribute|proc_macro)\s*\]"
    r"(?:\s*(?://[^\n]*|#\[[^\]]*\]))*"
    r"\s*pub\s+fn\s+(\w+)"
)


def macro_definitions(files: Mapping[str, str]) -> dict[str, MacroKind]:
    """The procedural macros a macro package's files define, by name: a
    ``#[proc_macro_derive(Name)]`` function is the derive ``Name``, a
    ``#[proc_macro_attribute]`` or ``#[proc_macro]`` one the attribute or function-like macro
    it is named."""
    found: dict[str, MacroKind] = {}
    for _, text in sorted(files.items()):
        for match in _DERIVE.finditer(text):
            found[match.group(1)] = MacroKind.DERIVE
        for match in _MACRO_FN.finditer(text):
            attribute = match.group(1) == "proc_macro_attribute"
            found[match.group(2)] = MacroKind.ATTRIBUTE if attribute else MacroKind.FUNCTION
    return found


_USE = re.compile(r"(?m)^[ \t]*(pub(?:[ \t]*\([^)]*\))?[ \t]+)?use\b[^;]*;")


def _use_lines(text: str) -> list[tuple[int, int, bool]]:
    """Each ``use`` statement's first and last line (1-based), and whether it re-exports to
    code outside the crate (``pub use``, not ``pub(crate) use``)."""
    found: list[tuple[int, int, bool]] = []
    for match in _USE.finditer(text):
        first = text.count("\n", 0, match.start()) + 1
        last = first + match.group(0).count("\n")
        visibility = (match.group(1) or "").strip()
        found.append((first, last, visibility == "pub"))
    return found


_CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")


def cfg_test_lines(text: str) -> list[tuple[int, int]]:
    """The lines (first, last; 1-based) of each ``#[cfg(test)]`` item: a module's tests, which
    a port's test unit translates after the module's unit made the rest a façade."""
    masked = mask(text)
    found: list[tuple[int, int]] = []
    for match in _CFG_TEST.finditer(masked):
        level = 0
        end = None
        for k in range(match.end(), len(masked)):
            ch = masked[k]
            if ch == ";" and level == 0:
                end = k
                break
            if ch == "{":
                level += 1
            elif ch == "}":
                level -= 1
                if level == 0:
                    end = k
                    break
        if end is not None:
            found.append((masked.count("\n", 0, match.start()) + 1, masked.count("\n", 0, end) + 1))
    return found


def macro_sites(
    documents: Iterable[Document],
    macros: Mapping[str, Mapping[str, MacroKind]],
    *,
    prefix: str,
    files: Mapping[str, str],
) -> list[MacroSite]:
    """The uses of the macro packages' macros (``macros``: package name, as SCIP names it, →
    its macros) in one package's ``files`` (path relative to the package, as SCIP's documents
    name them → text): each reference to a macro's symbol outside a ``use``, and each
    ``pub use`` of one, a re-export. A derive's helper attribute (``#[wire(skip)]``) resolves
    to the macro package but names none of its macros: it belongs to its derive's site. A site
    in a ``#[cfg(test)]`` item is the tests'."""
    sites: dict[tuple[str, int, int], MacroSite] = {}
    for document in documents:
        text = files.get(document.path)
        if text is None:
            continue
        uses = _use_lines(text)
        tests = cfg_test_lines(text)
        for occurrence in document.occurrences:
            if occurrence.definition:
                continue
            found = macro_symbol(occurrence.symbol)
            if found is None:
                continue
            package, name = found
            kind = macros.get(package, {}).get(name)
            if kind is None:
                continue
            within = [u for u in uses if u[0] <= occurrence.line <= u[1]]
            reexport = any(u[2] for u in within)
            if within and not reexport:
                continue
            file = prefix + document.path
            sites[(file, occurrence.line, occurrence.column)] = MacroSite(
                file=file,
                line=occurrence.line,
                column=occurrence.column,
                macro=name,
                kind=kind,
                package=package,
                reexport=reexport,
                test_only=any(a <= occurrence.line <= b for a, b in tests),
            )
    return [sites[k] for k in sorted(sites)]


_ITEM = re.compile(
    r"\bpub\s+(?:(?:unsafe|const|async|extern\s+\"\w+\")\s+)*"
    r"(?:fn|struct|enum|union|trait|type|const|static|mod)\s+(\w+)"
)
_IMPL = re.compile(r"\bimpl\b")
_FN = re.compile(r"(pub\s+)?(?:(?:const|unsafe|async|extern\s+\"\w+\")\s+)*fn\s+(\w+)")


def _depths(masked: str) -> list[int]:
    """The brace depth before each character of ``masked``."""
    depth = [0] * (len(masked) + 1)
    level = 0
    for k, ch in enumerate(masked):
        depth[k] = level
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
    depth[len(masked)] = level
    return depth


def _block(masked: str, start: int) -> tuple[int, int] | None:
    """The span of the brace block that opens at or after ``start`` (its ``{`` and ``}``)."""
    open_at = masked.find("{", start)
    if open_at < 0:
        return None
    level = 0
    for k in range(open_at, len(masked)):
        if masked[k] == "{":
            level += 1
        elif masked[k] == "}":
            level -= 1
            if level == 0:
                return open_at, k
    return None


def expansion_items(module: str | None, expansion: str) -> list[str]:
    """What an expansion at a site of the library's ``module`` defines that a port maps, by
    the scanner's paths: its public items (``module::name``), and the methods of its ``impl``
    blocks under their type (``module::Type::method``: all of a trait implementation's, an
    inherent ``impl``'s public ones). None for a site outside the library (a test's)."""
    if module is None or not expansion:
        return []
    masked = mask(expansion)
    depth = _depths(masked)
    found: set[str] = set()
    for match in _ITEM.finditer(masked):
        if depth[match.start()] == 0:
            found.add(f"{module}::{match.group(1)}")
    for match in _IMPL.finditer(masked):
        if depth[match.start()] != 0:
            continue
        span = _block(masked, match.end())
        if span is None:
            continue
        header = masked[match.end() : span[0]]
        header = re.sub(r"\bwhere\b.*", "", header, flags=re.S)
        trait = re.search(r"\bfor\s+([A-Za-z_][\w:]*)", header)
        if trait is not None:
            owner = trait.group(1)
        else:
            named = re.match(r"\s*(?:<[^{]*?>\s*)?([A-Za-z_][\w:]*)", header)
            if named is None:
                continue
            owner = named.group(1)
        owner = owner.rsplit("::", 1)[-1]
        for fn in _FN.finditer(masked, span[0] + 1, span[1]):
            at = fn.start()
            if depth[at] != depth[span[0]] + 1:
                continue
            if at and (masked[at - 1].isalnum() or masked[at - 1] == "_"):
                continue
            if trait is not None or fn.group(1):
                found.add(f"{module}::{owner}::{fn.group(2)}")
    return sorted(found)
