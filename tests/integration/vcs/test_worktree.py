"""The run worktree: snapshot, rollback, commit, diff, and the I-11 guard."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from codecoremorph.core.errors import NothingToCommitError, SafetyViolationError, VcsError
from codecoremorph.core.models import FileChangeKind
from codecoremorph.vcs import Repository, Worktree, parse_trailers

RUN = "20261102-141500-a3f9"
BRANCH = f"codecoremorph/{RUN}"


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Worktree:
    repository = Repository(repo)
    return repository.add_worktree(
        tmp_path / "worktrees" / RUN, BRANCH, repository.resolve_commit("HEAD")
    )


def tracked_bytes(path: Path, git) -> dict[str, bytes]:
    files = git(path, "ls-files", "-z").split("\0")
    return {f: (path / f).read_bytes() for f in files if f}


def test_rollback_discards_edits_new_files_and_deletions(worktree: Worktree, git) -> None:
    snapshot = worktree.snapshot()
    before = tracked_bytes(worktree.path, git)
    (worktree.path / "src" / "vector.h").write_bytes(b"garbage")
    (worktree.path / "README.md").unlink()
    (worktree.path / "new_dir" / "deep").mkdir(parents=True)
    (worktree.path / "new_dir" / "deep" / "x.cpp").write_bytes(b"x")
    (worktree.path / "build").mkdir()
    (worktree.path / "build" / "cache.o").write_bytes(b"ignored build output")

    worktree.rollback(snapshot)

    assert tracked_bytes(worktree.path, git) == before
    assert not (worktree.path / "new_dir").exists()
    assert (worktree.path / "build" / "cache.o").exists()  # ignored files survive (never -x)
    assert not worktree.has_changes()


def test_tracked_files_are_the_worktrees(worktree: Worktree, git) -> None:
    """What a port's report counts under `project.exclude` (D-62): the files git tracks in the
    worktree, not untracked ones."""
    (worktree.path / "untracked.cpp").write_bytes(b"x")
    files = worktree.tracked_files()
    assert files == sorted(f for f in git(worktree.path, "ls-files", "-z").split("\0") if f)
    assert "README.md" in files
    assert "untracked.cpp" not in files


def test_rollback_to_an_earlier_commit(worktree: Worktree) -> None:
    first = worktree.snapshot()
    (worktree.path / "a.txt").write_bytes(b"a")
    second = worktree.commit("add a")
    assert second != first
    worktree.rollback(first)
    assert worktree.head() == first
    assert not (worktree.path / "a.txt").exists()


_names = st.sampled_from(
    ["src/vector.h", "src/vector.cpp", "README.md", "src/util.h", "new.txt", "d/e.txt"]
)
_ops = st.lists(
    st.tuples(st.sampled_from(["write", "delete", "mkdir"]), _names, st.binary(max_size=40)),
    max_size=8,
)


@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(ops=_ops)
def test_rollback_after_arbitrary_writes(
    worktree: Worktree, git, ops: list[tuple[str, str, bytes]]
) -> None:
    snapshot = worktree.snapshot()
    before = tracked_bytes(worktree.path, git)
    try:
        for op, name, data in ops:
            target = worktree.path / name
            if op == "write" and not target.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif op == "delete" and target.is_file():
                target.unlink()
            elif op == "mkdir" and not target.exists():
                # Includes a directory where a tracked file was deleted.
                target.mkdir(parents=True)
    finally:
        worktree.rollback(snapshot)
    assert tracked_bytes(worktree.path, git) == before
    assert not worktree.has_changes()
    assert not (worktree.path / "d").exists()
    assert not (worktree.path / "new.txt").exists()


def test_commit_with_trailers_and_author(worktree: Worktree, git) -> None:
    (worktree.path / "src" / "vector.h").write_bytes(b"#pragma once\nusing Index = int;\n")
    sha = worktree.commit(
        "codecoremorph(replace_typedefs_with_using): src/vector.h\n\nReplaced 1 typedef.",
        trailers={"CodeCoreMorph-Run": RUN, "CodeCoreMorph-Unit": "U-0001"},
        author="CodeCoreMorph Bot <bot@example.com>",
    )
    message = git(worktree.path, "log", "-1", "--format=%B", sha)
    assert message.startswith("codecoremorph(replace_typedefs_with_using): src/vector.h\n\n")
    assert parse_trailers(message) == {"CodeCoreMorph-Run": RUN, "CodeCoreMorph-Unit": "U-0001"}
    assert git(worktree.path, "log", "-1", "--format=%an <%ae>|%cn <%ce>", sha).strip() == (
        "CodeCoreMorph Bot <bot@example.com>|Test User <test@example.com>"
    )
    assert git(worktree.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == BRANCH


def test_commit_without_changes(worktree: Worktree) -> None:
    with pytest.raises(NothingToCommitError):
        worktree.commit("nothing")


def test_commit_identity_fallbacks(
    worktree: Worktree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    (worktree.path / "a.txt").write_bytes(b"a")
    with pytest.raises(VcsError, match="no user identity"):
        worktree.commit("no identity")
    sha = worktree.commit("with author", author="Bot <bot@example.com>")
    assert (
        git(worktree.path, "log", "-1", "--format=%cn <%ce>", sha).strip()
        == "Bot <bot@example.com>"
    )


def test_diff_lists_changes_and_reverse_applies(worktree: Worktree, git) -> None:
    base = worktree.snapshot()
    before = tracked_bytes(worktree.path, git)
    (worktree.path / "src" / "vector.h").write_bytes(b"#pragma once\nusing Index = int;\nint n;\n")
    (worktree.path / "README.md").unlink()
    (worktree.path / "src" / "new.cpp").write_bytes(b"int x;\n")
    (worktree.path / "logo.bin").write_bytes(b"\x00\x01\x02\x03")

    diff = worktree.diff(base)

    by_path = {f.path: f for f in diff.files}
    assert by_path["src/vector.h"].change is FileChangeKind.MODIFIED
    assert (by_path["src/vector.h"].added_lines, by_path["src/vector.h"].removed_lines) == (2, 1)
    assert by_path["README.md"].change is FileChangeKind.DELETED
    assert by_path["src/new.cpp"].change is FileChangeKind.ADDED
    assert by_path["logo.bin"].added_lines is None  # binary
    assert diff.changed_lines == 2 + 1 + 1 + 1
    assert "+using Index = int;" in diff.patch

    patch = worktree.diff_bytes(base)
    subprocess.run(
        ["git", "apply", "-R", "--index", "-"], cwd=worktree.path, input=patch, check=True
    )
    after = tracked_bytes(worktree.path, git)
    assert after == before


def test_guard_refuses_a_worktree_on_another_branch(worktree: Worktree, git) -> None:
    snapshot = worktree.snapshot()
    git(worktree.path, "checkout", "--quiet", "-b", "someone-else")
    with pytest.raises(SafetyViolationError, match="someone-else"):
        worktree.rollback(snapshot)
    with pytest.raises(SafetyViolationError):
        worktree.commit("x")


def test_guard_refuses_a_path_that_is_not_the_worktree(repo: Path) -> None:
    impostor = Worktree(repo / "src", BRANCH)  # inside the user's checkout
    with pytest.raises(SafetyViolationError):
        impostor.rollback("0" * 40)


def test_rollback_rejects_non_shas(worktree: Worktree) -> None:
    with pytest.raises(ValueError, match="full commit SHA"):
        worktree.rollback("HEAD")


def test_worktree_with_crlf_checkout(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_gitconfig: Path
) -> None:
    config = tmp_path / "crlf-gitconfig"
    shutil.copy(test_gitconfig, config)
    with config.open("a", encoding="utf-8") as handle:
        handle.write("[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    repository = Repository(repo)
    worktree = repository.add_worktree(
        tmp_path / "wt" / RUN, BRANCH, repository.resolve_commit("HEAD")
    )
    snapshot = worktree.snapshot()
    assert not worktree.has_changes()
    (worktree.path / "README.md").write_bytes(b"# changed\r\n")
    assert worktree.diff(snapshot).paths == ["README.md"]
    worktree.rollback(snapshot)
    assert not worktree.has_changes()
