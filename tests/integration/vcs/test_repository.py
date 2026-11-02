"""The user's repository: discovery, revisions, dirty state, the exclude file, worktrees.

Every test works on a temporary repository; none touches this project's repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.core.errors import SafetyViolationError, VcsError
from codecoremorph.vcs import Repository, is_run_branch
from codecoremorph.vcs.repository import EXCLUDE_ENTRY

RUN = "20261102-141500-a3f9"
BRANCH = f"codecoremorph/{RUN}"


def test_discover_from_a_subdirectory(repo: Path) -> None:
    found = Repository.discover(repo / "src")
    assert found.root.resolve() == repo.resolve()


def test_discover_outside_a_repository(tmp_path: Path) -> None:
    with pytest.raises(VcsError, match="not inside a git working tree") as info:
        Repository.discover(tmp_path)
    assert info.value.hint is not None


def test_resolve_commit(repo: Path, git) -> None:
    repository = Repository(repo)
    head = git(repo, "rev-parse", "HEAD").strip()
    assert repository.resolve_commit("HEAD") == head
    assert repository.resolve_commit("main") == head
    assert repository.resolve_commit(head[:8]) == head
    assert repository.resolve_commit("HEAD~1") == git(repo, "rev-parse", "HEAD~1").strip()


@pytest.mark.parametrize("revision", ["no-such-branch", "--output=pwned.txt", "HEAD~99"])
def test_bad_revisions_are_errors_not_options(repo: Path, revision: str) -> None:
    with pytest.raises(VcsError, match="doesn't name a commit"):
        Repository(repo).resolve_commit(revision)
    assert not (repo / "pwned.txt").exists()


def test_dirty_state_never_writes_the_index(repo: Path, git) -> None:
    (repo / "README.md").write_bytes(b"# changed\n")  # modified
    (repo / "src" / "util.h").write_bytes(b"// staged\n")
    git(repo, "add", "src/util.h")  # staged
    git(repo, "mv", "src/vector.h", "src/vec.h")  # renamed
    (repo / "notes.txt").write_bytes(b"untracked\n")
    (repo / ".codecoremorph").mkdir()
    (repo / ".codecoremorph" / "lock").write_bytes(b"{}")
    index = repo / ".git" / "index"
    before = index.read_bytes()

    dirty = Repository(repo).dirty_state()

    assert dirty.is_dirty
    assert sorted(dirty.modified) == ["README.md", "src/util.h", "src/vec.h"]
    assert dirty.untracked == ["notes.txt"]  # .codecoremorph/ is never reported
    assert index.read_bytes() == before


def test_clean_repository_is_not_dirty(repo: Path) -> None:
    assert not Repository(repo).dirty_state().is_dirty


def test_exclude_entry_is_added_once_and_keeps_line_endings(repo: Path) -> None:
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_bytes(b"# user rules\r\n*.log")
    repository = Repository(repo)
    assert repository.ensure_excluded() is True
    assert repository.ensure_excluded() is False
    assert exclude.read_bytes() == (
        b"# user rules\r\n*.log\r\n# CodeCoreMorph state directory\r\n"
        + EXCLUDE_ENTRY.encode()
        + b"\r\n"
    )
    (repo / ".codecoremorph").mkdir()
    (repo / ".codecoremorph" / "x").write_bytes(b"x")
    assert not repository.dirty_state().untracked


def test_exclude_from_a_linked_worktree_goes_to_the_common_dir(repo: Path, git) -> None:
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "--quiet", "-b", "feature", str(linked))
    assert Repository(linked).ensure_excluded()
    assert EXCLUDE_ENTRY in (repo / ".git" / "info" / "exclude").read_text()


def test_worktree_lifecycle_and_branch_rules(repo: Path, tmp_path: Path, git) -> None:
    repository = Repository(repo)
    head = repository.resolve_commit("HEAD")
    path = tmp_path / "worktrees" / RUN
    worktree = repository.add_worktree(path, BRANCH, head)
    assert worktree.head() == head
    assert is_run_branch(BRANCH)
    assert repository.run_branches() == [BRANCH]
    assert any(e.branch == BRANCH for e in repository.worktrees())

    with pytest.raises(VcsError, match="already exists"):
        repository.add_worktree(tmp_path / "other", BRANCH, head)

    repository.remove_worktree(path)
    assert not path.exists()
    assert repository.branch_exists(BRANCH)  # the branch is the run's output
    repository.delete_run_branch(BRANCH)
    assert not repository.branch_exists(BRANCH)
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("branch", ["main", "feature/x", "codecoremorph/not-a-run-id"])
def test_only_run_branches_can_be_created_or_deleted(
    repo: Path, tmp_path: Path, branch: str
) -> None:
    repository = Repository(repo)
    with pytest.raises(SafetyViolationError):
        repository.delete_run_branch(branch)
    with pytest.raises(SafetyViolationError):
        repository.add_worktree(tmp_path / "wt", branch, repository.resolve_commit("HEAD"))
    if branch == "main":
        assert repository.branch_exists("main")


def test_the_users_own_worktrees_are_never_removed(repo: Path, git) -> None:
    repository = Repository(repo)
    with pytest.raises(SafetyViolationError):
        repository.remove_worktree(repo)
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "--quiet", "-b", "feature", str(linked))
    with pytest.raises(SafetyViolationError):
        repository.remove_worktree(linked)
    assert linked.exists()


def test_user_hooks_never_run(repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n".encode())
    hook.chmod(0o755)
    repository = Repository(repo)
    worktree = repository.add_worktree(
        tmp_path / "wt" / RUN, BRANCH, repository.resolve_commit("HEAD")
    )
    (worktree.path / "new.txt").write_bytes(b"x")
    worktree.commit("a commit")
    assert not marker.exists()


def test_worktrees_get_submodules_from_the_users_checkout(repo: Path, tmp_path: Path, git) -> None:
    lib = tmp_path / "lib"
    git(tmp_path, "init", "--quiet", str(lib))
    (lib / "lib.h").write_text("int lib();\n", encoding="utf-8")
    git(lib, "add", "--all")
    git(lib, "commit", "--quiet", "-m", "lib")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(lib),
        "vendor/lib")  # fmt: skip
    # a submodule the user never checked out: a gitlink with no directory behind it
    ghost = git(lib, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{ghost},vendor/ghost")
    git(repo, "commit", "--quiet", "-m", "vendor lib")
    before = git(repo, "status", "--porcelain")
    config = (repo / ".git" / "config").read_bytes()

    repository = Repository(repo)
    base = repository.resolve_commit("HEAD")
    assert repository.submodules_at(base) == [("vendor/ghost", ghost), ("vendor/lib", ghost)]
    worktree = repository.add_worktree(tmp_path / "wt", BRANCH, base)
    assert repository.populate_submodules(worktree, base) == ["vendor/ghost"]
    assert (worktree.path / "vendor" / "lib" / "lib.h").read_text() == "int lib();\n"
    assert git(worktree.path, "status", "--porcelain") == ""  # the submodule is at its commit
    assert repository.populate_submodules(worktree, base) == ["vendor/ghost"]  # idempotent

    # the user's repository is unchanged, and the worktree still goes away
    assert git(repo, "status", "--porcelain") == before
    assert (repo / ".git" / "config").read_bytes() == config
    repository.remove_worktree(worktree.path)
    assert not worktree.path.exists()
