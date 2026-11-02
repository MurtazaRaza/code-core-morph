"""Safety test (NFR-2.1, I-11): a run never modifies the user's working tree, index or branches.

The fingerprint covers every file in the user's checkout (tracked, modified, staged,
untracked and ignored), the index, HEAD, the repository config and every ref except
CodeCoreMorph's own ``codecoremorph/*`` branches. The only allowed change is the
``/.codecoremorph/`` line in ``.git/info/exclude`` (and ``.codecoremorph/`` itself).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codecoremorph.core.models import EditBatch
from codecoremorph.patching import PatchEngine
from codecoremorph.vcs import RunWorkspace

RUN = "20261102-141500-a3f9"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(repo: Path, git) -> dict[str, object]:
    files: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo).as_posix()
        if rel.split("/")[0] in (".git", ".codecoremorph"):
            continue
        files[rel] = _sha(path) if path.is_file() else "<dir>"
    refs = [
        line
        for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        if not line.startswith("refs/heads/codecoremorph/")
    ]
    git_dir = repo / ".git"
    return {
        "files": files,
        "index": _sha(git_dir / "index"),
        "HEAD": (git_dir / "HEAD").read_bytes(),
        "config": _sha(git_dir / "config"),
        "refs": refs,
    }


@pytest.fixture
def busy_repo(repo: Path, git, tmp_path: Path) -> Path:
    """The user is mid-work: modified, staged, untracked and ignored files, a stash, hooks."""
    (repo / "src" / "vector.h").write_bytes(b"#pragma once\n// work in progress\n")  # modified
    (repo / "src" / "util.h").write_bytes(b"// staged change\n")
    git(repo, "add", "src/util.h")
    (repo / "scratch.txt").write_bytes(b"untracked notes\r\n")
    (repo / "build").mkdir()
    (repo / "build" / "main.o").write_bytes(b"\x7fELF ignored")
    git(repo, "stash", "push", "--quiet", "--include-untracked", "--", "scratch.txt")
    (repo / "scratch.txt").write_bytes(b"untracked notes\r\n")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    marker = tmp_path / "hook-ran"
    for name in ("post-checkout", "pre-commit", "post-commit"):
        hook = repo / ".git" / "hooks" / name
        hook.write_bytes(f"#!/bin/sh\necho {name} >> '{marker.as_posix()}'\n".encode())
        hook.chmod(0o755)
    return repo


def _edits(*edits: dict[str, str]) -> EditBatch:
    return EditBatch.model_validate({"edits": list(edits), "summary": "safety test"})


def test_full_lifecycle_leaves_the_users_checkout_byte_identical(
    busy_repo: Path, git, tmp_path: Path
) -> None:
    before = fingerprint(busy_repo, git)
    exclude_before = (busy_repo / ".git" / "info" / "exclude").read_bytes()

    workspace = RunWorkspace.create(busy_repo, RUN)
    worktree = workspace.worktree
    engine = PatchEngine(worktree.path)
    snapshot = worktree.snapshot()

    applied = engine.apply(
        _edits(
            {
                "op": "replace",
                "path": "src/vector.h",
                "search": "typedef int Index;",
                "replace": "using Index = int;",
            },
            {
                "op": "replace",
                "path": "src/vector.cpp",
                "search": "return 0;",
                "replace": "return 1;",
            },
            {"op": "create", "path": "src/added.h", "content": "#pragma once\n"},
        )
    )
    assert applied.applied, applied.feedback()
    committed = worktree.commit("codecoremorph(test): three files")
    assert committed != snapshot

    second = worktree.snapshot()
    assert engine.apply(_edits({"op": "delete", "path": "README.md"})).applied
    (worktree.path / "stray.txt").write_bytes(b"x")
    worktree.rollback(second)
    workspace.cleanup()

    assert fingerprint(busy_repo, git) == before
    exclude_after = (busy_repo / ".git" / "info" / "exclude").read_bytes()
    assert exclude_after.startswith(exclude_before)
    assert exclude_after[len(exclude_before) :].strip().endswith(b"/.codecoremorph/")
    assert not (tmp_path / "hook-ran").exists()
    # The run's result is on its own branch, nowhere else.
    assert git(busy_repo, "log", "-1", "--format=%s", f"codecoremorph/{RUN}").strip() == (
        "codecoremorph(test): three files"
    )


def test_a_crash_mid_run_leaves_the_checkout_identical(busy_repo: Path, git) -> None:
    before = fingerprint(busy_repo, git)
    workspace = RunWorkspace.create(busy_repo, RUN)
    try:
        (workspace.worktree.path / "src" / "vector.h").write_bytes(b"half-written")
        raise RuntimeError("simulated crash")
    except RuntimeError:
        workspace.cleanup(delete_branch=True)
    assert fingerprint(busy_repo, git) == before
