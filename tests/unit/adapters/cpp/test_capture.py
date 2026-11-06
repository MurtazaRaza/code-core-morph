"""The build model of a Make build, from its compiler and archiver calls (D-68).

The calls in tests/data/c/capture were captured with helpers/capture.sh in the
codecoremorph-rust image: bzip2 1.0.8 (a plain Makefile), zlib 1.2.11 (`./configure`) and
libyaml 0.2.5 (autotools and libtool), each built and tested in /tmp/mirror/<name>.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codecoremorph.adapters.cpp.capture import (
    Call,
    build_model,
    for_worktree,
    library_name,
    read_calls,
)
from codecoremorph.core.models import TargetKind

DATA = Path(__file__).resolve().parents[3] / "data" / "c" / "capture"
COMPILERS = {"cc": "clang", "cxx": "clang++"}


def _recorded(name: str) -> list[Call]:
    raw = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return [Call(tool=c["tool"], cwd=c["cwd"], args=tuple(c["args"])) for c in raw]


def _model(name: str):
    return build_model(_recorded(name), root=f"/tmp/mirror/{name}", compilers=COMPILERS)


def test_a_plain_makefile_gives_its_library_and_programs() -> None:
    model = _model("bzip2")
    targets = {t.name: t for t in model.targets}
    assert sorted(targets) == ["bz2", "bzip2", "bzip2recover"]
    library = targets["bz2"]
    assert library.kind is TargetKind.LIBRARY
    assert library.files == [
        "blocksort.c", "bzlib.c", "compress.c", "crctable.c", "decompress.c", "huffman.c",
        "randtable.c",
    ]  # fmt: skip
    assert library.artifacts == ["libbz2.a"]
    assert targets["bzip2"].kind is TargetKind.EXECUTABLE
    assert (targets["bzip2"].files, targets["bzip2"].dependencies) == (["bzip2.c"], ["bz2"])
    assert targets["bzip2recover"].dependencies == []  # it links no project library
    assert len(model.commands) == 9
    assert {c["arguments"][0] for c in model.commands} == {"clang"}


def test_a_static_and_a_shared_library_of_the_same_sources_are_one_target() -> None:
    targets = {t.name: t for t in _model("zlib").targets}
    assert targets["z"].artifacts == ["libz.a", "libz.so.1.2.11"]
    assert len(targets["z"].files) == 15
    # test/example.c is built three ways (static, shared, 64-bit offsets): three programs.
    examples = [t for t in targets.values() if t.files == ["test/example.c"]]
    assert sorted(t.name for t in examples) == ["example", "example64", "examplesh"]
    assert all(t.dependencies == ["z"] for t in examples)


def test_libtool_builds_resolve_to_their_library() -> None:
    model = _model("libyaml")
    targets = {t.name: t for t in model.targets}
    yaml = targets["yaml"]
    assert yaml.artifacts == ["src/.libs/libyaml-0.so.2.0.9", "src/.libs/libyaml.a"]
    assert yaml.files == [f"src/{m}.c" for m in (
        "api", "dumper", "emitter", "loader", "parser", "reader", "scanner", "writer",
    )]  # fmt: skip
    # The programs link src/.libs/libyaml.so, libtool's link to libyaml-0.so.2.0.9.
    assert targets["test-reader"].dependencies == ["yaml"]
    # libtool links it as tests/.libs/test-reader; make makes, and recipes run, its wrapper.
    assert targets["test-reader"].artifacts == ["tests/test-reader"]
    # libtool compiles each source twice (PIC and not); the index needs one command each.
    files = [c["file"] for c in model.commands]
    assert len(files) == len(set(files)) == 21


def test_a_later_build_updates_the_model() -> None:
    """An incremental build adds calls: the newest archive of an output wins, so a source
    taken out of the library (ported) leaves it."""
    calls = _recorded("bzip2")
    root = "/tmp/mirror/bzip2"
    members = [f"{m}.o" for m in ("blocksort", "crctable", "randtable", "compress",
                                  "decompress", "bzlib")]  # fmt: skip
    rearchived = [*calls, Call(tool="ar", cwd=root, args=("cq", "libbz2.a", *members))]
    library = next(
        t
        for t in build_model(rearchived, root=root, compilers=COMPILERS).targets
        if t.name == "bz2"
    )
    assert "huffman.c" not in library.files
    assert len(library.files) == 6


def test_preprocessing_and_dependency_listing_are_not_compiles() -> None:
    calls = [
        Call(tool="cc", cwd="/m", args=("-E", "a.c")),
        Call(tool="cc", cwd="/m", args=("-MM", "a.c")),
        Call(tool="cc", cwd="/m", args=("-c", "b.c", "-o", "b.o")),
        Call(tool="ar", cwd="/m", args=("t", "libx.a")),  # a listing makes no archive
    ]
    model = build_model(calls, root="/m", compilers=COMPILERS)
    assert [c["file"] for c in model.commands] == ["/m/b.c"]
    assert model.targets == []


def test_compiling_and_linking_in_one_call() -> None:
    calls = [Call(tool="cc", cwd="/m", args=("-O2", "-o", "tool", "tool.c", "util.c", "-lm"))]
    model = build_model(calls, root="/m", compilers=COMPILERS)
    (program,) = model.targets
    assert (program.name, program.kind, program.files) == (
        "tool",
        TargetKind.EXECUTABLE,
        ["tool.c", "util.c"],
    )
    assert sorted(c["file"] for c in model.commands) == ["/m/tool.c", "/m/util.c"]


def test_a_partial_link_makes_no_program() -> None:
    """A C port's crate becomes one object (``cc -r``) before libtool archives it (D-70)."""
    calls = [
        Call(tool="cc", cwd="/m/src", args=("-c", "a.c", "-o", "a.o")),
        Call(
            tool="cc",
            cwd="/m/src",
            args=("-r", "-nostdlib", "-o", "crate.o", "-Wl,--whole-archive", "/m/libx_rs.a"),
        ),
        Call(tool="ar", cwd="/m/src", args=("cr", "libx.a", "a.o", "crate.o")),
    ]
    model = build_model(calls, root="/m", compilers=COMPILERS)
    assert [(t.name, t.kind) for t in model.targets] == [("x", TargetKind.LIBRARY)]
    assert model.targets[0].files == ["src/a.c"]


def test_a_relinked_libtool_program_is_its_wrapper() -> None:
    calls = [
        Call(tool="cc", cwd="/m/t", args=("-c", "t.c", "-o", "t.o")),
        Call(tool="cc", cwd="/m/t", args=("-o", ".libs/lt-t", "t.o")),
    ]
    (program,) = build_model(calls, root="/m", compilers=COMPILERS).targets
    assert (program.name, program.artifacts) == ("t", ["t/t"])


@pytest.mark.parametrize(
    ("path", "name"),
    [("libbz2.a", "bz2"), ("/x/libz.so.1.2.11", "z"), (".libs/libyaml-0.so.2", "yaml-0"),
     ("libfoo.dylib", "foo"), ("foo.lib", "foo"), ("lib.a", "lib.a")],
)  # fmt: skip
def test_library_names(path: str, name: str) -> None:
    assert library_name(path) == name


def test_the_worktree_commands_compile_the_worktree_and_find_generated_headers() -> None:
    model = _model("libyaml")
    commands = for_worktree(
        model.commands,
        mirror="/tmp/mirror/libyaml",
        worktree="/workspace",
        exists=lambda rel: rel != "src/scanner.c",  # say the port took this one out
    )
    files = [c["file"] for c in commands]
    assert "/workspace/src/api.c" in files
    assert "/workspace/src/scanner.c" not in files
    api = next(c for c in commands if c["file"] == "/workspace/src/api.c")
    assert api["directory"] == "/workspace/src"
    args = api["arguments"]
    assert "/workspace/src/api.c" in args
    assert not any(a.startswith("/tmp/mirror") for a in args[: args.index("-iquote")])
    # configure's config.h is only in the mirror: searched after the worktree's own headers.
    tail = args[args.index("-iquote") :]
    assert tail[:2] == ["-iquote", "/tmp/mirror/libyaml/src"]
    assert "/tmp/mirror/libyaml/include" in tail
    assert all(flag in ("-iquote", "-idirafter") for flag in tail[::2])


def test_calls_are_read_in_the_order_they_ran(tmp_path: Path) -> None:
    for name, cwd, args, when in [
        ("ar.zzzz", "/m", ["cq", "libx.a", "a.o"], 3_000),
        ("cc.aaaa", "/m", ["-c", "a.c"], 1_000),
        ("cc.mmmm", "/m/sub", ["-c", "b.c"], 2_000),
        ("note.txt", "/m", ["x"], 500),  # not a call
    ]:
        path = tmp_path / name
        path.write_bytes(b"".join(f"{part}\0".encode() for part in [cwd, *args]))
        os.utime(path, ns=(when * 1_000_000, when * 1_000_000))
    calls = read_calls(tmp_path)
    assert [(c.tool, c.cwd, c.args) for c in calls] == [
        ("cc", "/m", ("-c", "a.c")),
        ("cc", "/m/sub", ("-c", "b.c")),
        ("ar", "/m", ("cq", "libx.a", "a.o")),
    ]
    assert read_calls(tmp_path / "missing") == []
