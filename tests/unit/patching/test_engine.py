from __future__ import annotations

import codecs
import hashlib
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from codecoremorph.core.models import (
    CreateEdit,
    DeleteEdit,
    Edit,
    EditBatch,
    RejectionReason,
    ReplaceEdit,
)
from codecoremorph.patching import PatchEngine, PatchError
from codecoremorph.patching import engine as engine_module


def batch(*edits: dict[str, str]) -> EditBatch:
    return EditBatch.model_validate({"edits": list(edits), "summary": "test"})


def replace(path: str, search: str, new: str) -> dict[str, str]:
    return {"op": "replace", "path": path, "search": search, "replace": new}


def snapshot(root: Path) -> dict[str, str]:
    """Every file under root with its SHA-256; directories are listed too."""
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
        )
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "crlf.cpp").write_bytes(b"int a = 1;\r\nint b = 2;\r\n")
    (root / "src" / "lf.h").write_bytes(b"typedef int Index;\ntypedef long Size;\n")
    (root / "src" / "bom.h").write_bytes(codecs.BOM_UTF8 + b"// caf\xc3\xa9\nint x;\n")
    (root / "src" / "legacy.c").write_bytes(b"/* caf\xe9 */\r\nint y;\r\n")
    (root / "data.bin").write_bytes(b"\x00\x01\x02binary")
    (root / "third_party").mkdir()
    (root / "third_party" / "lib.h").write_bytes(b"int lib;\n")
    return root


def engine(root: Path) -> PatchEngine:
    return PatchEngine(root, exclude=["third_party/**"])


# --------------------------------------------------------------------------- preservation


def test_line_endings_bom_and_legacy_encodings_are_preserved(tree: Path) -> None:
    result = engine(tree).apply(
        batch(
            replace("src/crlf.cpp", "int b = 2;\n", "int b = 3;\nint c = 4;\n"),
            replace("src/lf.h", "typedef int Index;", "using Index = int;"),
            replace("src/bom.h", "int x;", "int x = 0;"),
            replace("src/legacy.c", "int y;", "int y = 0; /* naïve */"),
        )
    )
    assert result.applied, result.feedback()
    assert result.changed_files == ["src/bom.h", "src/crlf.cpp", "src/legacy.c", "src/lf.h"]
    assert (tree / "src/crlf.cpp").read_bytes() == b"int a = 1;\r\nint b = 3;\r\nint c = 4;\r\n"
    assert (tree / "src/lf.h").read_bytes() == b"using Index = int;\ntypedef long Size;\n"
    assert (tree / "src/bom.h").read_bytes() == codecs.BOM_UTF8 + b"// caf\xc3\xa9\nint x = 0;\n"
    assert (tree / "src/legacy.c").read_bytes() == b"/* caf\xe9 */\r\nint y = 0; /* na\xefve */\r\n"


def test_crlf_in_model_output_is_normalized(tree: Path) -> None:
    result = engine(tree).apply(batch(replace("src/lf.h", "Index;\r\ntypedef", "Idx;\r\ntypedef")))
    assert result.applied
    assert (tree / "src/lf.h").read_bytes() == b"typedef int Idx;\ntypedef long Size;\n"


def test_whitespace_tolerant_edits_are_reported(tree: Path) -> None:
    (tree / "src/indented.cpp").write_bytes(b"void f() {\n    call();\n}\n")
    result = engine(tree).apply(
        batch(
            replace("src/lf.h", "typedef long Size;", "using Size = long;"),
            replace("src/indented.cpp", "call();", "other();"),
            # tab-indented in the search, four spaces in the file: only matches loosely
            replace("src/indented.cpp", "\tother();\n}", "\tother();\n\tmore();\n}"),
        )
    )
    assert result.applied, result.feedback()
    assert result.whitespace_tolerant == [2]
    assert (tree / "src/indented.cpp").read_bytes() == b"void f() {\n    other();\n    more();\n}\n"


def test_unencodable_replacement_is_rejected(tree: Path) -> None:
    result = engine(tree).apply(batch(replace("src/legacy.c", "int y;", "int y; // ✓")))
    assert not result.applied
    assert result.rejections[0].reason is RejectionReason.ENCODING
    assert "cp1252" in result.rejections[0].message


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_file_mode_is_preserved(tree: Path) -> None:
    script = tree / "run.sh"
    script.write_bytes(b"#!/bin/sh\necho old\n")
    script.chmod(0o755)
    assert engine(tree).apply(batch(replace("run.sh", "old", "new"))).applied
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


# --------------------------------------------------------------------------- batch semantics


def test_edits_apply_in_order_within_a_file(tree: Path) -> None:
    result = engine(tree).apply(
        batch(
            {"op": "create", "path": "src/new/deep.h", "content": "int v = 1;\r\n"},
            replace("src/new/deep.h", "int v = 1;", "int v = 2;"),
            {"op": "delete", "path": "src/lf.h"},
            {"op": "create", "path": "src/lf.h", "content": "// recreated\n"},
        )
    )
    assert result.applied, result.feedback()
    assert (tree / "src/new/deep.h").read_bytes() == b"int v = 2;\n"  # new files use LF
    assert (tree / "src/lf.h").read_bytes() == b"// recreated\n"


def test_a_no_op_edit_changes_nothing(tree: Path) -> None:
    result = engine(tree).apply(batch(replace("src/lf.h", "Index", "Index")))
    assert result.applied
    assert result.changed_files == []


def test_all_or_nothing_and_every_rejection_is_reported(tree: Path) -> None:
    before = snapshot(tree)
    result = engine(tree).apply(
        batch(
            replace("src/lf.h", "typedef int Index;", "using Index = int;"),  # fine
            {"op": "create", "path": "src/brand_new.h", "content": "x"},  # fine
            replace("src/crlf.cpp", "int z", "int w"),  # not found
            replace("src/crlf.cpp", "int ", "long "),  # ambiguous (lines 1 and 2)
            {"op": "create", "path": "src/lf.h", "content": "x"},  # exists
            {"op": "delete", "path": "src/missing.h"},  # missing
            replace("src/missing.h", "a", "b"),  # missing
            replace("data.bin", "binary", "x"),  # binary
            {"op": "create", "path": "third_party/new.h", "content": "x"},  # excluded
        )
    )
    assert not result.applied
    assert [(r.edit_index, r.reason) for r in result.rejections] == [
        (2, RejectionReason.NOT_FOUND),
        (3, RejectionReason.AMBIGUOUS),
        (4, RejectionReason.FILE_EXISTS),
        (5, RejectionReason.FILE_MISSING),
        (6, RejectionReason.FILE_MISSING),
        (7, RejectionReason.BINARY_FILE),
        (8, RejectionReason.EXCLUDED),
    ]
    assert result.rejections[1].match_lines == [1, 2]
    assert "Edit 3 (replace src/crlf.cpp)" in result.feedback()
    assert snapshot(tree) == before


def test_binary_files_can_be_deleted(tree: Path) -> None:
    result = engine(tree).apply(batch({"op": "delete", "path": "data.bin"}))
    assert result.applied
    assert not (tree / "data.bin").exists()


def test_preview_writes_nothing(tree: Path) -> None:
    before = snapshot(tree)
    result, changes = engine(tree).preview(
        batch(
            replace("src/lf.h", "typedef int Index;", "using Index = int;"),
            {"op": "delete", "path": "data.bin"},
        )
    )
    assert result.applied
    assert changes == {
        "src/lf.h": b"using Index = int;\ntypedef long Size;\n",
        "data.bin": None,
    }
    assert snapshot(tree) == before


# --------------------------------------------------------------------------- path safety


def _unvalidated(edit: type[ReplaceEdit | CreateEdit | DeleteEdit], **fields: str) -> Edit:
    """Bypass model validation, to prove the engine checks paths on its own."""
    return edit.model_construct(None, **fields)


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("../escape.h", "normalized"),
        ("/etc/passwd", "relative"),
        (".git/config", ".git"),
        ("src/CON.h", "reserved device name"),
        ("src/nul.txt", "reserved device name"),
        ("src/trailing.", "dot or space"),
        ("src/a:stream", "isn't allowed"),
        ("src", "directory"),
    ],
)
def test_unsafe_paths_are_rejected(tree: Path, path: str, fragment: str) -> None:
    result = engine(tree).apply(
        [_unvalidated(CreateEdit, op="create", path=path, content="x")]
        if path != "src"
        else [_unvalidated(DeleteEdit, op="delete", path=path)]
    )
    assert not result.applied
    assert result.rejections[0].reason is RejectionReason.UNSAFE_PATH
    assert fragment in result.rejections[0].message


def _symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("creating symbolic links needs Developer Mode or admin rights on Windows")


def test_symlink_escaping_the_worktree_is_rejected(tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(tree / "src" / "linked", outside, directory=True)
    result = engine(tree).apply(
        batch({"op": "create", "path": "src/linked/evil.h", "content": "x"})
    )
    assert not result.applied
    assert "outside the worktree" in result.rejections[0].message
    assert not (outside / "evil.h").exists()


def test_symlinked_file_is_rejected(tree: Path) -> None:
    _symlink(tree / "src" / "alias.h", tree / "src" / "lf.h", directory=False)
    result = engine(tree).apply(batch(replace("src/alias.h", "Index", "Idx")))
    assert not result.applied
    assert "symbolic link" in result.rejections[0].message


def test_symlink_into_git_dir_is_rejected(tree: Path) -> None:
    (tree / ".git").mkdir()
    _symlink(tree / "gitlink", tree / ".git", directory=True)
    result = engine(tree).apply(batch({"op": "create", "path": "gitlink/hooks/x", "content": "x"}))
    assert not result.applied
    assert ".git" in result.rejections[0].message


# --------------------------------------------------------------------------- injected failures


Primitive = Callable[..., Any]


def _failing_after(real: Primitive, successes: int) -> Primitive:
    calls = {"n": 0}

    def wrapper(*args: Any) -> Any:
        calls["n"] += 1
        if calls["n"] > successes:
            raise OSError(28, "No space left on device (injected)")
        return real(*args)

    return wrapper


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    primitive=st.sampled_from(["_write_file", "_replace_file", "_delete_file"]),
    successes=st.integers(0, 3),
)
def test_injected_write_failures_leave_the_tree_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primitive: str, successes: int
) -> None:
    root = tmp_path / f"wt-{primitive}-{successes}"
    if root.exists():
        import shutil

        shutil.rmtree(root)
    (root / "src").mkdir(parents=True)
    for name in ("a.h", "b.h", "c.h"):
        (root / "src" / name).write_bytes(f"// {name}\nint v;\n".encode())
    before = snapshot(root)
    real = getattr(engine_module, primitive)
    with monkeypatch.context() as patch:
        patch.setattr(engine_module, primitive, _failing_after(real, successes))
        result = PatchEngine(root).apply(
            batch(
                replace("src/a.h", "int v;", "int v = 1;"),
                replace("src/b.h", "int v;", "int v = 2;"),
                {"op": "create", "path": "src/new/dir/d.h", "content": "int d;\n"},
                {"op": "delete", "path": "src/c.h"},
            )
        )
    total_calls = {"_write_file": 3, "_replace_file": 3, "_delete_file": 1}[primitive]
    if successes >= total_calls:
        assert result.applied
        return
    assert not result.applied
    assert result.rejections[0].reason is RejectionReason.WRITE_FAILED
    assert snapshot(root) == before  # no partial writes, temp files or new directories


def test_failed_restore_raises_patch_error(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "_delete_file", _failing_after(lambda p: None, 0))
    original_replace = Path.replace

    def broken_restore(self: Path, target: str | os.PathLike[str]) -> Path:
        if self.name.endswith(".ccm-restore.tmp"):
            raise OSError("restore failed (injected)")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", broken_restore)
    with pytest.raises(PatchError, match="could not be restored"):
        engine(tree).apply(
            batch(
                replace("src/lf.h", "typedef int Index;", "using Index = int;"),
                {"op": "delete", "path": "src/crlf.cpp"},
            )
        )


# --------------------------------------------------------------------------- properties

_chars = st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x00")
_lines = st.lists(
    st.tuples(st.text(_chars, max_size=12), st.sampled_from(["\n", "\r\n"])), max_size=8
)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    before=_lines,
    after=_lines,
    bom=st.booleans(),
    new_text=st.text(
        st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=20
    ),
)
def test_only_the_matched_region_changes(
    tmp_path: Path,
    before: list[tuple[str, str]],
    after: list[tuple[str, str]],
    bom: bool,
    new_text: str,
) -> None:
    marker = "@@CCM-TARGET@@"
    head = "".join(t + n for t, n in before)
    tail = "".join(t + n for t, n in after)
    prefix = codecs.BOM_UTF8 if bom else b""
    root = tmp_path / "prop"
    root.mkdir(exist_ok=True)
    target = root / "f.txt"
    target.write_bytes(prefix + (head + marker + tail).encode())

    result = PatchEngine(root).apply(batch(replace("f.txt", marker, new_text)))

    assert result.applied, result.feedback()
    data = target.read_bytes()
    assert data.startswith(prefix + head.encode())
    assert data.endswith(tail.encode())
    middle = data[len(prefix + head.encode()) : len(data) - len(tail.encode())]
    newline = (
        "\r\n"
        if tail.count("\r\n") + head.count("\r\n")
        > (head + tail).count("\n") - (head + tail).count("\r\n")
        else "\n"
    )
    assert middle == new_text.replace("\r\n", "\n").replace("\n", newline).encode()
