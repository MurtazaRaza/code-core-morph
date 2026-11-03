"""Loading the helper's JSON lines into the SQLite index (roles, C compatibility, public API)."""

from __future__ import annotations

import json
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.indexing import ingest, line_ending
from codecoremorph.core.models import Boundary, FileRole, LanguageConfig, LineEnding
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.store.index import IndexStore


def _ctx(tmp_path: Path) -> AdapterContext:
    work = tmp_path / "work"
    files = {
        "CMakeLists.txt": b"project(x)\n",
        "include/api.h": b"#pragma once\r\nint api();\r\n",
        "src/a.cpp": b'#include "api.h"\nint api() { return 1; }\n',
        "src/c.c": b'#include "shared.h"\n',
        "src/shared.h": b"typedef int t;\n",
        "tests/a_test.cpp": b"int t;\n",
        "third_party/lib.h": b"int lib;\n",
        "third_party/unused.cc": b"int unused;\n",
        "src/env_windows.cc": b"int windows_only;\n",
        "src/windows_logger.h": b"int logger;\n",
        "src/legacy.c": b"int c_only;\n",
        "build/CMakeLists.txt": b"# a stale in-tree build directory is not a build file\n",
    }
    for rel, data in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(
            language="cpp",
            standard="cpp11",
            test_dirs=["tests"],
            exclude=["third_party/**"],
            public_api=["include/**"],
        ),
    )


def test_ingest(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.cache_dir.mkdir()
    records = [
        ["tu", "src/a.cpp", False, 0.1],
        ["file", "src/a.cpp", False],
        ["file", "include/api.h", False],
        ["file", "src/c.c", True],
        ["file", "src/shared.h", True],
        ["file", "tests/a_test.cpp", False],
        ["file", "third_party/lib.h", False],
        ["dep", "src/a.cpp", "include/api.h"],
        ["sym", "c:@F@api#", "api", "api", "function", "include/api.h", 2, 1, 2, 10,
         "int api()", "none", False, "none"],
        ["sym", "c:@F@api#", "api", "api", "function", "src/a.cpp", 2, 1, 2, 23,
         "int api()", "none", True, "none"],
        ["sym", "c:@F@c_fn", "c_fn", "c_fn", "function", "include/api.h", 3, 1, 3, 9,
         None, "none", False, "c_abi"],
        ["ref", "src/a.cpp", None, "c:@F@api#", "call", 2, 5],
        ["problem", "src/x.cpp: could not parse"],
        ["meta", {"libclang": "clang version 18.1.1", "translation_units": 1, "parsed": 1,
                  "cached": 0, "failed": 0, "seconds": 0.5}],
    ]  # fmt: skip
    output = tmp_path / "out.jsonl"
    output.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    index = ingest(ctx, output, adapter_version="1", helper_s=0.7)

    assert index.problems == ["src/x.cpp: could not parse"]
    assert index.meta.tool_versions == {"libclang": "clang version 18.1.1"}
    assert index.meta.stats["symbols"] == 3
    assert index.meta.timings["helper_s"] == 0.7
    # Code no compile command reaches: another platform's sources (D-42). Excluded paths and
    # C sources don't count; the in-tree build directory is never walked.
    assert index.meta.uncompiled == ["src/env_windows.cc", "src/windows_logger.h"]
    with IndexStore.open(index.path) as store:
        files = {f.path: f for f in store.files()}
        assert sorted(files) == [
            "CMakeLists.txt",
            "include/api.h",
            "src/a.cpp",
            "src/c.c",
            "src/shared.h",
            "tests/a_test.cpp",
            "third_party/lib.h",
        ]
        assert files["include/api.h"].role is FileRole.HEADER
        assert files["include/api.h"].public_api
        assert files["include/api.h"].line_ending is LineEnding.CRLF
        assert files["src/shared.h"].c_compatible
        assert not files["src/c.c"].c_compatible  # a C source is C, not "shared with C"
        assert files["src/c.c"].language == "c"
        assert files["tests/a_test.cpp"].role is FileRole.TEST
        assert files["third_party/lib.h"].role is FileRole.THIRD_PARTY
        assert files["CMakeLists.txt"].role is FileRole.BUILD
        boundaries = {(s.file, s.name): s.boundary for s in store.symbols()}
        assert boundaries[("include/api.h", "api")] is Boundary.PUBLIC_API
        assert boundaries[("src/a.cpp", "api")] is Boundary.NONE
        assert boundaries[("include/api.h", "c_fn")] is Boundary.C_ABI  # C ABI wins
        assert [r.from_file for r in store.references_to("c:@F@api#")] == ["src/a.cpp"]
        assert store.dependents_of("include/api.h") == ["src/a.cpp"]


def test_line_endings() -> None:
    assert line_ending(b"a\nb\n") is LineEnding.LF
    assert line_ending(b"a\r\nb\r\n") is LineEnding.CRLF
    assert line_ending(b"a\r\nb\n") is LineEnding.MIXED
    assert line_ending(b"abc") is LineEnding.NONE
