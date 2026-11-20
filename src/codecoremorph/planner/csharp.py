"""The source side of a port's plan (SPEC §11.9, §11.12, D-77, D-78, D-92): a C# or a Java
library's type units, then its programs, its test files and the retirement, the same
whatever the port's target. What follows says C#; a Java source is cut the same way
(:class:`Source`), but for its generics (a Java façade can't dispatch on an erased type
parameter, so one ports to its bound, or to the one argument every use gives it).

One unit per type, leaves first by the references between the types. A reference cycle is one
unit. So is a class with virtual members together with the project's classes that derive from
it, so a hierarchy ports at once (C# classes outside the library that derive from it run on
directors, D-87). Nested types go with their
outer type, and a partial type is one type. An interface is a unit of its own: the C# classes
that implement it reach the ported code through calls back into C#.

What the first version refuses, a unit at a time and with the reason (D-78): what the index's
refusal sites name (``async`` code, ``dynamic``, reflection, serializers and dependency
injection, expression trees); public generic types and methods that nothing in the project
instantiates; a class with non-private instance fields; a ported constructor that calls a
virtual member a C# class staying C# overrides; and a unit that uses a refused one. Types to
port in more than one library project are refused whole. A C# class staying C# (the tests, a
program) that derives from a ported class and overrides runs on a director (D-87).

A program (a project whose output is an executable) is one unit with all of its types, after the
library types it uses; its code moves whole, so C# outside the program that uses its types
refuses it. A test file is one unit, translated beside the C# tests, which stay until the
retirement; its cases are the ones the sources name exactly, and a data-driven method's cases
are the test report's. The retirement deletes the C#, or with ``keep_source_api`` only the
programs and the tests that need them, the library staying a .NET library over the port.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from codecoremorph.core.models import (
    DynamicSite,
    FileRole,
    GenericUse,
    NativeImport,
    PackageEntry,
    PackageUse,
    PortDependency,
    PortDependencyKind,
    PortNoticeKind,
    Symbol,
    SymbolKind,
    TargetKind,
    TestMethod,
    TypeEntry,
    Visibility,
)
from codecoremorph.planner.graph import condense
from codecoremorph.planner.project import ProjectModel

_VISIBLE = frozenset({Visibility.PUBLIC, Visibility.PROTECTED})


@dataclass(frozen=True)
class Source:
    """The language a port's façades stay in, as its IDs spell symbols and tests and its
    notes name it."""

    language: str
    name: str  # C#, Java
    prefix: str  # its symbol IDs'
    test_prefix: str  # its test IDs'
    constructor: str  # what follows a type's ID in a constructor's
    suffix: str  # its source files'


SOURCES = {
    "csharp": Source("csharp", "C#", "csharp:", "dotnet:", ".#ctor", ".cs"),
    "java": Source("java", "Java", "java:", "junit:", "#<init>", ".java"),
}
# What a Java member takes when it takes a callback: a functional interface of the JDK.
_JAVA_CALLBACKS = re.compile(
    r"\bjava\.util\.function\.\w+|\bjava\.lang\.Runnable\b|\bjava\.util\.Comparator\b"
    r"|\bjava\.util\.concurrent\.Callable\b"
)
_DELEGATES = re.compile(r"\bSystem\.(?:Func|Action|Predicate|Comparison|Converter|EventHandler)\b")
_SHOWN = 3  # sites, uses and implementations a note names before "and N more"

# An operating system's own libraries, by name without extension: the port builds and tests on
# Linux, so code that calls them can't be ported yet (D-82).
_PLATFORM_LIBRARIES = {
    "Windows": frozenset(
        {
            "kernel32", "user32", "advapi32", "gdi32", "ole32", "oleaut32", "shell32", "ntdll",
            "ws2_32", "winmm", "crypt32", "bcrypt", "ncrypt", "secur32", "iphlpapi", "dbghelp",
            "version", "setupapi", "comctl32", "comdlg32", "winspool", "netapi32", "userenv",
            "wtsapi32", "psapi", "rpcrt4", "dwmapi", "uxtheme", "shlwapi", "mpr", "hid",
            "winhttp", "wininet", "msvcrt", "ucrtbase", "api-ms-win-core-sysinfo-l1-1-0",
        }
    ),
    "macOS": frozenset(
        {"libsystem", "corefoundation", "foundation", "security", "libobjc", "iokit", "appkit"}
    ),
}  # fmt: skip
# Packages that are the framework's own (polyfills of what newer frameworks have built in, the
# SDK's packages): no dependency of the port's.
_FRAMEWORK_PACKAGE = re.compile(
    r"^(?:System\.|Microsoft\.Bcl\.|Microsoft\.NETCore\.|Microsoft\.Win32\.|runtime\.)"
    r"|^(?:NETStandard\.Library|Microsoft\.CSharp)$",
    re.IGNORECASE,
)


def library_name(name: str) -> str:
    """A native library's name as its file stem, lowercase: ``kernel32.dll`` is
    ``kernel32``, ``/usr/lib/libSystem.dylib`` ``libsystem``, ``libsodium.so.23``
    ``libsodium``."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(?:dll|dylib|so)(?:\.\d+)*$", "", base)


def platform_of(library: str) -> str | None:
    """The operating system whose own library ``library`` is, or None."""
    base = library_name(library)
    for platform, names in _PLATFORM_LIBRARIES.items():
        if base in names:
            return platform
    return None


_C_RUNTIME = frozenset({"libc", "c", "libm", "m", "libdl", "dl", "libpthread", "pthread", "librt"})


def c_runtime(library: str) -> bool:
    """Whether a P/Invoke names the C library itself (``libc``, ``libm``): every program has
    it, with its standard headers."""
    return library_name(library) in _C_RUNTIME


def framework_package(name: str) -> bool:
    """Whether a package is the framework's own rather than a dependency."""
    return _FRAMEWORK_PACKAGE.search(name) is not None


@dataclass
class TypeUnit:
    """A unit of a C# library's types, before the target names its files."""

    types: list[TypeEntry]  # by ID
    lead: TypeEntry  # what names the unit: a hierarchy's base, else the first type by name
    files: list[str]  # where its types are declared
    callers: list[str]  # files outside the unit that use its types: they call the façades
    exports: list[str]  # its types' public and protected members, by ID
    details: list[str]  # what its note says after naming its files
    needs: list[int] = field(default_factory=list[int])  # the units it uses, by position
    refused: str | None = None  # why the first version refuses it
    native_calls: list[str] = field(default_factory=list[str])  # P/Invoke members it calls

    def native(self, imports: Mapping[str, NativeImport]) -> list[str]:
        """The native libraries its types call that the new code links, by the name the
        P/Invokes give (not the C library, which every program links)."""
        return sorted(
            {
                imports[m].library
                for m in self.native_calls
                if m in imports and not c_runtime(imports[m].library)
            }
        )


@dataclass
class TypeCut:
    """A C# library's type units, leaves first; ``refused`` refuses all of the types."""

    project: str | None  # the library project that compiles them
    units: list[TypeUnit] = field(default_factory=list[TypeUnit])
    files: list[str] = field(default_factory=list[str])  # every type's files
    refused: str | None = None
    types: list[str] = field(default_factory=list[str])  # with ``refused``: what stays, by ID
    # What the library depends on outside its own code (D-82), and the native declarations it
    # needs no unit for.
    dependencies: list[PortDependency] = field(default_factory=list[PortDependency])
    imports: dict[str, NativeImport] = field(default_factory=dict[str, NativeImport])


def display(entry: TypeEntry) -> str:
    """``Zoo.Pen<T>``."""
    if entry.type_parameters:
        return f"{entry.name}<{', '.join(entry.type_parameters)}>"
    return entry.name


_ANONYMOUS = re.compile(r"^(?P<outer>.+)\.\d+$")


def _who(entry: TypeEntry) -> str:
    """A type by name for notes; a Java anonymous class (``shop.CartTest$1``), or a Mockito mock
    the index records as a subclass (``shop.CartTest$mock$shop.Book``), by where it is."""
    if entry.id.startswith("java:") and "$mock$" in entry.id and entry.outer is not None:
        return f"a Mockito mock in {entry.outer.removeprefix('java:').replace('$', '.')}"
    if entry.id.startswith("java:") and (match := _ANONYMOUS.match(entry.name)):
        return f"an anonymous class in {match['outer']}"
    return entry.name


def _implementations(entries: Iterable[TypeEntry]) -> list[str]:
    """Implementations by name and file, a file's anonymous classes counted together."""
    shown: list[str] = []
    anonymous: dict[tuple[str, str], int] = {}
    for entry in entries:
        files = _listed(entry.files, 1)
        if entry.id.startswith("java:") and (match := _ANONYMOUS.match(entry.name)):
            key = (match["outer"], files)
            anonymous[key] = anonymous.get(key, 0) + 1
        else:
            shown.append(f"{entry.name} ({files})")
    for (outer, files), count in sorted(anonymous.items()):
        what = "an anonymous class" if count == 1 else f"{count} anonymous classes"
        shown.append(f"{what} in {outer} ({files})")
    return shown


def _erased(parameters: Sequence[str], uses: int) -> str:
    """What a Java generic's note says of its parameters (D-92): Java erases them, so each
    ports to its bound, or to the one argument the project gives it."""
    parts: list[str] = []
    for parameter in parameters:
        name, _, bound = parameter.partition(" extends ")
        if bound:
            parts.append(f"{name} ports to its bound, {bound}")
        elif uses == 1:
            parts.append(f"{name} ports to the one argument the project gives it")
    ported = _listed(parts) if parts else "its parameters port as the uses give them"
    return (
        f"Java erases the type parameters, so {ported}: one set of exports, and the façade "
        "keeps its type parameters"
    )


def member_name(member_id: str) -> str:
    """A member by name, for notes: ``csharp:Zoo.Pen`1.Fold``1(``0,…)`` is ``Zoo.Pen.Fold``,
    ``java:shop.Cart$Item#add(int)`` is ``shop.Cart.Item.add``."""
    if member_id.startswith("java:"):
        head = member_id.removeprefix("java:").split("(", 1)[0]
        return head.replace("#<init>", "").replace("#", ".").replace("$", ".")
    head = member_id.removeprefix("csharp:").split("(", 1)[0]
    return re.sub(r"`+\d+", "", head)


def _generic_method(member_id: str) -> bool:
    return "``" in member_id.split("(", 1)[0]


def _listed(items: Iterable[str], shown: int = _SHOWN) -> str:
    items = list(items)
    more = f" and {len(items) - shown} more" if len(items) > shown else ""
    return ", ".join(items[:shown]) + more


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


_DYNAMIC_SHOWN = 12  # dynamic sites a note names before "and N more" (D-88)


def dynamic_notes(types: Iterable[TypeEntry], files: Collection[str] | None = None) -> list[str]:
    """What a unit's note says of its dynamic sites and values (D-88): the types that reach
    each site and what C#'s runtime binder binds for each, which the new code makes a closed
    sum. ``files`` limits them to a test unit's files. A site that binds one way for its one
    type is plain code, so the note only counts those."""
    entries = list(types)
    sites = [s for t in entries for s in t.dynamic_sites if files is None or s.file in files]
    values = [
        v
        for t in entries
        for v in t.dynamic_values
        if (files is None or v.file in files) and not v.what.startswith("local ")
    ]
    out: list[str] = []
    telling = [s for s in sites if len(s.bindings) > 1 or any(b.error for b in s.bindings)]
    if telling:
        shown = "; ".join(_dynamic_site(s) for s in telling[:_DYNAMIC_SHOWN])
        more = len(telling) - _DYNAMIC_SHOWN
        out.append(
            "dynamic sites, each a closed sum in the new code over the types that reach it, "
            f"bound as C#'s runtime binder binds them (D-88): {shown}"
            + (f"; and {more} more (the index has them)" if more > 0 else "")
        )
    plain = len(sites) - len(telling)
    if plain:
        out.append(f"{_plural(plain, 'other dynamic site')} with one type and one binding each")
    if values:
        out.append(
            "dynamic values and the types that reach them: "
            + "; ".join(
                f"{v.what} ({v.file}:{v.line}{', public' if v.public else ''}): "
                f"{', '.join(v.types) or 'none'}"
                for v in values
            )
        )
    if any(v.public and v.what.startswith("parameter ") for v in values):
        out.append(
            "a public dynamic parameter takes the types the solution passes it and no other: an "
            "export per kind, the façade switching on the object's type (null throws the "
            "binder's RuntimeBinderException, another type NotSupportedException naming the "
            "types it takes)"
        )
    if any(v.public and not v.what.startswith("parameter ") for v in values):
        out.append(
            "a public dynamic result crosses the bridge as a kind (result_kind) and an out "
            "parameter per kind, the façade returning the boxed value"
        )
    if any(b.error for s in sites for b in s.bindings):
        out.append(
            "where the binder fails for a type that reaches a site, the new code throws "
            "Microsoft.CSharp.RuntimeBinder.RuntimeBinderException there with the binder's "
            "message (the runtime's mirror; CcmExceptions.cs maps it)"
        )
    return out


def _dynamic_site(site: DynamicSite) -> str:
    outcomes: dict[str, list[str]] = {}
    for binding in site.bindings:
        types = " and ".join(binding.types) or "nothing"
        if binding.error:
            outcome = f"RuntimeBinderException ({binding.error})"
        else:
            outcome = binding.member or "nothing"
        outcomes.setdefault(outcome, []).append(types)
    shown = ", ".join(f"{_listed(types, 8)} → {outcome}" for outcome, types in outcomes.items())
    return f"{site.text} ({site.file}:{site.line}): {shown}"


def owner_of(types: Mapping[str, TypeEntry], symbol: str | None) -> str | None:
    """The type a symbol is (or is a member of), when ``types`` has it."""
    head = (symbol or "").split("(", 1)[0]
    while head:
        if head in types:
            return head
        # C# members follow a dot (Zoo.Pen.Fold), Java's a hash (java:shop.Cart#add).
        cut = max(head.rfind("."), head.rfind("#"))
        if cut < 0:
            return None
        head = head[:cut]
    return None


def _refused_sites(entry: TypeEntry) -> str | None:
    if not entry.refusals:
        return None
    sites = _listed(f"{r.detail} ({r.file}:{r.line})" for r in entry.refusals)
    kinds = sorted({r.what.value.replace("_", " ") for r in entry.refusals})
    return f"{entry.name} uses {', '.join(kinds)}: {sites}"


# The target languages' names and guides, for what the plan says of native libraries (D-84).
_TARGETS = {
    "cpp": ("C++", "docs/porting-csharp-to-cpp.md"),
    "rust": ("Rust", "docs/porting-csharp-to-rust.md"),
}


def cut_types(
    project: ProjectModel,
    files: Collection[str],
    skip: Callable[[str], bool],
    target: str = "cpp",
    source: str = "csharp",
) -> TypeCut:
    """The type units of the library types declared in ``files`` (``skip`` leaves out a port's
    new code), for a port from ``source`` to ``target`` (language IDs)."""
    return _Cut(project, files, skip, target, source).cut()


class _Cut:
    def __init__(
        self,
        project: ProjectModel,
        files: Collection[str],
        skip: Callable[[str], bool],
        target: str = "cpp",
        source: str = "csharp",
    ) -> None:
        self.project = project
        self.source = SOURCES[source]
        self.target_name, self.guide = _TARGETS.get(target, (target, "the porting guide"))
        self.types = {t.id: t for t in project.types}
        # What each symbol calls (the index's references by enclosing symbol), read when a
        # director's constructors are checked (D-87).
        self.calls: dict[str, set[str]] | None = None
        kinds = {t.name: t.kind for t in project.targets}
        wanted = set(files)

        def portable(entry: TypeEntry) -> bool:
            # A library's source files declare it; a program's types go with the program, and
            # test projects' with their tests.
            kind = kinds.get(entry.project or "", TargetKind.LIBRARY)
            return (
                kind is TargetKind.LIBRARY
                and bool(entry.files)
                and all(self._source(f) and not skip(f) for f in entry.files)
            )

        candidates = {t.id: t for t in project.types if portable(t)}

        def taken(entry: TypeEntry) -> bool:
            # In the rule's files, or nested in a type that is (a partial type's nested types
            # may be in its other file).
            seen: set[str] = set()
            while entry.id not in seen:
                seen.add(entry.id)
                if any(f in wanted for f in entry.files):
                    return True
                if entry.outer is None or entry.outer not in candidates:
                    return False
                entry = candidates[entry.outer]
            return False

        def outermost(entry: TypeEntry) -> TypeEntry:
            seen: set[str] = set()
            while entry.outer is not None and entry.outer in candidates and entry.id not in seen:
                seen.add(entry.id)
                entry = candidates[entry.outer]
            return entry

        ported = {i: t for i, t in candidates.items() if taken(t)}
        # Types that only declare native functions need no unit: the new code calls the native
        # library itself (D-82). The retirement deletes them with the rest of the C#.
        self.native = {i: t for i, t in ported.items() if outermost(t).native_only}
        self.ported = {i: t for i, t in ported.items() if i not in self.native}
        self.imports = {n.member: n for t in project.types for n in t.native_imports}
        self.checked = {t.name for t in project.targets if t.checked_arithmetic}

    def _source(self, path: str) -> bool:
        entry = self.project.files.get(path)
        return entry is not None and entry.role is FileRole.SOURCE

    # ------------------------------------------------------------------ names

    def owner(self, symbol: str | None) -> str | None:
        """The type a symbol is (or is a member of), when the index has it."""
        return owner_of(self.types, symbol)

    def root(self, type_id: str) -> str:
        """The outermost ported type ``type_id`` is nested in (itself if it isn't)."""
        entry = self.ported[type_id]
        seen = {entry.id}
        while entry.outer is not None and entry.outer in self.ported and entry.outer not in seen:
            entry = self.ported[entry.outer]
            seen.add(entry.id)
        return entry.id

    def overridable(self, type_id: str | None) -> bool:
        """Whether a derived class can override something of ``type_id`` or its bases."""
        seen: set[str] = set()
        while type_id is not None and type_id in self.ported and type_id not in seen:
            seen.add(type_id)
            entry = self.ported[type_id]
            if entry.virtual_members:
                return True
            type_id = entry.base
        return False

    # ------------------------------------------------------------------ the cut

    def cut(self) -> TypeCut:
        ported = self.ported
        if not ported:
            return TypeCut(
                project=None, dependencies=self.dependencies([]), imports=dict(self.imports)
            )
        every_file = sorted({f for t in ported.values() for f in t.files})
        projects = sorted({t.project or "" for t in ported.values()})
        if len(projects) > 1:
            return TypeCut(
                project=None,
                files=every_file,
                refused=f"the types to port are in {len(projects)} library projects "
                f"({_listed(p or '(unknown)' for p in projects)}): a port from "
                f"{self.source.name} takes one for now, since each would need handle types "
                "of its own (D-78)",
                types=sorted(ported),
            )
        if not projects[0]:
            return TypeCut(
                project=None,
                files=every_file,
                refused="the index doesn't say which project compiles the types to port; "
                "index the project again",
                types=sorted(ported),
            )
        roots = sorted({self.root(i) for i in ported})
        edges: dict[str, set[str]] = {r: set() for r in roots}
        family = {r: r for r in roots}  # roots tied by a hierarchy

        def head(r: str) -> str:
            while family[r] != r:
                r = family[r]
            return r

        for entry in ported.values():
            if entry.base in ported and self.overridable(entry.base):
                a, b = self.root(entry.id), self.root(entry.base)
                if a != b:
                    edges[a].add(b)
                    edges[b].add(a)  # one cycle, so one unit
                    family[head(a)] = head(b)
        # Who uses what: a reference from a ported type ties its root to the one it uses; the
        # C# outside the ported types, and the other units, call the façades.
        users: dict[str, set[tuple[str, str | None]]] = {r: set() for r in roots}
        # The native functions each root's code calls (D-82), its own declarations included.
        native_calls: dict[str, set[str]] = {r: set() for r in roots}
        for entry in ported.values():
            native_calls[self.root(entry.id)].update(n.member for n in entry.native_imports)
        for from_file, from_symbol, to_symbol in self.project.symbol_references():
            if to_symbol in self.imports:
                caller = self.owner(from_symbol)
                if caller in ported:
                    native_calls[self.root(caller)].add(to_symbol)
                continue
            to_type = self.owner(to_symbol)
            if to_type is None or to_type not in ported:
                continue
            to_root = self.root(to_type)
            from_type = self.owner(from_symbol)
            from_root = self.root(from_type) if from_type in ported else None
            if from_root == to_root:
                continue
            if from_root is not None:
                edges[from_root].add(to_root)
            users[to_root].add((from_file, from_root))
        # The types that reach a type's dynamic values and sites (D-88): its new code names
        # each, as a closed sum, so they go first, as a reference makes them.
        by_name = {t.name: i for i, t in ported.items()}
        for entry in ported.values():
            reached = {n for v in entry.dynamic_values for n in v.types}
            reached.update(n for s in entry.dynamic_sites for b in s.bindings for n in b.types)
            from_root = self.root(entry.id)
            for name in sorted(reached):
                if name in by_name and (to_root := self.root(by_name[name])) != from_root:
                    edges[from_root].add(to_root)
        graph = condense(roots, edges)
        definitions = self.project.definitions(
            {i for t in self.types.values() for i in (t.id, *t.members)}
        )
        refused_generics = (
            self.erased_generics(definitions)
            if self.source.language == "java"
            else self.unused_generics(definitions)
        )
        units: list[TypeUnit] = []
        for position, cluster in enumerate(graph.clusters):
            members = set(cluster)
            types = sorted(
                (t for t in ported.values() if self.root(t.id) in members), key=lambda t: t.id
            )
            unit_files = sorted({f for t in types for f in t.files})
            callers = sorted(
                {f for r in cluster for f, by in users[r] if by not in members} - set(unit_files)
            )
            leads = [ported[r] for r in cluster if ported[r].base not in members]
            unit = TypeUnit(
                types=types,
                lead=min(leads or [ported[r] for r in cluster], key=lambda t: t.name),
                files=unit_files,
                callers=callers,
                exports=sorted({m for t in types for m in t.members}),
                details=[],
                needs=sorted(graph.depends_on[position]),
                native_calls=sorted({m for r in cluster for m in native_calls[r]}),
            )
            unit.refused = self.refusal(unit, refused_generics) or self.inherited(units, unit)
            if unit.refused is None:
                unit.details = self.details(unit, cluster, head, definitions, len(callers))
            units.append(unit)
        native_files = {f for t in self.native.values() for f in t.files}
        return TypeCut(
            project=projects[0],
            units=units,
            files=sorted({*every_file, *native_files}),
            dependencies=self.dependencies(units),
            imports=dict(self.imports),
        )

    # ------------------------------------------------------------------ refusals

    def refusal(self, unit: TypeUnit, unused: dict[str, str]) -> str | None:
        """Why the first version refuses the unit's own types, or None."""
        reasons: list[str] = []
        for entry in unit.types:
            if (sites := _refused_sites(entry)) is not None:
                reasons.append(sites)
            if entry.kind in (SymbolKind.CLASS, SymbolKind.RECORD) and entry.instance_fields:
                fields = _listed(entry.instance_fields)
                reasons.append(
                    f"{entry.name} has non-private instance fields ({fields}): a façade can't "
                    "forward a field, and a property in its place changes the API"
                )
        reasons += [unused[t.id] for t in unit.types if t.id in unused]
        reasons += [unused[m] for t in unit.types for m in t.members if m in unused]
        for entry in unit.types:
            for package, uses in self.package_uses(entry).items():
                sites = _listed(f"{u.symbol} ({u.file}:{u.line})" for u in uses)
                reasons.append(
                    f"{entry.name} uses the package {self.package_label(entry, package)} "
                    f"({sites}): the new code has no counterpart of a .NET package"
                )
        platform_calls: dict[str, list[NativeImport]] = {}
        for member in unit.native_calls:
            native = self.imports.get(member)
            if native is not None and (platform := platform_of(native.library)) is not None:
                platform_calls.setdefault(platform, []).append(native)
        for platform, calls in sorted(platform_calls.items()):
            shown = _listed(f"{c.library}!{c.entry_point} ({c.file}:{c.line})" for c in calls)
            reasons.append(
                f"{display(unit.lead)} calls {platform}'s own API ({shown}): the port builds and "
                "tests on Linux, where the new code can't call it"
            )
        # Java has no directors yet (D-92): a Java class staying Java that overrides a ported
        # class's member would no longer run its override from the ported code.
        if self.source.language == "java":
            for other, overrides in self.subclasses(unit):
                names = _listed(member_name(m).rsplit(".", 1)[-1] for m in overrides)
                reasons.append(
                    f"{_who(other)} ({_listed(other.files)}) stays Java and extends "
                    f"{self.ported[other.base or ''].name}, overriding {names}: the ported code "
                    "would no longer call the override, and a port from Java has no directors "
                    "yet (D-92)"
                )
        # A C# subclass that overrides gets a director (D-87), unless a ported constructor calls
        # what it overrides: C# runs the override before the subclass's constructor, and a
        # ported constructor runs its own class's.
        for other, overrides in self.subclasses(unit) if self.source.language != "java" else []:
            suffixes = {m[len(other.id) :] for m in overrides}
            for ctor, called in self.constructor_calls(other.base or "", suffixes):
                reasons.append(
                    f"{member_name(ctor)} calls {member_name(called)}, which {other.name} "
                    f"({_listed(other.files)}) overrides: {self.source.name} runs the override "
                    f"before {other.name}'s constructor, and the ported constructor can't (D-87)"
                )
        if not reasons:
            return None
        return "; ".join(reasons) + " (D-78)"

    def subclasses(self, unit: TypeUnit) -> list[tuple[TypeEntry, list[str]]]:
        """The C# classes that stay C# (the tests, a program) and derive from one of the
        unit's types, overriding some of its virtual members: each one's object runs on a
        director (D-87). With the members each overrides."""
        members = {t.id for t in unit.types}
        found: list[tuple[TypeEntry, list[str]]] = []
        for other in sorted(self.types.values(), key=lambda t: t.id):
            if other.id in self.ported or other.base not in members:
                continue
            overrides = self.overrides(other)
            if overrides:
                found.append((other, overrides))
        return found

    def constructor_calls(self, type_id: str, suffixes: set[str]) -> list[tuple[str, str]]:
        """The calls from a constructor of ``type_id`` or a ported base to one of their virtual
        members whose name and parameters ``suffixes`` holds (``.Sound``), as (constructor,
        member)."""
        if self.calls is None:
            self.calls = {}
            for _file, from_symbol, to_symbol in self.project.symbol_references():
                self.calls.setdefault(from_symbol, set()).add(to_symbol)
        chain: list[TypeEntry] = []
        seen: set[str] = set()
        current: str | None = type_id
        while current is not None and current in self.ported and current not in seen:
            seen.add(current)
            chain.append(self.ported[current])
            current = self.ported[current].base
        virtual = {f"{t.id}{s}" for t in chain for s in suffixes} & {
            m for t in chain for m in t.virtual_members
        }
        found: list[tuple[str, str]] = []
        for entry in chain:
            for ctor in entry.members:
                if ctor.startswith(f"{entry.id}{self.source.constructor}"):
                    found += [(ctor, m) for m in sorted(self.calls.get(ctor, set()) & virtual)]
        return found

    def overrides(self, derived: TypeEntry) -> list[str]:
        """The members of ``derived`` that override a ported base's virtual members."""
        virtual: set[str] = set()
        base, seen = derived.base, set[str]()
        while base is not None and base in self.ported and base not in seen:
            seen.add(base)
            entry = self.ported[base]
            virtual |= {m[len(entry.id) :] for m in entry.virtual_members if m.startswith(entry.id)}
            base = entry.base
        return [m for m in derived.members if m[len(derived.id) :] in virtual]

    def unused_generics(self, definitions: dict[str, Symbol]) -> dict[str, str]:
        """The public generic types and methods nothing in the project instantiates, with why
        each is refused: a use counts when its type arguments are closed, or when it is in
        generic code that something instantiates."""
        used = {u.symbol for u in self.project.generic_uses if not u.open}
        open_uses: list[GenericUse] = [u for u in self.project.generic_uses if u.open]
        changed = True
        while changed:
            changed = False
            for use in open_uses:
                if use.symbol in used or use.member is None:
                    continue
                owners = {use.member, *self.enclosing(self.owner(use.member))}
                if owners & used:
                    used.add(use.symbol)
                    changed = True
        refused: dict[str, str] = {}
        for entry in self.ported.values():
            if (
                entry.type_parameters
                and entry.id not in used
                and self.visible(entry.id, definitions)
            ):
                refused[entry.id] = (
                    f"{display(entry)} is public and generic, and nothing in the project "
                    "instantiates it: a façade exports each instantiation in use, and it has none"
                )
            for member in entry.members:
                if (
                    _generic_method(member)
                    and member not in used
                    and self.visible(member, definitions)
                ):
                    refused[member] = (
                        f"{member_name(member)} is public and generic, and nothing in the project "
                        "calls it with type arguments: a façade exports each instantiation in "
                        "use, and it has none"
                    )
        return refused

    def erased_generics(self, definitions: dict[str, Symbol]) -> dict[str, str]:
        """A Java source's generics (D-92): Java erases a type parameter, so a façade can't
        dispatch on it as a C# one does on ``typeof(T)``. A parameter bounded by a type ports to
        its bound; an unbounded one of a public type ports where every closed use gives it the
        same argument (the port is of that instantiation), and is refused otherwise."""
        arguments: dict[str, set[tuple[str, ...]]] = {}
        for use in self.project.generic_uses:
            if not use.open:
                arguments.setdefault(use.symbol, set()).add(tuple(use.arguments))
        refused: dict[str, str] = {}
        for entry in self.ported.values():
            if not entry.type_parameters or not self.visible(entry.id, definitions):
                continue
            free = [p for p in entry.type_parameters if " extends " not in p]
            if not free:
                continue
            seen = sorted(arguments.get(entry.id, set()))
            if len(seen) == 1:
                continue
            uses = (
                "nothing in the project instantiates it"
                if not seen
                else f"the project gives it {len(seen)} different arguments "
                f"({_listed('<' + ', '.join(a) + '>' for a in seen)})"
            )
            refused[entry.id] = (
                f"{display(entry)} is public and generic, with no bound on {', '.join(free)}, and "
                f"{uses}: Java erases the parameter, so its façade can't tell which "
                "instantiation an object is (D-92)"
            )
        return refused

    def enclosing(self, type_id: str | None) -> list[str]:
        """``type_id`` and the types it is nested in."""
        found: list[str] = []
        while type_id is not None and type_id in self.types and type_id not in found:
            found.append(type_id)
            type_id = self.types[type_id].outer
        return found

    def visible(self, symbol_id: str, definitions: dict[str, Symbol]) -> bool:
        """Whether code outside the library sees the symbol: it, and each type it is in, is
        public or protected (a symbol the index lacks counts as visible)."""
        if symbol_id in self.types:
            chain = self.enclosing(symbol_id)
        else:
            chain = [symbol_id, *self.enclosing(self.owner(symbol_id))]
        return all(definitions[s].visibility in _VISIBLE for s in chain if s in definitions)

    def inherited(self, units: list[TypeUnit], unit: TypeUnit) -> str | None:
        """A unit that uses a refused unit is refused too: the ported code can't use the
        source's."""
        refused = [units[i] for i in unit.needs if units[i].refused is not None]
        if not refused:
            return None
        tops = [t for t in unit.types if t.outer is None or t.outer not in self.ported]
        names = _listed(display(u.lead) for u in refused)
        where = "its unit" if len(refused) == 1 else "their units"
        verb = "uses" if len(tops) == 1 else "use"
        return (
            f"{_listed(display(t) for t in tops)} {verb} {names}, which this port refuses "
            f"(see {where})"
        )

    # ------------------------------------------------------------------ notes

    def details(
        self,
        unit: TypeUnit,
        cluster: tuple[str, ...],
        family: Callable[[str], str],
        definitions: dict[str, Symbol],
        callers: int,
    ) -> list[str]:
        """What a unit's note says after naming its files: what to know to port it."""
        ported = self.ported
        out = [_plural(len(unit.exports), "public member")]
        nested = [t for t in unit.types if t.outer is not None and t.outer in ported]
        if nested:
            # A Java anonymous class (``Report.1``) is counted, not named by its number.
            java = [t for t in nested if t.id.startswith("java:")]
            named = [t.name for t in nested if t not in java or not _ANONYMOUS.match(t.name)]
            anonymous = len(nested) - len(named)
            some = "an anonymous class" if anonymous == 1 else f"{anonymous} anonymous classes"
            if not named:
                out.append(f"with {some} of its own")
            else:
                more = f" and {some}" if anonymous else ""
                out.append(f"with its nested types {_listed(named)}{more}")
        derived = [t for t in unit.types if t.base in {u.id for u in unit.types}]
        if derived:
            bases = sorted({ported[t.base or ""].name for t in derived})
            out.append(
                f"{_listed(bases)} {'has' if len(bases) == 1 else 'have'} virtual members, so "
                f"the classes that derive from {'it' if len(bases) == 1 else 'them'} come with "
                f"{'it' if len(bases) == 1 else 'them'} ({_listed(t.name for t in derived)})"
            )
        if len({family(r) for r in cluster}) > 1:
            out.append(
                "a reference cycle: "
                f"{_listed(sorted(ported[r].name for r in cluster), 6)} use each other"
            )
        for entry in unit.types:
            if entry.kind is SymbolKind.STRUCT:
                out.append(f"{entry.name} is a value type: it crosses the bridge by its fields")
        out += self.instantiations(unit)
        state = [member_name(s) for t in unit.types for s in t.static_state]
        if state:
            out.append(f"static state: {_listed(state)}")
        thrown = sorted({site.exception for t in unit.types for site in t.throws})
        if thrown:
            out.append(
                f"throws {_listed(thrown, 6)}: the ported code throws their mirrors, and the "
                f"façade throws them again in {self.source.name}"
            )
        callbacks = self.callbacks(unit, definitions)
        if callbacks:
            out.append(f"calls into {self.source.name}: {'; '.join(callbacks)}")
        for other, overrides in self.subclasses(unit) if self.source.language != "java" else []:
            names = _listed(member_name(m).rsplit(".", 1)[-1] for m in overrides)
            out.append(
                f"{other.name} ({_listed(other.files)}) stays {self.source.name} and "
                f"derives from {ported[other.base or ''].name}, overriding {names}: its objects "
                f"run on a director of {ported[other.base or ''].name}, whose virtual members "
                f"call the {self.source.name} object's, with a base export per virtual member "
                "(D-87)"
            )
        out += dynamic_notes(unit.types)
        out += self.takeovers(unit)
        if callers:
            out.append(
                f"{_plural(callers, 'file keeps', 'files keep')} using it through the façade"
            )
        return out

    # ------------------------------------------------------------------ what it depends on (D-82)

    def package_uses(self, entry: TypeEntry) -> dict[str, list[PackageUse]]:
        """The uses of each package ``entry`` uses that is no part of the framework."""
        found: dict[str, list[PackageUse]] = {}
        for use in entry.package_uses:
            if not framework_package(use.package):
                found.setdefault(use.package, []).append(use)
        return found

    def package_version(self, project: str | None, package: str) -> str | None:
        for entry in self.project.packages:
            if entry.name == package and (project is None or entry.project == project):
                return entry.version or None
        return None

    def package_label(self, entry: TypeEntry, package: str) -> str:
        version = self.package_version(entry.project, package)
        return f"{package} {version}" if version else package

    def takeovers(self, unit: TypeUnit) -> list[str]:
        """What the unit's note says of what the port takes over that the C# runtime did: deep
        copies through a serializer, unsafe code, platform checks, native calls, and checked
        arithmetic."""
        out: list[str] = []
        notices = [(t, n) for t in unit.types for n in t.notices]
        copies = [n for _, n in notices if n.what is PortNoticeKind.DEEP_COPY]
        for copy in copies:
            where = member_name(copy.member) if copy.member else f"{copy.file}:{copy.line}"
            out.append(
                f"{where} makes {copy.detail} ({copy.file}:{copy.line}): the new code copies the "
                "object graph instead, each object the copy reaches a new one"
            )
        unsafe = [n for _, n in notices if n.what is PortNoticeKind.UNSAFE]
        if unsafe:
            sites = _listed(f"{n.detail} ({n.file}:{n.line})" for n in unsafe)
            out.append(
                f"unsafe code: {sites}: plain pointers in the new code, where nothing moves "
                "objects, so `fixed` pins nothing; bytes read through a cast pointer are copied "
                "instead, and a pinned string's UTF-16 units aren't a UTF-8 string's bytes"
            )
        platform = [n for _, n in notices if n.what is PortNoticeKind.PLATFORM]
        if platform:
            sites = _listed(f"{n.detail} ({n.file}:{n.line})" for n in platform)
            out.append(
                f"asks which platform it runs on or loads native code itself ({sites}): the new "
                "code is built for its platform and links its libraries, so it has nothing to "
                "ask or load"
            )
        by_library: dict[str, list[str]] = {}
        for member in unit.native_calls:
            native = self.imports.get(member)
            if native is not None and platform_of(native.library) is None:
                by_library.setdefault(native.library, []).append(native.entry_point)
        for library, entries in sorted(by_library.items()):
            how = (
                "with the C library's standard headers"
                if c_runtime(library)
                else f"with {library}'s C header, and the scaffold links it"
            )
            out.append(
                f"calls {library}'s {_listed(sorted(set(entries)), 4)} through P/Invoke: the new "
                f"code calls them itself, {how}"
            )
        if any(t.project in self.checked for t in unit.types):
            out.append(
                "the project checks integer overflow (CheckForOverflowUnderflow): where the C# "
                "would throw System.OverflowException, outside `unchecked`, the new code throws "
                "its mirror"
            )
        return out

    def dependencies(self, units: list[TypeUnit]) -> list[PortDependency]:
        """What the library depends on outside its own code, and what the port does about each
        (D-82): the native libraries its P/Invokes name, and the .NET packages its types use."""
        names = {t.id: t.name for t in self.project.types}
        callers: dict[str, set[str]] = {}
        refused_callers: dict[str, set[str]] = {}
        for unit in units:
            for member in unit.native_calls:
                callers.setdefault(member, set()).add(display(unit.lead))
                if unit.refused is not None:
                    refused_callers.setdefault(member, set()).add(display(unit.lead))
        by_library: dict[str, list[NativeImport]] = {}
        for native in self.imports.values():
            by_library.setdefault(native.library, []).append(native)
        out: list[PortDependency] = []
        for library, imports in sorted(by_library.items()):
            declared = sorted(
                {self.declaring_name(n.member, names) for n in imports}, key=str.lower
            )
            called = [n for n in imports if n.member in callers]
            users = sorted({u for n in called for u in callers[n.member]})
            blocked = sorted({u for n in called for u in refused_callers.get(n.member, set())})
            platform = platform_of(library)
            functions = _plural(len(imports), "function")
            if platform is not None:
                if not called:
                    summary = (
                        f"{library} ({platform}'s own library): {functions} declared in "
                        f"{_listed(declared)}, which nothing calls, so they go with the "
                        "retirement"
                    )
                else:
                    summary = (
                        f"{library} ({platform}'s own library): {functions} declared in "
                        f"{_listed(declared)}, called by {_listed(users)}, which stay C#: the "
                        "port builds and tests on Linux, where the new code can't call it"
                    )
                out.append(
                    PortDependency(
                        kind=PortDependencyKind.PLATFORM_API,
                        name=library,
                        ported=not called,
                        summary=summary,
                        used_by=users,
                    )
                )
                continue
            if c_runtime(library):
                out.append(
                    PortDependency(
                        kind=PortDependencyKind.NATIVE_LIBRARY,
                        name=library,
                        ported=True,
                        summary=(
                            f"{library} (the C library, through P/Invoke): {functions} declared "
                            f"in {_listed(declared)}, {len(called)} of them called by "
                            f"{_listed(users) if users else 'nothing'}; the new code calls them "
                            "itself with the C library's standard headers, and the declarations "
                            "go with the retirement"
                        ),
                        used_by=users,
                    )
                )
                continue
            package = self.native_package(library)
            probe = next((p for p in self.project.native_probes if p.library == library), None)
            loads = (
                f"the C# loads it from the package {package.name} {package.version} "
                f"({_listed(sorted({f.split(':', 1)[0] for f in package.native}), 6)}), its "
                "own copy, which the C# not ported yet goes on calling until the retirement, "
                "so what initializes the library is done for both copies"
                if package is not None
                else "the C# loads it from the system"
            )
            summary = (
                f"{library} (a native library, through P/Invoke): {functions} declared in "
                f"{_listed(declared)}, {len(called)} of them called by "
                f"{_listed(users) if users else 'nothing'}; {loads}. The new code calls it "
                "itself: the declarations need no port and go with the retirement, and the new "
                "code includes its C header and links it, so the build needs its development "
                "files"
            )
            action: str | None = None
            if probe is None:
                summary += " (not looked for: only a Docker run's image is)"
            elif probe.header is None or probe.link is None:
                missing = " and ".join(
                    what
                    for what, there in (("its header", probe.header), ("the library", probe.link))
                    if there is None
                )
                summary += f": the toolchain image lacks {missing}"
                wanted = f" {package.version}" if package is not None else ""
                action = (
                    f"the units that call {library} need its development files{wanted} in the "
                    f"toolchain image, which lacks {missing}: build an image from "
                    "codecoremorph-csharp-port with them and set sandbox.image to it (see "
                    f"{self.guide}, Native libraries)"
                )
            else:
                version = f", version {probe.version}" if probe.version else ""
                summary += f": the toolchain image has them ({probe.header}, {probe.link}{version})"
                if (
                    package is not None
                    and probe.version
                    and not package.version.startswith(probe.version)
                ):
                    summary += (
                        f", though the C# loads {package.version}: the {self.target_name} "
                        "tests run the image's version"
                    )
            if blocked:
                summary += f"; {_listed(blocked)} stay C# for reasons of their own"
            out.append(
                PortDependency(
                    kind=PortDependencyKind.NATIVE_LIBRARY,
                    name=library,
                    version=package.version if package is not None else None,
                    ported=action is None,
                    summary=summary,
                    used_by=users,
                    action=action,
                )
            )
        packages: dict[str, dict[str, list[PackageUse]]] = {}
        for entry in self.ported.values():
            for package, uses in self.package_uses(entry).items():
                packages.setdefault(package, {})[entry.name] = uses
        for package, by_type in sorted(packages.items()):
            version = self.package_version(None, package)
            symbols = sorted({u.symbol for uses in by_type.values() for u in uses})
            out.append(
                PortDependency(
                    kind=PortDependencyKind.PACKAGE,
                    name=package,
                    version=version,
                    ported=False,
                    summary=(
                        f"{package}{' ' + version if version else ''} (a .NET package): used by "
                        f"{_listed(sorted(by_type))} ({_listed(symbols)}); the new code has no "
                        "counterpart of a .NET package, so they stay C#"
                    ),
                    used_by=sorted(by_type),
                )
            )
        return out

    def declaring_name(self, member: str, names: Mapping[str, str]) -> str:
        """The outermost type that declares a native function, by name."""
        owner = self.owner(member)
        seen: set[str] = set()
        while owner is not None and owner not in seen:
            seen.add(owner)
            outer = self.types[owner].outer if owner in self.types else None
            if outer is None:
                break
            owner = outer
        return names.get(owner or "", owner or member)

    def native_package(self, library: str) -> PackageEntry | None:
        """The package whose native files a P/Invoke of ``library`` loads, if any."""
        wanted = library_name(library)
        for package in self.project.packages:
            files = [f.split(":", 1)[-1] for f in package.native]
            stems = (wanted, f"lib{wanted}", wanted.removeprefix("lib"))
            if any(library_name(f) in stems for f in files):
                return package
        return None

    def instantiations(self, unit: TypeUnit) -> list[str]:
        """Each generic of the unit with the type arguments it is used with."""
        generics = [t.id for t in unit.types if t.type_parameters]
        generics += [m for t in unit.types for m in t.members if _generic_method(m)]
        out: list[str] = []
        for symbol in generics:
            arguments = sorted(
                {
                    f"<{', '.join(u.arguments)}>"
                    for u in self.project.generic_uses
                    if u.symbol == symbol and not u.open
                }
            )
            if not arguments:
                continue
            name = display(self.types[symbol]) if symbol in self.types else member_name(symbol)
            if self.source.language == "java" and symbol in self.types:
                out.append(f"{name} is generic, used with {_listed(arguments, 6)}: " + _erased(
                    self.types[symbol].type_parameters, len(arguments)
                ))  # fmt: skip
                continue
            out.append(
                f"{name} is generic, used with {_listed(arguments, 6)}: one set of exports per "
                "type argument"
            )
        return out

    def callbacks(self, unit: TypeUnit, definitions: dict[str, Symbol]) -> list[str]:
        """What reaches C# from the unit's code: delegates, events, and the C# implementations
        of its interfaces."""
        # A Java member that takes one of the project's interfaces may be given a lambda or a
        # Java implementation: either is a call back into Java.
        callable_kind = SymbolKind.INTERFACE if self.source.language == "java" else None
        delegates = [
            re.escape(t.name.rsplit(".", 1)[-1] if callable_kind else t.name)
            for t in self.types.values()
            if t.kind is (callable_kind or SymbolKind.DELEGATE)
        ]
        own = re.compile(rf"\b(?:{'|'.join(delegates)})\b") if delegates else None
        taking: list[str] = []
        events: list[str] = []
        for member in unit.exports:
            symbol = definitions.get(member)
            if symbol is None:
                continue
            if symbol.kind is SymbolKind.EVENT:
                events.append(member_name(member))
            elif callable_kind and own and own.search(member.partition("(")[2]):
                taking.append(member_name(member))  # a Java ID spells its parameters' types
            elif symbol.signature and (
                _DELEGATES.search(symbol.signature)
                or _JAVA_CALLBACKS.search(symbol.signature)
                or (own and own.search(symbol.signature))
            ):
                taking.append(member_name(member))
        out: list[str] = []
        taking = list(dict.fromkeys(taking))  # overloads, a constructor's included, named once
        if taking:
            what = "a callback" if self.source.language == "java" else "a delegate"
            out.append(f"{_listed(taking)} {'takes' if len(taking) == 1 else 'take'} {what}")
        if events:
            out.append(f"{'the event' if len(events) == 1 else 'the events'} {_listed(events)}")
        inside = {t.id for t in unit.types}
        for entry in unit.types:
            if entry.kind is not SymbolKind.INTERFACE:
                continue
            implementations = sorted(
                (t for t in self.types.values() if entry.id in t.interfaces and t.id not in inside),
                key=lambda t: t.name,
            )
            if implementations:
                shown = _listed(_implementations(implementations))
                out.append(
                    f"{entry.name} is implemented in {self.source.name} by {shown}, which "
                    f"reach the ported code through a proxy while they are {self.source.name}"
                )
        return out


# ---------------------------------------------------------------------------- programs, tests


@dataclass
class ProgramUnit:
    """A C# program (a project whose output is an executable), ported whole with its types."""

    project: str  # its project file
    name: str  # the project file's name without its suffix: the target program's name
    files: list[str]  # its source files
    uses: list[str]  # the library types it uses, by ID
    details: list[str]  # what its note says after naming its files
    refused: str | None = None


@dataclass
class TestFileUnit:
    """A C# test file, translated into the target's tests beside it."""

    __test__ = False  # not a pytest test class

    file: str
    project: str | None  # the test project that compiles it
    cases: list[str]  # its cases the sources name exactly and the test map lacks, by test ID
    data_driven: list[str]  # "Class.Method" of methods whose cases only the test report names
    helpers: list[str]  # test files without tests that it uses: test doubles, fixtures
    uses: list[str]  # the library types it uses, by ID
    data_driven_ids: list[str] = field(default_factory=list[str])  # their test ID stems


@dataclass
class Retirement:
    """What a C# port's retirement deletes, and what it keeps."""

    deleted: list[str]  # the C# files and the project, solution and MSBuild files
    edited: list[str]  # the solutions it takes the deleted projects out of
    projects: list[str]  # the projects it deletes
    kept: list[str]  # the projects that stay (keep_source_api)


def _role(project: ProjectModel, path: str) -> FileRole | None:
    entry = project.files.get(path)
    return entry.role if entry is not None else None


def project_name(project_file: str) -> str:
    """``src/Zoo.Cli/Zoo.Cli.csproj`` gives ``Zoo.Cli``."""
    return re.sub(r"\.[A-Za-z]+proj$", "", posixpath.basename(project_file))


def cut_programs(
    project: ProjectModel, files: Collection[str], skip: Callable[[str], bool]
) -> list[ProgramUnit]:
    """The programs with a file in ``files`` (the rule's sites are at each one's ``Main``), by
    project, each with all of its source files (``skip`` leaves out a port's new code)."""
    wanted = set(files)
    types = {t.id: t for t in project.types}
    libraries = {t.name for t in project.targets if t.kind is TargetKind.LIBRARY}
    programs = [
        t
        for t in sorted(project.targets, key=lambda t: t.name)
        if t.kind is TargetKind.EXECUTABLE and wanted.intersection(t.files)
    ]
    if not programs:
        return []
    references = project.symbol_references()
    units: list[ProgramUnit] = []
    for target in programs:
        sources = sorted(
            f for f in target.files if _role(project, f) is FileRole.SOURCE and not skip(f)
        )
        if not sources:
            continue
        inside = set(target.files)
        own = sorted((t for t in types.values() if t.project == target.name), key=lambda t: t.id)
        own_ids = {t.id for t in own}
        uses: set[str] = set()
        outside: dict[str, set[str]] = {}  # its types that C# outside it uses, and where
        for from_file, _, to_symbol in references:
            to_type = owner_of(types, to_symbol)
            if to_type is None:
                continue
            if from_file in inside and types[to_type].project in libraries:
                uses.add(to_type)
            elif from_file not in inside and to_type in own_ids:
                outside.setdefault(to_type, set()).add(from_file)
        reasons = [r for t in own if (r := _refused_sites(t)) is not None]
        for type_id, users in sorted(outside.items()):
            reasons.append(
                f"{types[type_id].name} is used outside the program ({_listed(sorted(users))}): "
                "a program's code moves whole, and that C# would lose it"
            )
        details = [_plural(len(sources), "file")]
        tops = sorted(display(t) for t in own if t.outer is None)
        if tops:
            details.append(f"its types {_listed(tops, 6)}")
        if uses:
            shown = sorted(display(types[u]) for u in uses if types[u].outer is None)
            details.append(f"uses the library's {_listed(shown, 6)}")
        thrown = sorted({site.exception for t in own for site in t.throws})
        if thrown:
            details.append(f"throws {_listed(thrown, 6)}")
        units.append(
            ProgramUnit(
                project=target.name,
                name=project_name(target.name),
                files=sources,
                uses=sorted(uses),
                details=details,
                refused="; ".join(reasons) + " (D-78)" if reasons else None,
            )
        )
    return units


def _mapped_method(method: TestMethod, mapped: Collection[str], prefix: str = "dotnet:") -> bool:
    """Whether the test map has a case of a data-driven method (one earlier run mapped):
    ``M(1)`` in a TRX report, ``m[1]`` or ``m(int)[1]`` in a Surefire one."""
    stem = f"{prefix}{method.class_name}#{method.method}"
    return any(m == stem or m.startswith((stem + "(", stem + "[")) for m in mapped)


def cut_tests(
    project: ProjectModel,
    files: Collection[str],
    mapped: Collection[str],
    source: str = "csharp",
) -> list[TestFileUnit]:
    """The test files in ``files`` that have test methods, by path, each with its cases the
    test map (``mapped``) lacks; a file whose cases it has them all for has no unit."""
    prefix = SOURCES[source].test_prefix
    wanted = set(files)
    by_file: dict[str, list[TestMethod]] = {}
    for method in project.test_methods:
        if method.file in wanted:
            by_file.setdefault(method.file, []).append(method)
    if not by_file:
        return []
    with_tests = {m.file for m in project.test_methods}
    types = {t.id: t for t in project.types}
    libraries = {t.name for t in project.targets if t.kind is TargetKind.LIBRARY}
    used: dict[str, set[str]] = {}  # file -> the types it uses
    for from_file, _, to_symbol in project.symbol_references():
        to_type = owner_of(types, to_symbol)
        if to_type is not None:
            used.setdefault(from_file, set()).add(to_type)
    known = set(mapped)
    units: list[TestFileUnit] = []
    for path in sorted(by_file):
        methods = sorted(by_file[path], key=lambda m: (m.class_name, m.method))
        cases = sorted({c for m in methods if m.exact for c in m.cases} - known)
        open_methods = [m for m in methods if not m.exact and not _mapped_method(m, known, prefix)]
        data_driven = sorted(f"{m.class_name}.{m.method}" for m in open_methods)
        if not cases and not data_driven:
            continue  # an earlier run translated it: the test map has every case
        helpers: list[str] = []
        uses: set[str] = set()
        queue, seen = [path], {path}
        while queue:
            for type_id in sorted(used.get(queue.pop(0), ())):
                entry = types[type_id]
                if entry.project in libraries:
                    uses.add(type_id)
                    continue
                for f in entry.files:
                    if f not in seen and _role(project, f) is FileRole.TEST and f not in with_tests:
                        seen.add(f)
                        helpers.append(f)
                        queue.append(f)
        units.append(
            TestFileUnit(
                file=path,
                project=next((m.project for m in methods if m.project), None),
                cases=cases,
                data_driven=data_driven,
                helpers=sorted(helpers),
                uses=sorted(uses),
                data_driven_ids=sorted(
                    {f"{prefix}{m.class_name}#{m.method}" for m in open_methods}
                ),
            )
        )
    return units


def cut_retirement(project: ProjectModel, files: Collection[str], keep: bool) -> Retirement:
    """What the retirement deletes of ``files`` (the C# left) and the build: everything, or with
    ``keep`` (``keep_source_api``) the programs, which the target's programs replace, and the
    test projects that reference them, the solutions dropping them."""
    code = sorted(f for f in files if f in project.files)
    projects = sorted(project.targets, key=lambda t: t.name)
    if not keep:
        return Retirement(
            deleted=sorted({*code, *project.build_files}),
            edited=[],
            projects=[t.name for t in projects],
            kept=[],
        )
    doomed = {t.name for t in projects if t.kind is TargetKind.EXECUTABLE}
    changed = bool(doomed)
    while changed:
        changed = False
        for target in projects:
            if (
                target.name not in doomed
                and target.kind is TargetKind.TEST
                and doomed.intersection(target.dependencies)
            ):
                doomed.add(target.name)
                changed = True
    kept = [t.name for t in projects if t.name not in doomed]
    if not doomed:
        return Retirement(deleted=[], edited=[], projects=[], kept=kept)
    wanted = set(code)
    deleted = {f for t in projects if t.name in doomed for f in t.files if f in wanted}
    solutions = [f for f in project.build_files if f.lower().endswith((".sln", ".slnx"))]
    return Retirement(
        deleted=sorted(deleted | doomed),
        edited=sorted(solutions),
        projects=sorted(doomed),
        kept=kept,
    )
