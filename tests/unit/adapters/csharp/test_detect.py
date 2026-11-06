"""What `codecoremorph init` reads from a C# project (FR-1.4)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.csharp.detect import detect

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "csharp"


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_a_net6_library_at_csharp_7_3() -> None:
    root = FIXTURES / "order-book"
    found = detect(root, _files(root))
    assert found is not None
    assert found.language == "csharp"
    assert found.build_system == "dotnet"
    assert found.name == "OrderBook"
    assert found.standard == "csharp7.3"
    assert not found.standard_guessed
    assert "net6.0" in found.standard_note
    assert "7.3" in found.standard_note
    assert found.target_standard == "csharp14"
    assert found.test_dirs == ["tests/OrderBook.Tests/**"]
    assert any("NUnit" in n for n in found.notes)


def test_a_multi_project_build_with_directory_build_props() -> None:
    root = FIXTURES / "shipping-rates"
    found = detect(root, _files(root))
    assert found is not None
    assert found.standard == "csharp7.3"  # Directory.Build.props sets it for every project
    assert found.name is None  # no solution and more than one project
    assert any("xUnit" in n for n in found.notes)


def test_no_csharp_no_detection(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert detect(tmp_path, ["main.py"]) is None


def test_projects_without_a_framework_are_a_guess(tmp_path: Path) -> None:
    (tmp_path / "A.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk" />', encoding="utf-8")
    (tmp_path / "A.cs").write_text("class A {}", encoding="utf-8")
    found = detect(tmp_path, ["A.cs", "A.csproj"])
    assert found is not None
    assert found.standard_guessed
    assert found.standard == "csharp7.3"
