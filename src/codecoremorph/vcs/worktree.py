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

from codecoremorph.core.errors import NothingToCommitError, SafetyViolationError, VcsError
from codecoremorph.core.models.vcs import ChangedFile, DiffResult, FileChangeKind
from codecoremorph.vcs.git import Git
from codecoremorph.vcs.messages import with_trailers

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
        self.git = Git(path)

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

    def stage_all(self) -> None:
        self.git.run("add", "--all")

    def has_changes(self) -> bool:
        return bool(self.git.run("status", "--porcelain=v1", "-z").stdout)

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

    def _has_identity(self) -> bool:
        return self.git.run("var", "GIT_COMMITTER_IDENT", check=False).returncode == 0
