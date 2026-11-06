"""The dotnet-format codemod's pieces (SPEC §11.3, D-65)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.csharp import formatting


def test_the_editorconfig_appends_the_rule_s_options_after_the_project_s() -> None:
    project = "root = true\r\n\r\n[*.cs]\r\nindent_size = 4\r\n"
    text = formatting.editorconfig(
        project, {"csharp_style_namespace_declarations": "file_scoped"}, ["IDE0161"]
    )
    assert text.startswith(project)
    assert text.endswith(
        "[*.cs]\r\ncsharp_style_namespace_declarations = file_scoped\r\n"
        "dotnet_diagnostic.IDE0161.severity = warning\r\n"
    )
    assert text.count("root = true") == 1


def test_a_project_without_an_editorconfig_gets_a_root_one(tmp_path: Path) -> None:
    formatting.write_editorconfig(tmp_path, {"a": "b"}, ["IDE0090"])
    text = (tmp_path / ".editorconfig").read_text(encoding="utf-8")
    assert text.startswith("root = true\n")
    assert "a = b\n" in text
    assert "dotnet_diagnostic.IDE0090.severity = warning\n" in text


def test_checks_must_be_diagnostic_ids() -> None:
    assert formatting.check_ids(["IDE0161", "CA1825"]) == ["IDE0161", "CA1825"]
    with pytest.raises(ValueError, match="rm -rf"):
        formatting.check_ids(["rm -rf"])


def test_format_arguments_name_the_unit_s_files() -> None:
    args = formatting.format_arguments("App.sln", ["src/A.cs", "src/B.cs"], ["IDE0161"])
    assert args[:4] == ["format", "style", "App.sln", "--no-restore"]
    assert args[args.index("--diagnostics") + 1] == "IDE0161"
    assert args[-3:] == ["--include", "src/A.cs", "src/B.cs"]


def test_keep_style_keeps_line_endings_and_a_bom() -> None:
    old = "﻿namespace A\r\n{\r\n}\r\n".encode()
    new = b"namespace A;\n"
    assert formatting.keep_style(old, new) == "﻿namespace A;\r\n".encode()
    assert formatting.keep_style(b"a\nb\n", b"a\r\nc\r\n") == b"a\nc\n"
    assert formatting.changed_ranges(b"a\nb\nc\n", b"a\nx\nc\n") == [(2, 2)]
