"""What a port's source records in its index beyond its symbols (SPEC §11.9, §11.12, D-78,
D-92): the rows the C# and the Java index helpers write with ``port=1`` (types, generic uses,
refusals, throws, test methods and the C# ones' notices, native imports, packages and dynamic
sites), read into the index's models, the same for every source.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from codecoremorph.core.models import (
    DynamicBinding,
    DynamicSite,
    DynamicValue,
    FileRole,
    GenericUse,
    NativeCall,
    NativeImport,
    PackageEntry,
    PackageUse,
    PortNotice,
    PortNoticeKind,
    PortRefusal,
    RefusalSite,
    RuleSite,
    SymbolKind,
    TargetKind,
    TestEntry,
    TestMethod,
    ThrowSite,
    TypeEntry,
)
from codecoremorph.store.index import IndexStore

# A generic's uses with the same type arguments the index keeps (the planner names a few).
MAX_USES = 12


@dataclass
class PortRecords:
    """What a port's source records beyond its symbols (D-78)."""

    types: list[TypeEntry] = field(default_factory=list[TypeEntry])
    uses: list[GenericUse] = field(default_factory=list[GenericUse])
    tests: list[TestMethod] = field(default_factory=list[TestMethod])
    packages: list[PackageEntry] = field(default_factory=list[PackageEntry])

    def entries(self) -> list[TestEntry]:
        """A test entry per case the sources name exactly, run by its test project."""
        return [
            TestEntry(test_id=case, runs=method.project, file=method.file)
            for method in self.tests
            if method.exact
            for case in method.cases
        ]


def port_records(
    rows: Iterable[Mapping[str, Any]],
    known: Collection[str],
    test_id: Callable[[str, str], str],
) -> PortRecords:
    """The helper's ``type``, ``inst``, ``refuse``, ``throw``, ``test``, ``notice``, ``native``,
    ``pkguse``, ``pkg``, ``dyn`` and ``dynval`` rows (``port=1``) as the index's models, for the
    files the index has (``known``). A refusal, a throw, a notice, a P/Invoke declaration, a
    package use, a dynamic site or a dynamic value (D-88) goes to the type it is in; a
    generic's uses with the same type arguments keep their first :data:`MAX_USES`. A test
    row's cases get their IDs from ``test_id`` (class, case): the source language's."""
    types: dict[str, TypeEntry] = {}
    uses: list[GenericUse] = []
    tests: list[TestMethod] = []
    packages: list[PackageEntry] = []
    refusals: list[tuple[str, RefusalSite]] = []
    throws: list[tuple[str, ThrowSite]] = []
    notices: list[tuple[str, PortNotice]] = []
    imports: list[tuple[str, NativeImport]] = []
    calls: list[tuple[str, NativeCall]] = []
    package_uses: list[tuple[str, PackageUse]] = []
    dynamic_sites: list[tuple[str, DynamicSite]] = []
    dynamic_values: list[tuple[str, DynamicValue]] = []
    counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for row in rows:
        kind = row.get("t")
        if kind == "type":
            files = [str(f) for f in _strings(row.get("files")) if f in known]
            if not files:
                continue
            types[str(row["id"])] = TypeEntry(
                id=str(row["id"]),
                name=str(row["name"]),
                kind=SymbolKind(str(row["kind"])),
                files=files,
                project=_optional(row.get("project")),
                outer=_optional(row.get("outer")),
                base=_optional(row.get("base")),
                interfaces=_strings(row.get("interfaces")),
                external_bases=_strings(row.get("external")),
                abstract=bool(row.get("abstract")),
                sealed=bool(row.get("sealed")),
                static=bool(row.get("static")),
                type_parameters=_strings(row.get("tparams")),
                members=_strings(row.get("members")),
                virtual_members=_strings(row.get("virtual")),
                instance_fields=_strings(row.get("fields")),
                static_state=_strings(row.get("statics")),
                native_only=bool(row.get("native_only")),
            )
        elif kind == "refuse" and row.get("file") in known:
            site = RefusalSite(
                what=PortRefusal(str(row["what"])),
                detail=str(row.get("detail") or row["what"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            refusals.append((str(row["type"]), site))
        elif kind == "notice" and row.get("file") in known:
            notice = PortNotice(
                what=PortNoticeKind(str(row["what"])),
                detail=str(row.get("detail") or row["what"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            notices.append((str(row["type"]), notice))
        elif kind == "native" and row.get("file") in known:
            native = NativeImport(
                library=str(row.get("library") or ""),
                entry_point=str(row["entry"]),
                member=str(row["member"]),
                signature=str(row.get("sig") or ""),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
            )
            imports.append((str(row["type"]), native))
        elif kind == "ncall" and row.get("file") in known:
            call = NativeCall(
                member=str(row["member"]),
                caller=_optional(row.get("caller")),
                platforms=_strings(row.get("platforms")),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
            )
            calls.append((str(row["type"]), call))
        elif kind == "pkguse" and row.get("file") in known:
            used = PackageUse(
                package=str(row["package"]),
                symbol=str(row["symbol"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            package_uses.append((str(row["type"]), used))
        elif kind == "dyn" and row.get("file") in known:
            dynamic_site = DynamicSite(
                kind=str(row["kind"]),
                text=str(row.get("text") or ""),
                operands=_strings(row.get("operands")),
                bindings=[_binding(b) for b in _mappings(row.get("bindings"))],
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            dynamic_sites.append((str(row["type"]), dynamic_site))
        elif kind == "dynval" and row.get("file") in known:
            value = DynamicValue(
                what=str(row["what"]),
                symbol=_optional(row.get("symbol")),
                public=bool(row.get("public")),
                types=_strings(row.get("types")),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
            )
            dynamic_values.append((str(row["type"]), value))
        elif kind == "pkg":
            packages.append(
                PackageEntry(
                    project=str(row["project"]),
                    name=str(row["name"]),
                    version=str(row.get("version") or ""),
                    assemblies=_strings(row.get("assemblies")),
                    native=_strings(row.get("native")),
                )
            )
        elif kind == "throw" and row.get("file") in known:
            thrown = ThrowSite(
                exception=str(row["exception"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            throws.append((str(row["type"]), thrown))
        elif kind == "inst" and row.get("file") in known:
            arguments = _strings(row.get("args"))
            key = (str(row["of"]), tuple(arguments))
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > MAX_USES:
                continue
            uses.append(
                GenericUse(
                    symbol=str(row["of"]),
                    arguments=arguments,
                    open=bool(row.get("open")),
                    file=str(row["file"]),
                    line=max(int(row["line"]), 1),
                    member=_optional(row.get("member")),
                )
            )
        elif kind == "test" and row.get("file") in known:
            class_name = str(row["cls"])
            tests.append(
                TestMethod(
                    file=str(row["file"]),
                    class_name=class_name,
                    method=str(row["method"]),
                    framework=str(row["fw"]),
                    project=_optional(row.get("project")),
                    cases=[test_id(class_name, c) for c in _strings(row.get("cases"))],
                    exact=bool(row.get("exact", True)),
                )
            )
    for owner, site in refusals:
        if owner in types:
            types[owner].refusals.append(site)
    for owner, thrown in throws:
        if owner in types:
            types[owner].throws.append(thrown)
    for owner, notice in notices:
        if owner in types:
            types[owner].notices.append(notice)
    for owner, native in imports:
        if owner in types:
            types[owner].native_imports.append(native)
    for owner, call in calls:
        if owner in types:
            types[owner].native_calls.append(call)
    for owner, used in package_uses:
        if owner in types:
            types[owner].package_uses.append(used)
    for owner, dynamic_site in dynamic_sites:
        if owner in types:
            types[owner].dynamic_sites.append(dynamic_site)
    for owner, value in dynamic_values:
        if owner in types:
            types[owner].dynamic_values.append(value)
    return PortRecords(
        types=sorted(types.values(), key=lambda t: t.id),
        uses=sorted(uses, key=lambda u: (u.symbol, u.file, u.line, u.arguments)),
        tests=sorted(tests, key=lambda m: (m.file, m.class_name, m.method)),
        packages=sorted(packages, key=lambda p: (p.project, p.name)),
    )


def _optional(value: object) -> str | None:
    return str(value) if value else None


def _strings(value: object) -> list[str]:
    return [str(v) for v in cast(list[object], value)] if isinstance(value, list) else []


def _mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(Mapping[str, Any], v) for v in cast(list[object], value) if isinstance(v, dict)]


def _binding(row: Mapping[str, Any]) -> DynamicBinding:
    return DynamicBinding(
        types=_strings(row.get("types")),
        member=_optional(row.get("member")),
        member_id=_optional(row.get("member_id")),
        result=_strings(row.get("result")),
        error=_optional(row.get("error")),
    )


def type_sites(index_path: Path, files: list[str], source: str) -> list[RuleSite]:
    """A port's types (``csharp_types``, ``java_types``; D-79, D-92): each type a library
    project or module declares in ``files`` and isn't nested in another, at its declaration in
    each of its files (a C# partial type has several); ``source`` names the language its API
    stays in. The planner cuts the units."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        libraries = {t.name for t in store.build_targets() if t.kind is TargetKind.LIBRARY}
        sources = {f.path for f in store.files(FileRole.SOURCE)}
        sites: list[RuleSite] = []
        for entry in store.port_types():
            if entry.outer is not None or entry.project not in libraries:
                continue
            if any(f not in sources for f in entry.files):
                continue
            lines = {s.file: s.range.start_line for s in store.symbols(symbol_id=entry.id)}
            text = f"{entry.name}: port it, keeping its {source} API as a façade"
            sites += [
                RuleSite(file=f, line=lines.get(f, 1), text=text)
                for f in entry.files
                if f in wanted
            ]
    return sorted(sites, key=lambda s: (s.file, s.line, s.text))
