"""dotnet's output as diagnostics (SPEC §11.3)."""

# ruff: noqa: E501 - real dotnet output and TRX lines, kept whole

from __future__ import annotations

from codecoremorph.adapters.csharp.diagnostics import PathMap, build_failure, parse_dotnet_output
from codecoremorph.core.models import DiagnosticOrigin, Severity

MIRROR = r"E:\cache\run\dotnet\project"

OUTPUT = r"""  Determining projects to restore...
E:\cache\run\dotnet\project\src\OrderBook\Book.cs(54,17): error SYSLIB0011: 'BinaryFormatter' is obsolete: 'BinaryFormatter serialization is obsolete.' (https://aka.ms/dotnet-warnings/SYSLIB0011) [E:\cache\run\dotnet\project\src\OrderBook\OrderBook.csproj]
e:\cache\run\dotnet\project\src\OrderBook\Order.cs(12,5): warning CS8618: Non-nullable field 'note' must contain a non-null value. [E:\cache\run\dotnet\project\src\OrderBook\OrderBook.csproj]
E:\cache\run\dotnet\project\src\OrderBook\Book.cs(54,17): error SYSLIB0011: 'BinaryFormatter' is obsolete: 'BinaryFormatter serialization is obsolete.' (https://aka.ms/dotnet-warnings/SYSLIB0011) [E:\cache\run\dotnet\project\src\OrderBook\OrderBook.csproj]
E:\cache\run\dotnet\project\tests\T\T.csproj : error NU1101: Unable to find package Nope. No packages exist with this id in source(s): nuget.org
CSC : error CS2001: Source file 'missing.cs' could not be found. [E:\cache\run\dotnet\project\src\A\A.csproj]
C:\sdk\Microsoft.Common.CurrentVersion.targets(5096,5): error MSB3030: Could not copy the file "x.dll" because it was not found. [E:\cache\run\dotnet\project\src\A\A.csproj]
E:\cache\run\dotnet\project\src\A\Style.cs(3,1): warning IDE0161: Convert to file-scoped namespace [E:\cache\run\dotnet\project\src\A\A.csproj]
Build FAILED.
"""


def test_canonical_lines_become_diagnostics_once() -> None:
    found = parse_dotnet_output(OUTPUT, PathMap([MIRROR], case_insensitive=True))
    assert [(d.file_path, d.line, d.column, d.code) for d in found] == [
        ("src/OrderBook/Book.cs", 54, 17, "SYSLIB0011"),
        ("src/OrderBook/Order.cs", 12, 5, "CS8618"),
        ("tests/T/T.csproj", None, None, "NU1101"),
        (None, None, None, "CS2001"),
        (r"C:\sdk\Microsoft.Common.CurrentVersion.targets", 5096, 5, "MSB3030"),
        ("src/A/Style.cs", 3, 1, "IDE0161"),
    ]
    by_code = {d.code: d for d in found}
    assert by_code["SYSLIB0011"].severity is Severity.ERROR
    assert by_code["SYSLIB0011"].origin is DiagnosticOrigin.COMPILER
    assert by_code["SYSLIB0011"].message.startswith("'BinaryFormatter' is obsolete")
    assert "[E:" not in by_code["SYSLIB0011"].message
    assert by_code["CS8618"].severity is Severity.WARNING
    assert by_code["NU1101"].origin is DiagnosticOrigin.BUILD_SYSTEM
    assert by_code["MSB3030"].origin is DiagnosticOrigin.BUILD_SYSTEM
    assert by_code["IDE0161"].origin is DiagnosticOrigin.ANALYZER


def test_paths_match_case_sensitively_off_windows() -> None:
    line = "/w/cache/project/src/A.cs(1,2): error CS1002: ; expected [/w/cache/project/A.csproj]"
    found = parse_dotnet_output(line, PathMap(["/w/cache/project"], case_insensitive=False))
    assert found[0].file_path == "src/A.cs"
    upper = parse_dotnet_output(
        line.replace("/w/", "/W/"), PathMap(["/w/cache/project"], case_insensitive=False)
    )
    assert upper[0].file_path == "/W/cache/project/src/A.cs"


def test_a_failure_without_error_lines() -> None:
    failure = build_failure("one\n\ntwo\n", "build", "build.log")
    assert failure.severity is Severity.ERROR
    assert failure.origin is DiagnosticOrigin.BUILD_SYSTEM
    assert failure.message == "dotnet's build failed:\none\ntwo"
    assert build_failure("", "restore", "r.log").message == "dotnet's restore failed; see r.log"
