"""A run worktree: the only place CodeCoreMorph runs destructive git commands (§7.9).

Before each destructive operation the worktree checks that it is still where it was created
and on its own ``codecoremorph/<run-id>`` branch, so a bug can't point ``reset --hard`` at
the user's checkout (I-11).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from codecoremorph.core.errors import NothingToCommitError, SafetyViolationError, VcsError
from codecoremorph.core.models.vcs import ChangedFile, DiffResult, FileChangeKind
from codecoremorph.vcs.git import LF_CHECKOUT, Git
from codecoremorph.vcs.messages import TRAILER_PREFIX, parse_trailers, with_trailers

LineEndings = Literal["native", "lf"]
"""How a run worktree is checked out: ``native`` with the machine's git settings, as the user's
own checkout is; ``lf`` as on Linux (:data:`LF_CHECKOUT`), for builds in a Linux container
(D-68). With Git for Windows' ``core.autocrlf=true``, a native checkout gives a ``configure``
script CRLF line endings, and ``sh`` fails on them."""

_CHECKOUT_MARKER = "codecoremorph-checkout"  # in the worktree's directory under .git/worktrees


def admin_dir(path: Path) -> Path | None:
    """The worktree's own directory in the repository (``.git/worktrees/<name>``), from its
    ``.git`` file; None when ``path`` holds no worktree."""
    dot_git = path / ".git"
    if not dot_git.is_file():
        return None
    text = dot_git.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    target = Path(text.removeprefix("gitdir:").strip())
    return target if target.is_absolute() else path / target


def checkout_mode(path: Path) -> LineEndings:
    """The line endings the worktree at ``path`` was checked out with (``native`` when it
    recorded none)."""
    admin = admin_dir(path)
    marker = admin / _CHECKOUT_MARKER if admin is not None else None
    if marker is None or not marker.is_file():
        return "native"
    return "lf" if marker.read_text(encoding="utf-8").strip() == "lf" else "native"


def record_checkout(path: Path, mode: LineEndings) -> None:
    """Remember how the worktree at ``path`` was checked out, so every later command in it
    (status, reset, checkout, commit) reads its files the same way."""
    admin = admin_dir(path)
    if admin is None:
        raise VcsError(f"{path} is not a git worktree")
    (admin / _CHECKOUT_MARKER).write_text(f"{mode}\n", encoding="utf-8")


_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_AUTHOR_RE = re.compile(r"^(?P<name>[^<>\n]+?) <(?P<email>[^<>\s]+)>$")
_CHANGE_KINDS = {
    "A": FileChangeKind.ADDED,
    "M": FileChangeKind.MODIFIED,
    "D": FileChangeKind.DELETED,
    "T": FileChangeKind.TYPE_CHANGED,
}
_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--no-renames")


def _check_sha(sha: str) -> str:
    if not _SHA_RE.match(sha):
        raise ValueError(f"expected a full commit SHA, got {sha!r}")
    return sha


class Worktree:
    def __init__(self, path: Path, branch: str):
        self.path = path
        self.branch = branch
        self.line_endings: LineEndings = checkout_mode(path)
        self.git = Git(path, config=LF_CHECKOUT if self.line_endings == "lf" else ())

    def verify(self) -> None:
        """Raise unless this is still the run worktree, on the run branch (I-11)."""
        toplevel = Path(self.git.text("rev-parse", "--show-toplevel"))
        if os.path.normcase(toplevel.resolve()) != os.path.normcase(self.path.resolve()):
            raise SafetyViolationError(
                f"worktree {self.path} resolves to {toplevel}; refusing to continue"
            )
        branch = self.git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False).text
        if branch != self.branch:
            raise SafetyViolationError(
                f"worktree {self.path} is on {branch or 'a detached HEAD'}, not {self.branch}"
            )

    def head(self) -> str:
        return self.git.text("rev-parse", "--verify", "HEAD")

    def tracked_files(self) -> list[str]:
        """Every file git tracks in the worktree, the files of checked-out submodules too, as
        sorted POSIX paths from its root. Reads only."""
        out = self.git.run("ls-files", "-z", "--recurse-submodules").stdout
        return sorted({p for p in out.decode("utf-8", errors="replace").split("\0") if p})

    def snapshot(self) -> str:
        """The commit to roll back to if the next attempt fails (FR-6.3)."""
        return self.head()

    def rollback(self, sha: str) -> None:
        """Discard everything since ``sha``: ``reset --hard`` plus ``clean -fd``, never ``-x``,
        so ignored build caches survive (FR-6.3, FR-6.7)."""
        _check_sha(sha)
        self.verify()
        self.git.run("reset", "--hard", "--quiet", sha)
        self.git.run("clean", "-f", "-d", "--quiet")

    def reset_soft(self, sha: str) -> None:
        """Move the branch back to ``sha`` and keep every change since as uncommitted work
        (squashes a host unit's checkpoint commits into one, D-41)."""
        _check_sha(sha)
        self.verify()
        self.git.run("reset", "--soft", "--quiet", sha)

    def stage_all(self) -> None:
        self.git.run("add", "--all")

    def has_changes(self) -> bool:
        return bool(self.git.run("status", "--porcelain=v1", "-z").stdout)

    def write_tree(self) -> str:
        """The tree object of the worktree as it is now, without committing: a point to diff
        against later (the agent's own changes, after the codemods')."""
        self.stage_all()
        return self.git.text("write-tree")

    def restore_tree(self, tree: str) -> None:
        """Put every file back as it is in ``tree`` (from :meth:`write_tree`); files made since
        are removed. Only inside the run worktree (I-11)."""
        _check_sha(tree)
        self.verify()
        self.git.run("checkout", "--quiet", tree, "--", ".")
        self.git.run("clean", "-f", "-d", "--quiet")

    def show(self, revision: str, path: str) -> bytes | None:
        """A file's bytes at ``revision`` (a commit or tree SHA), or None if it didn't exist."""
        _check_sha(revision)
        result = self.git.run("cat-file", "blob", f"{revision}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def diff_bytes(self, since: str) -> bytes:
        """The git-compatible patch (binary included) of all changes since ``since``."""
        _check_sha(since)
        self.stage_all()
        return self.git.run("diff", "--cached", "--binary", *_DIFF_FLAGS, since, "--").stdout

    def diff(self, since: str) -> DiffResult:
        """Unified diff plus changed files and line counts since ``since`` (FR-3.2)."""
        patch = self.diff_bytes(since)
        numstat = self.git.run("diff", "--cached", "--numstat", "-z", *_DIFF_FLAGS, since, "--")
        status = self.git.run("diff", "--cached", "--name-status", "-z", *_DIFF_FLAGS, since, "--")
        counts: dict[str, tuple[int | None, int | None]] = {}
        for record in numstat.stdout.decode("utf-8", "replace").split("\0"):
            parts = record.split("\t", 2)
            if len(parts) == 3:
                added, removed, path = parts
                counts[path] = (
                    int(added) if added.isdigit() else None,
                    int(removed) if removed.isdigit() else None,
                )
        fields = status.stdout.decode("utf-8", "replace").split("\0")
        files: list[ChangedFile] = []
        for code, path in zip(fields[::2], fields[1::2], strict=False):
            if not code:
                continue
            added, removed = counts.get(path, (None, None))
            files.append(
                ChangedFile(
                    path=path,
                    change=_CHANGE_KINDS.get(code[:1], FileChangeKind.MODIFIED),
                    added_lines=added,
                    removed_lines=removed,
                )
            )
        return DiffResult(base=since, patch=patch.decode("utf-8", errors="replace"), files=files)

    def commit(
        self,
        message: str,
        *,
        trailers: Mapping[str, str] | None = None,
        author: str | None = None,
    ) -> str:
        """Commit every change in the worktree and return the new SHA (FR-6.4).

        ``author`` ("Name <email>") overrides the commit author; the committer is the user's
        git identity, or the author when git has no identity configured.
        """
        self.verify()
        self.stage_all()
        if self.git.run("diff", "--cached", "--quiet", check=False).returncode == 0:
            raise NothingToCommitError("there are no changes to commit in the run worktree")
        args = ["commit", "--quiet", "--cleanup=verbatim", "--file=-"]
        env: dict[str, str] = {}
        if author is not None:
            match = _AUTHOR_RE.match(author)
            if match is None:
                raise ValueError(f"author must look like 'Name <email>', got {author!r}")
            args.append(f"--author={author}")
            if not self._has_identity():
                env |= {
                    "GIT_COMMITTER_NAME": match.group("name"),
                    "GIT_COMMITTER_EMAIL": match.group("email"),
                }
        elif not self._has_identity():
            raise VcsError(
                "git has no user identity to commit with",
                hint='Run `git config --global user.name "Your Name"` and '
                "`git config --global user.email you@example.com`, or set git.author in the "
                "manifest.",
            )
        text = with_trailers(message, trailers) if trailers else message.rstrip("\n") + "\n"
        self.git.run(*args, stdin=text.encode("utf-8"), env=env)
        return self.head()

    def parent(self, sha: str) -> str:
        return self.git.text("rev-parse", "--verify", f"{_check_sha(sha)}^")

    def below(self, author_email: str, since: str) -> str:
        """HEAD without the commits on top of it authored by ``author_email`` (a host unit's
        checkpoints, D-41): the newest commit since ``since`` by anyone else, or ``since``."""
        out = self.git.run("log", "--format=%H %ae", f"{_check_sha(since)}..HEAD").stdout
        for line in out.decode("utf-8", "replace").splitlines():
            sha, _, email = line.partition(" ")
            if email != author_email:
                return sha
        return since

    def trailer_commits(self, since: str, name: str) -> list[tuple[str, str]]:
        """``(commit, value)`` for each commit since ``since`` with a ``CodeCoreMorph-<name>``
        trailer, newest first."""
        out = self.git.run("log", "--format=%H%x00%B%x01", f"{_check_sha(since)}..HEAD").stdout
        found: list[tuple[str, str]] = []
        for record in out.decode("utf-8", "replace").split("\x01"):
            sha, _, body = record.strip().partition("\0")
            value = parse_trailers(body).get(f"{TRAILER_PREFIX}{name}")
            if sha and value:
                found.append((sha, value))
        return found

    def unit_commits(self, since: str) -> dict[str, str]:
        """Unit ID -> commit, for the unit commits on the branch since ``since`` (from their
        ``CodeCoreMorph-Unit`` trailers). A resumed run finds the units that committed before
        the engine stopped, even when its checkpoint didn't record them."""
        found: dict[str, str] = {}
        for sha, unit in self.trailer_commits(since, "Unit"):
            found.setdefault(unit, sha)
        return found

    def can_commit(self, author: str | None) -> bool:
        """Whether :meth:`commit` with this ``author`` has an identity to commit with."""
        return author is not None or self._has_identity()

    def _has_identity(self) -> bool:
        return self.git.run("var", "GIT_COMMITTER_IDENT", check=False).returncode == 0
