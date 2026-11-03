"""clang-tidy's header filter, formatting the lines a codemod changed, and the standard the
build files set (D-42)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.adapter import (
    CppAdapter,
    _header_filter,  # pyright: ignore[reportPrivateUsage]
    _styled,  # pyright: ignore[reportPrivateUsage]
    changed_line_ranges,
)
from codecoremorph.core.models import LanguageConfig
from codecoremorph.sandbox.executor import Executor


@pytest.mark.parametrize(
    "name",
    [
        "/workspace/db/dbformat.h",
        "/workspace/./db/dbformat.h",  # leveldb: include_directories(".")
        "/workspace/db/./dbformat.h",
        "/workspace/src/../db/dbformat.h",
        r"E:\codecoremorph-home\worktrees\run\db\dbformat.h",
        "E:/codecoremorph-home/worktrees/run/./db/dbformat.h",
        "db/dbformat.h",
    ],
)
def test_the_header_filter_matches_every_spelling_of_a_header(name: str) -> None:
    """Regression: an anchored full-path filter missed `/workspace/./db/dbformat.h`, so
    clang-tidy never fixed leveldb's headers and the agent converted them by hand."""
    pattern = re.compile(_header_filter(["db/dbformat.h", "db/memtable.h"]))
    assert pattern.search(name)


@pytest.mark.parametrize(
    "name",
    [
        "/workspace/db/dbformat.hpp",
        "/workspace/xdb/dbformat.h",
        "/workspace/db/dbformat_h",
        "/workspace/db/other.h",
    ],
)
def test_the_header_filter_rejects_other_files(name: str) -> None:
    pattern = re.compile(_header_filter(["db/dbformat.h"]))
    assert not pattern.search(name)


def test_the_header_filter_has_no_python_only_syntax() -> None:
    """clang-tidy reads it with llvm::Regex (POSIX extended): no `(?:` groups."""
    assert "(?" not in _header_filter(["include/a-b/c+d.h"])
    assert _header_filter([]) == "^$"
    assert re.compile(_header_filter(["include/a-b/c+d.h"])).search("/w/include/a-b/c+d.h")


def test_changed_line_ranges() -> None:
    before = b"a\nb\nc\nd\ne\n"
    assert changed_line_ranges(before, before) == []
    assert changed_line_ranges(before, b"a\nB\nc\nd\nE\n") == [(2, 2), (5, 5)]
    assert changed_line_ranges(before, b"a\nB\nC\nd\ne\n") == [(2, 3)]
    assert changed_line_ranges(before, b"a\nb\nnew\nc\nd\ne\n") == [(3, 3)]
    assert changed_line_ranges(before, b"a\nc\nd\ne\n") == []  # a deletion leaves no line
    assert changed_line_ranges(b"a\r\nb\r\n", b"a\r\nB\r\n") == [(2, 2)]


def test_only_a_style_file_inside_the_worktree_counts(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "src" / "deep").mkdir(parents=True)
    (tmp_path / ".clang-format").write_text("BasedOnStyle: LLVM\n", encoding="utf-8")
    assert not _styled(worktree, "src/deep/a.cpp")  # the one above the worktree isn't ours
    (worktree / "src" / ".clang-format").write_text("BasedOnStyle: Google\n", encoding="utf-8")
    assert _styled(worktree, "src/deep/a.cpp")
    assert not _styled(worktree, "other/b.cpp")
    (worktree / "_clang-format").write_text("BasedOnStyle: Google\n", encoding="utf-8")
    assert _styled(worktree, "other/b.cpp")


class _NoExecutor:
    kind = "native"

    def to_sandbox_path(self, host_path: Path) -> str:
        return host_path.as_posix()


def _ctx(worktree: Path, *, exclude: list[str] | None = None) -> AdapterContext:
    executor: Executor = _NoExecutor()  # type: ignore[assignment]  # reads files only
    return AdapterContext(
        worktree=worktree,
        build_dir=worktree.parent / "build",
        cache_dir=worktree.parent / "cache",
        log_dir=worktree.parent / "logs",
        executor=executor,
        lang=LanguageConfig(
            language="cpp", standard="cpp11", target_standard="cpp20", exclude=exclude or []
        ),
    )


def test_the_project_standard_comes_from_its_own_build_files(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "third_party" / "gtest").mkdir(parents=True)
    (worktree / "CMakeLists.txt").write_text(
        "project(x)\n# was -std=c++98\nset(CMAKE_CXX_STANDARD 20)\n", encoding="utf-8"
    )
    (worktree / "third_party" / "gtest" / "CMakeLists.txt").write_text(
        "set(CMAKE_CXX_STANDARD 11)\n", encoding="utf-8"
    )
    adapter = CppAdapter()
    assert adapter.project_standard(_ctx(worktree, exclude=["third_party/**"])) == "cpp20"
    assert adapter.project_standard(_ctx(worktree)) == "cpp11"  # vendored code counts too
    (worktree / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    assert adapter.project_standard(_ctx(worktree, exclude=["third_party/**"])) is None
