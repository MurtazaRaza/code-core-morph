"""The C# side of a port's plan (SPEC §11.9, D-77, D-78): a C# library's type units, then its
programs, its test files and the retirement, the same whatever the port's target.

One unit per type, leaves first by the references between the types. A reference cycle is one
unit. So is a class with virtual members together with the project's classes that derive from
it, since the target can't dispatch a virtual call to a C# override. Nested types go with their
outer type, and a partial type is one type. An interface is a unit of its own: the C# classes
that implement it reach the ported code through calls back into C#.

What the first version refuses, a unit at a time and with the reason (D-78): what the index's
refusal sites name (``async`` code, ``dynamic``, reflection, serializers and dependency
injection, ``unsafe`` code, expression trees); public generic types and methods that nothing in
the project instantiates; a class with non-private instance fields; a class that C# staying C#
(the tests, a program) derives from and overrides; and a unit that uses a refused one. Types to
port in more than one library project are refused whole.

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
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field

from codecoremorph.core.models import (
    FileRole,
    GenericUse,
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
_DELEGATES = re.compile(r"\bSystem\.(?:Func|Action|Predicate|Comparison|Converter|EventHandler)\b")
_SHOWN = 3  # sites, uses and implementations a note names before "and N more"


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


@dataclass
class TypeCut:
    """A C# library's type units, leaves first; ``refused`` refuses all of the types."""

    project: str | None  # the library project that compiles them
    units: list[TypeUnit] = field(default_factory=list[TypeUnit])
    files: list[str] = field(default_factory=list[str])  # every type's files
    refused: str | None = None


def display(entry: TypeEntry) -> str:
    """``Zoo.Pen<T>``."""
    if entry.type_parameters:
        return f"{entry.name}<{', '.join(entry.type_parameters)}>"
    return entry.name


def member_name(member_id: str) -> str:
    """A member by name, for notes: ``csharp:Zoo.Pen`1.Fold``1(``0,…)`` is ``Zoo.Pen.Fold``."""
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


def owner_of(types: Mapping[str, TypeEntry], symbol: str | None) -> str | None:
    """The type a symbol is (or is a member of), when ``types`` has it."""
    head = (symbol or "").split("(", 1)[0]
    while head:
        if head in types:
            return head
        cut = head.rfind(".")
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


def cut_types(
    project: ProjectModel, files: Collection[str], skip: Callable[[str], bool]
) -> TypeCut:
    """The type units of the library types declared in ``files`` (``skip`` leaves out a port's
    new code)."""
    return _Cut(project, files, skip).cut()


class _Cut:
    def __init__(
        self, project: ProjectModel, files: Collection[str], skip: Callable[[str], bool]
    ) -> None:
        self.project = project
        self.types = {t.id: t for t in project.types}
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

        self.ported = {i: t for i, t in candidates.items() if taken(t)}

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
            return TypeCut(project=None)
        every_file = sorted({f for t in ported.values() for f in t.files})
        projects = sorted({t.project or "" for t in ported.values()})
        if len(projects) > 1:
            return TypeCut(
                project=None,
                files=every_file,
                refused=f"the types to port are in {len(projects)} library projects "
                f"({_listed(p or '(unknown)' for p in projects)}): a port from C# takes one for "
                "now, since each would need handle types of its own (D-78)",
            )
        if not projects[0]:
            return TypeCut(
                project=None,
                files=every_file,
                refused="the index doesn't say which project compiles the types to port; "
                "index the project again",
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
        for from_file, from_symbol, to_symbol in self.project.symbol_references():
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
        graph = condense(roots, edges)
        definitions = self.project.definitions(
            {i for t in self.types.values() for i in (t.id, *t.members)}
        )
        refused_generics = self.unused_generics(definitions)
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
            )
            unit.refused = self.refusal(unit, refused_generics) or self.inherited(units, unit)
            if unit.refused is None:
                unit.details = self.details(unit, cluster, head, definitions, len(callers))
            units.append(unit)
        return TypeCut(project=projects[0], units=units, files=every_file)

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
        members = {t.id for t in unit.types}
        for other in sorted(self.types.values(), key=lambda t: t.id):
            if other.id in self.ported or other.base not in members:
                continue
            overrides = self.overrides(other)
            if overrides:
                names = _listed(member_name(m).rsplit(".", 1)[-1] for m in overrides)
                reasons.append(
                    f"{self.types[other.base or ''].name} has a subclass that stays C# and "
                    f"overrides it, {other.name} ({_listed(other.files)}, overriding {names}): "
                    "the ported code can't call a C# override"
                )
        if not reasons:
            return None
        return "; ".join(reasons) + " (D-78)"

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
        """A unit that uses a refused unit is refused too: the ported code can't use C#."""
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
            out.append(f"with its nested types {_listed(t.name for t in nested)}")
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
                "façade throws them again in C#"
            )
        callbacks = self.callbacks(unit, definitions)
        if callbacks:
            out.append(f"calls into C#: {'; '.join(callbacks)}")
        if callers:
            out.append(
                f"{_plural(callers, 'file keeps', 'files keep')} using it through the façade"
            )
        return out

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
            out.append(
                f"{name} is generic, used with {_listed(arguments, 6)}: one set of exports per "
                "type argument"
            )
        return out

    def callbacks(self, unit: TypeUnit, definitions: dict[str, Symbol]) -> list[str]:
        """What reaches C# from the unit's code: delegates, events, and the C# implementations
        of its interfaces."""
        delegates = [
            re.escape(t.name) for t in self.types.values() if t.kind is SymbolKind.DELEGATE
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
            elif symbol.signature and (
                _DELEGATES.search(symbol.signature) or (own and own.search(symbol.signature))
            ):
                taking.append(member_name(member))
        out: list[str] = []
        if taking:
            out.append(f"{_listed(taking)} {'takes' if len(taking) == 1 else 'take'} a delegate")
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
                shown = _listed(f"{t.name} ({_listed(t.files, 1)})" for t in implementations)
                out.append(
                    f"{entry.name} is implemented in C# by {shown}, which reach the ported code "
                    "through a proxy while they are C#"
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


def _mapped_method(method: TestMethod, mapped: Collection[str]) -> bool:
    """Whether the test map has a case of a data-driven method (one earlier run mapped)."""
    stem = f"dotnet:{method.class_name}#{method.method}"
    return any(m == stem or m.startswith(stem + "(") for m in mapped)


def cut_tests(
    project: ProjectModel, files: Collection[str], mapped: Collection[str]
) -> list[TestFileUnit]:
    """The test files in ``files`` that have test methods, by path, each with its cases the
    test map (``mapped``) lacks; a file whose cases it has them all for has no unit."""
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
        data_driven = sorted(
            f"{m.class_name}.{m.method}"
            for m in methods
            if not m.exact and not _mapped_method(m, known)
        )
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
