"""What the C# index records for a port's source (D-78): the Roslyn helper's rows, as it wrote
them for the port index's test data (``tests/integration/adapters/data/csharp_port_index``),
turned into the index's models and kept in the store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codecoremorph.adapters.csharp.indexing import MAX_USES, port_records, project_targets
from codecoremorph.adapters.csharp.msbuild import Layout
from codecoremorph.core.models import PortRefusal, SymbolKind, TargetKind
from codecoremorph.store.index import IndexStore

ROWS = Path(__file__).with_name("data") / "port_rows.jsonl"
KNOWN = {
    "src/Zoo/Animal.cs",
    "src/Zoo/Keeper.cs",
    "src/Zoo/Pen.cs",
    "src/Zoo/Refused.cs",
    "src/Zoo/Ticket.cs",
    "src/Zoo/Ticket.Price.cs",
    "tests/Zoo.NUnit/AnimalTests.cs",
    "tests/Zoo.Xunit/AnimalFacts.cs",
}


def _rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in ROWS.read_text(encoding="utf-8").splitlines() if line]


def test_types_with_their_hierarchy_members_and_state() -> None:
    records = port_records(_rows(), KNOWN)
    types = {t.id: t for t in records.types}
    assert sorted(types) == [
        "csharp:Zoo.Animal",
        "csharp:Zoo.Keeper",
        "csharp:Zoo.Mirror",
        "csharp:Zoo.Pen`1",
        "csharp:Zoo.Ticket",
        "csharp:Zoo.Ticket.Stub",
    ]
    animal = types["csharp:Zoo.Animal"]
    assert animal.abstract
    assert animal.virtual_members == [
        "csharp:Zoo.Animal.Sound",
        "csharp:Zoo.Animal.Legs",
        "csharp:Zoo.Animal.ToString",
    ]
    keeper = types["csharp:Zoo.Keeper"]
    assert keeper.instance_fields == ["Rations"]
    # A readonly list is state; a readonly string and a constant aren't.
    assert keeper.static_state == [
        "csharp:Zoo.Keeper.Log",
        "csharp:Zoo.Keeper.hired",
        "csharp:Zoo.Keeper.Current",
    ]
    assert [(t.exception, t.member) for t in keeper.throws] == [
        ("System.ArgumentNullException", "csharp:Zoo.Keeper.#ctor(Zoo.IFeeder)"),
        ("Zoo.ZooException", "csharp:Zoo.Keeper.Round(Zoo.Pen{Zoo.Dog})"),
    ]
    assert types["csharp:Zoo.Ticket"].files == ["src/Zoo/Ticket.Price.cs", "src/Zoo/Ticket.cs"]
    stub = types["csharp:Zoo.Ticket.Stub"]
    assert (stub.kind, stub.outer) == (SymbolKind.STRUCT, "csharp:Zoo.Ticket")
    assert types["csharp:Zoo.Pen`1"].type_parameters == ["T"]
    mirror = types["csharp:Zoo.Mirror"]
    assert [(r.what, r.detail) for r in mirror.refusals] == [
        (PortRefusal.REFLECTION, "System.Activator.CreateInstance"),
        (PortRefusal.REFLECTION, "System.Type.GetProperties"),
    ]


def test_generic_uses_keep_their_type_arguments() -> None:
    (fold,) = port_records(_rows(), KNOWN).uses
    assert fold.symbol == "csharp:Zoo.Pen`1.Fold``1(``0,System.Func{``0,`0,``0})"
    assert (fold.arguments, fold.open, fold.line) == (["int"], False, 51)


def test_a_generic_used_all_over_keeps_its_first_uses() -> None:
    use = {"t": "inst", "of": "csharp:Zoo.Pen`1", "args": ["Zoo.Dog"], "file": "src/Zoo/Pen.cs"}
    rows = [{**use, "line": n} for n in range(1, 30)] + [{**use, "args": ["Zoo.Bird"], "line": 3}]
    uses = port_records(rows, KNOWN).uses
    assert len([u for u in uses if u.arguments == ["Zoo.Dog"]]) == MAX_USES
    assert [u.line for u in uses if u.arguments == ["Zoo.Bird"]] == [3]


def test_test_methods_and_the_entries_of_exact_cases() -> None:
    records = port_records(_rows(), KNOWN)
    methods = {(m.class_name, m.method): m for m in records.tests}
    ints = methods["Zoo.NUnit.AnimalTests", "Ints"]
    assert ints.framework == "nunit"
    assert ints.cases == [
        'dotnet:Zoo.NUnit.AnimalTests#Ints(1,"a")',
        'dotnet:Zoo.NUnit.AnimalTests#Ints(-2,"with \\"quotes\\" and \'apostrophes\'")',
        "dotnet:Zoo.NUnit.AnimalTests#Ints(0,null)",
    ]
    source = methods["Zoo.NUnit.AnimalTests", "FromASource"]
    assert (source.exact, source.cases) == (False, [])
    doubles = methods["Zoo.Xunit.AnimalFacts", "Doubles"]
    assert doubles.cases[1] == "dotnet:Zoo.Xunit.AnimalFacts#Doubles(value: 7.9000000000000004)"
    entries = records.entries()
    assert len(entries) == 6
    assert {e.runs for e in entries} == {
        "tests/Zoo.NUnit/Zoo.NUnit.csproj",
        "tests/Zoo.Xunit/Zoo.Xunit.csproj",
    }


def test_rows_of_files_the_index_lacks_are_left_out() -> None:
    records = port_records(_rows(), KNOWN - {"src/Zoo/Refused.cs", "src/Zoo/Ticket.Price.cs"})
    types = {t.id: t for t in records.types}
    assert "csharp:Zoo.Mirror" not in types
    assert types["csharp:Zoo.Ticket"].files == ["src/Zoo/Ticket.cs"]


def test_the_store_keeps_them(tmp_path: Path) -> None:
    records = port_records(_rows(), KNOWN)
    with IndexStore.open(tmp_path / "index.sqlite") as store:
        assert (store.port_types(), store.generic_uses(), store.test_methods()) == ([], [], [])
        store.set_port_source(
            types=records.types, generic_uses=records.uses, test_methods=records.tests
        )
    with IndexStore.open(tmp_path / "index.sqlite") as store:
        assert store.port_types() == records.types
        assert store.generic_uses() == records.uses
        assert store.test_methods() == records.tests


def test_each_project_is_a_build_target(tmp_path: Path) -> None:
    """A port cuts types from the libraries and leaves programs and tests to their own units
    (D-78): each project is a target, with what it compiles and the projects it references."""
    sdk = (
        '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n{}  </PropertyGroup>\n{}</Project>\n'
    )
    files = {
        "src/Zoo/Zoo.csproj": sdk.format("", ""),
        "src/App/App.csproj": sdk.format(
            "    <OutputType>Exe</OutputType>\n",
            '  <ItemGroup><ProjectReference Include="../Zoo/Zoo.csproj" /></ItemGroup>\n',
        ),
        "tests/Zoo.Tests/Zoo.Tests.csproj": sdk.format(
            "",
            '  <ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />'
            "</ItemGroup>\n",
        ),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    lay = Layout(
        projects=sorted(files),
        sources={
            "src/Zoo/Zoo.csproj": ["src/Zoo/Animal.cs"],
            "src/App/App.csproj": ["src/App/Program.cs"],
            "tests/Zoo.Tests/Zoo.Tests.csproj": ["tests/Zoo.Tests/AnimalTests.cs"],
        },
        references={"src/App/App.csproj": ["src/Zoo/Zoo.csproj"]},
    )
    targets = {t.name: t for t in project_targets(tmp_path, lay)}
    assert {name: t.kind for name, t in targets.items()} == {
        "src/App/App.csproj": TargetKind.EXECUTABLE,
        "src/Zoo/Zoo.csproj": TargetKind.LIBRARY,
        "tests/Zoo.Tests/Zoo.Tests.csproj": TargetKind.TEST,
    }
    assert targets["src/App/App.csproj"].dependencies == ["src/Zoo/Zoo.csproj"]
    assert targets["src/Zoo/Zoo.csproj"].files == ["src/Zoo/Animal.cs"]
