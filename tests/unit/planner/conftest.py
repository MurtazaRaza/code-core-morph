"""A synthetic project and symbol index for planner tests: files on disk plus index rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from codecoremorph.core.models import BuildTarget, TargetKind, TestEntry
from codecoremorph.store.index import (
    DependencyRow,
    FileRow,
    IndexStore,
    ReferenceRow,
    SymbolRow,
)

HEADERS = (".h", ".hpp")


@dataclass
class FakeProject:
    worktree: Path
    index_path: Path
    files: list[FileRow] = field(default_factory=list[FileRow])
    symbols: list[SymbolRow] = field(default_factory=list[SymbolRow])
    refs: list[ReferenceRow] = field(default_factory=list[ReferenceRow])
    deps: list[DependencyRow] = field(default_factory=list[DependencyRow])
    targets: list[BuildTarget] = field(default_factory=list[BuildTarget])
    tests: list[TestEntry] = field(default_factory=list[TestEntry])

    def file(
        self,
        path: str,
        text: str = "",
        *,
        role: str | None = None,
        language: str | None = None,
        c_compatible: bool = False,
        public_api: bool = False,
    ) -> FakeProject:
        host = self.worktree / path
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(text.encode("utf-8"))
        if role is None:
            if path.endswith("CMakeLists.txt"):
                role = "build"
            elif path.startswith("tests/"):
                role = "test"
            elif path.startswith("third_party/"):
                role = "third_party"
            else:
                role = "header" if path.endswith(HEADERS) else "source"
        if language is None:
            language = "cmake" if role == "build" else ("c" if path.endswith(".c") else "cpp")
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.files.append(
            (path, language, digest, role, int(c_compatible), int(public_api), "utf-8", "lf")
        )
        return self

    def symbol(
        self,
        symbol_id: str,
        file: str,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        kind: str = "function",
        definition: bool = True,
        boundary: str = "none",
        signature: str | None = None,
    ) -> FakeProject:
        name = symbol_id.rsplit("@", 1)[-1]
        self.symbols.append(
            (symbol_id, name, name, kind, file, start[0], start[1], end[0], end[1], signature,
             "none", int(definition), boundary)
        )  # fmt: skip
        return self

    def ref(self, from_file: str, to_symbol: str, line: int, column: int = 1) -> FakeProject:
        self.refs.append((from_file, None, to_symbol, "call", line, column))
        return self

    def dep(self, from_file: str, to_file: str) -> FakeProject:
        self.deps.append((from_file, to_file, "include"))
        return self

    def target(
        self,
        name: str,
        kind: str,
        files: list[str],
        dependencies: tuple[str, ...] = (),
        artifacts: tuple[str, ...] = (),
    ) -> FakeProject:
        self.targets.append(
            BuildTarget(
                name=name,
                kind=TargetKind(kind),
                files=files,
                dependencies=list(dependencies),
                artifacts=list(artifacts),
            )
        )
        return self

    def test(self, test_id: str, runs: str | None, file: str | None = None) -> FakeProject:
        self.tests.append(TestEntry(test_id=test_id, runs=runs, file=file))
        return self

    def build(self) -> Path:
        with IndexStore.open(self.index_path, reset=True) as store:
            store.write(
                files=self.files, symbols=self.symbols, references=self.refs, dependencies=self.deps
            )
            store.set_build_targets(self.targets)
            store.set_test_entries(self.tests)
        return self.index_path


@pytest.fixture
def fake_project(tmp_path: Path) -> FakeProject:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    return FakeProject(worktree=worktree, index_path=tmp_path / "cache" / "index.sqlite")
