"""The sandboxed index helper's pure parts: globs, command-line splitting, flag filtering and
the compiler's include-path query. Parsing with libclang is covered by the toolchain tests."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from codecoremorph.core.globs import PathMatcher

HELPER = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "codecoremorph"
    / "adapters"
    / "cpp"
    / "helpers"
    / "cpp_index_helper.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cpp_index_helper_under_test", HELPER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper: Any = _load()


def test_helper_imports_nothing_from_codecoremorph() -> None:
    assert "codecoremorph" not in HELPER.read_text(encoding="utf-8").replace(
        "CodeCoreMorph", ""
    ).replace("codecoremorph.core.globs", "")


@pytest.mark.parametrize(
    "pattern", ["third_party/**", "*.pb.cc", "**/generated/**", "src/[!a]*.h", "build", "a?c"]
)
@pytest.mark.parametrize(
    "path",
    ["third_party/x/y.h", "src/a.pb.cc", "x/generated/z.h", "src/b.h", "src/a.h", "build/x", "abc"],
)
def test_globs_match_core_semantics(pattern: str, path: str) -> None:
    regexes = [helper.glob_regex(pattern)]
    assert helper.glob_matches(regexes, path) == PathMatcher([pattern]).matches(path)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"clang++.exe -IE:\a\b -c x.cpp", ["clang++.exe", r"-IE:\a\b", "-c", "x.cpp"]),
        (r'cl "C:\Program Files\x" /c', ["cl", r"C:\Program Files\x", "/c"]),
        (r'a "b\"c" d\\e', ["a", 'b"c', r"d\\e"]),
        (r'a "x\\" y', ["a", "x\\", "y"]),
        ('a ""', ["a", ""]),
    ],
)
def test_split_windows(command: str, expected: list[str]) -> None:
    assert helper.split_windows(command) == expected


def test_parse_flags_keeps_only_what_changes_parsing(tmp_path: Path) -> None:
    directory = str(tmp_path)
    argv = [
        "clang++",
        "-Iinclude",
        "-isystem",
        "third",
        "-DA=1",
        "-D",
        "B",
        "-std=c++11",
        "-Wall",
        "-O2",
        "-MD",
        "-MF",
        "x.d",
        "-o",
        "x.o",
        "-c",
        "src/x.cpp",
        "-fno-rtti",
        "--target=x86_64-linux-gnu",
        "-x",
        "c++",
    ]
    args, query, language = helper.parse_flags(argv, directory)
    assert args == [
        "-I" + os.path.normpath(Path(directory) / "include"),
        "-isystem",
        os.path.normpath(Path(directory) / "third"),
        "-DA=1",
        "-D",
        "B",
        "-std=c++11",
        "-fno-rtti",
        "--target=x86_64-linux-gnu",
        "-x",
        "c++",
    ]
    assert query == ["-std=c++11", "--target=x86_64-linux-gnu"]
    assert language == "c++"


def test_system_includes_come_from_the_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    output = (
        "clang version 18.1.8\n"
        "Target: x86_64-w64-windows-gnu\n"
        '#include "..." search starts here:\n'
        "#include <...> search starts here:\n"
        " /opt/llvm/include/c++/v1\n"
        " /opt/llvm/lib/clang/18/include\n"
        " /Library/Frameworks (framework directory)\n"
        "End of search list.\n"
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", output.encode())

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    helper._SYSTEM_INCLUDES.clear()
    args = helper.system_include_args("clang++", "c++", ["-std=c++11"])
    assert args == [
        "-nostdinc",
        "-nostdinc++",
        "--target=x86_64-w64-windows-gnu",
        "-isystem",
        os.path.normpath("/opt/llvm/include/c++/v1"),
        "-isystem",
        os.path.normpath("/opt/llvm/lib/clang/18/include"),
        "-iframework",
        os.path.normpath("/Library/Frameworks"),
    ]
    assert calls == [["clang++", "-x", "c++", "-std=c++11", "-E", "-v", "-"]]
    helper.system_include_args("clang++", "c++", ["-std=c++11"])
    assert len(calls) == 1  # cached per compiler and flags


def test_missing_compiler_falls_back_to_libclang_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> None:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    helper._SYSTEM_INCLUDES.clear()
    assert helper.system_include_args("gone", "c", []) == []


def test_make_jobs_filters_and_classifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "system_include_args", lambda *a: ["-nostdinc"])
    ws = tmp_path / "ws"
    entries = [
        {"directory": str(tmp_path / "b"), "file": str(ws / "src" / "a.cpp"),
         "arguments": ["clang++", "-c", str(ws / "src" / "a.cpp")]},
        {"directory": str(tmp_path / "b"), "file": str(ws / "src" / "c.c"),
         "command": "clang -c c.c"},
        {"directory": str(tmp_path / "b"), "file": str(ws / "third_party" / "z.cpp"),
         "arguments": ["clang++", "z.cpp"]},
        {"directory": str(tmp_path / "b"), "file": str(tmp_path / "elsewhere.cpp"),
         "arguments": ["clang++", "e.cpp"]},
        {"directory": str(tmp_path / "b"), "file": str(ws / "src" / "m.cpp"),
         "arguments": ["clang-cl", "/c", "m.cpp"]},
    ]  # fmt: skip
    compdb = tmp_path / "compile_commands.json"
    import json

    compdb.write_text(json.dumps(entries), encoding="utf-8")
    workspace = helper.Workspace(str(ws), ["third_party/**"])
    jobs, problems = helper.make_jobs({"compile_commands": str(compdb)}, workspace)
    assert [(j["rel"], j["lang"]) for j in jobs] == [("src/a.cpp", "cpp"), ("src/c.c", "c")]
    assert jobs[1]["args"][:2] == ["-x", "c"]
    assert problems == ["src/m.cpp: MSVC-style compile commands are not indexed"]
