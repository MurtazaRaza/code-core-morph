"""What a port from C#'s units tell the agent (D-78, D-79): the façades and the C# that keeps
using them, the members and cases to map by ID, the program's export, the retirement."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.agent.context import build_context, csharp_port_note
from codecoremorph.core.models import PortUnit, PortUnits, WorkUnit
from codecoremorph.rules.loader import load_pack

PACK = Path(__file__).parents[3] / "src" / "codecoremorph" / "rules" / "packs" / "csharp_to_cpp"


def _type_unit() -> WorkUnit:
    return WorkUnit(
        id="U-0002",
        seq=2,
        rules=["port_csharp_type_to_cpp"],
        files=["src/Zoo/Animal.cs", "src/Zoo/Dog.cs", "src/Zoo/Zoo.csproj"],
        note="ports Zoo.Animal, Zoo.Dog (src/Zoo/Animal.cs, src/Zoo/Dog.cs) to C++",
        port=PortUnit(
            build_target="src/Zoo/Zoo.csproj",
            crate="zoo",
            module="animal",
            callers=["src/Zoo/Pen.cs", "tests/Zoo.Tests/AnimalTests.cs"],
            exports=["csharp:Zoo.Animal.Sound", "csharp:Zoo.Dog.#ctor(System.String)"],
            types=["csharp:Zoo.Animal", "csharp:Zoo.Dog"],
        ),
    )


def test_a_type_units_note_names_its_facades_its_callers_and_its_members() -> None:
    note = csharp_port_note(_type_unit(), PortUnits.CSHARP_TYPE, "cpp")
    assert "The façades: src/Zoo/Animal.cs, src/Zoo/Dog.cs." in note  # not the project file
    assert "don't change: src/Zoo/Pen.cs, tests/Zoo.Tests/AnimalTests.cs" in note
    assert "never edit it" in note
    assert "cpp/symbol-map.toml" in note
    assert "- csharp:Zoo.Animal.Sound\n- csharp:Zoo.Dog.#ctor(System.String)" in note


def test_the_target_names_the_maps_directory() -> None:
    """Phase 18 reuses the C# side: only the target's name and its maps' directory change."""
    note = csharp_port_note(_type_unit(), PortUnits.CSHARP_TYPE, "rust")
    assert "rust/symbol-map.toml" in note
    assert "cpp" not in note


def test_a_programs_note_names_its_export_and_its_members() -> None:
    unit = WorkUnit(
        id="U-0005",
        seq=5,
        rules=["port_csharp_program_to_cpp"],
        files=["src/Zoo.Cli/Program.cs", "src/Zoo.Cli/Report.cs", "src/Zoo.Cli/Zoo.Cli.csproj"],
        port=PortUnit(
            build_target="src/Zoo.Cli/Zoo.Cli.csproj",
            crate="zoo",
            module="zoo_cli",
            bin="Zoo.Cli",
            exports=["csharp:Zoo.Cli.Program.Main(System.String[])"],
        ),
    )
    note = csharp_port_note(unit, PortUnits.EXECUTABLE, "cpp")
    assert "calls `zoo_zoo_cli_main` with its arguments" in note
    assert "(of src/Zoo.Cli/Program.cs, src/Zoo.Cli/Report.cs)" in note
    assert "- csharp:Zoo.Cli.Program.Main(System.String[])" in note


def test_a_test_files_note_lists_its_cases_and_its_read_only_files() -> None:
    files = ["tests/Zoo.Tests/AnimalTests.cs", "tests/Zoo.Tests/FakeKeeper.cs"]
    unit = WorkUnit(
        id="U-0006",
        seq=6,
        rules=["port_csharp_tests_to_cpp"],
        files=files,
        reference_files=files,
        port=PortUnit(
            build_target="tests/Zoo.Tests/Zoo.Tests.csproj",
            crate="zoo",
            module="animal_tests",
            tests=[
                "dotnet:Zoo.Tests.AnimalTests#Sound",
                'dotnet:Zoo.Tests.AnimalTests#Legs(4,"x")',
            ],
        ),
    )
    note = csharp_port_note(unit, PortUnits.TESTS, "cpp")
    assert f"until the retirement: {', '.join(files)}." in note
    assert '`csharp = "dotnet:..."`, `cpp = ...`' in note
    assert "cpp/test-map.toml" in note
    assert (
        '- dotnet:Zoo.Tests.AnimalTests#Sound\n- dotnet:Zoo.Tests.AnimalTests#Legs(4,"x")' in note
    )


def test_the_retirement_with_and_without_the_kept_api() -> None:
    files = ["src/Zoo/Animal.cs", "src/Zoo/Zoo.csproj", "Zoo.sln"]
    retire = WorkUnit(id="U-0009", seq=9, rules=["retire_csharp_to_cpp"], files=files)
    note = csharp_port_note(retire, PortUnits.RETIREMENT, "cpp")
    assert "deleted 3 C# and MSBuild files" in note
    assert "the C++ builds and tests alone" in note
    assert "cpp/test-map.toml" in note
    kept = retire.model_copy(
        update={"files": ["src/Zoo.Cli/Program.cs"], "kept_api": ["src/Zoo/Zoo.csproj"]}
    )
    note = csharp_port_note(kept, PortUnits.RETIREMENT, "cpp")
    assert "src/Zoo/Zoo.csproj stays as the .NET library over the C++ (keep_source_api)" in note
    assert "both builds run" in note


def test_the_context_takes_the_target_from_the_rule(tmp_path: Path) -> None:
    for name in ("Animal.cs", "Dog.cs"):
        (tmp_path / "src" / "Zoo").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "Zoo" / name).write_text("namespace Zoo { }\n", encoding="utf-8")
    _, rules = load_pack(PACK)
    rule = next(r for r in rules if r.rule.id == "port_csharp_type_to_cpp")
    context = build_context(
        _type_unit(),
        [rule],
        worktree=tmp_path,
        index_path=None,
        language="csharp",
        source="csharp7.3",
        target="cpp17",
        codemods=None,
        remaining={},
        build=None,
        tools=[],
        token_budget=100_000,
    )
    message = context.first_message.text
    assert "ports Zoo.Animal, Zoo.Dog" in message
    assert "- csharp:Zoo.Animal.Sound" in message
    assert "cpp/symbol-map.toml" in message
    assert "rust/" not in message  # no module port's note (crates, Rust bridges)
