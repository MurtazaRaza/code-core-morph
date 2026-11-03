"""The user's repository. Read-mostly: CodeCoreMorph never modifies the user's working tree,
index or branches (NFR-2.1, NFR-2.5).

The only writes here are the ones NFR-2.1 allows or that touch only what CodeCoreMorph
created: the ``.codecoremorph/`` line in ``.git/info/exclude``, and adding and removing run
worktrees and their ``codecoremorph/<run-id>`` branches.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.errors import GitCommandError, SafetyViolationError, VcsError
from codecoremorph.core.ids import BRANCH_PREFIX, is_run_id
from codecoremorph.core.models.vcs import DirtyState
from codecoremorph.core.paths import STATE_DIR_NAME
from codecoremorph.vcs.git import Git
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

EXCLUDE_ENTRY = f"/{STATE_DIR_NAME}/"
_EXCLUDE_EQUIVALENTS = frozenset(
    {EXCLUDE_ENTRY, f"{STATE_DIR_NAME}/", f"/{STATE_DIR_NAME}", STATE_DIR_NAME}
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


@dataclass(frozen=True)
class RepoFingerprint:
    """The user's side of the repository: HEAD, the refs CodeCoreMorph didn't create, and
    the uncommitted files. Compared before and after each unit (I-11)."""

    head: str
    refs: str
    files: str

    def changes(self, later: RepoFingerprint) -> list[str]:
        found: list[str] = []
        if later.head != self.head:
            found.append("HEAD moved")
        if later.refs != self.refs:
            found.append("branches or tags changed")
        if later.files != self.files:
            found.append("uncommitted files changed")
        return found


@dataclass(frozen=True)
class WorktreeEntry:
    path: Path
    head: str | None
    branch: str | None  # short name, e.g. codecoremorph/20261102-141500-a3f9


def is_run_branch(branch: str) -> bool:
    return branch.startswith(BRANCH_PREFIX) and is_run_id(branch.removeprefix(BRANCH_PREFIX))


class Repository:
    """A git repository the user owns."""

    def __init__(self, root: Path):
        self.root = root
        self.git = Git(root)

    @classmethod
    def discover(cls, path: Path) -> Repository:
        """The repository containing ``path``. Fails for bare repositories and non-repos."""
        if not path.is_dir():
            raise VcsError(f"{path} is not a directory")
        probe = Git(path)
        result = probe.run("rev-parse", "--is-inside-work-tree", "--show-toplevel", check=False)
        lines = result.text.splitlines()
        if result.returncode != 0 or not lines or lines[0] != "true" or len(lines) < 2:
            raise VcsError(
                f"{path} is not inside a git working tree",
                reason="CodeCoreMorph migrates git repositories, starting from a commit.",
                hint="Run it inside your repository, or `git init` and commit your code first.",
            )
        return cls(Path(lines[1]))

    # ------------------------------------------------------------------ reading

    def resolve_commit(self, revision: str) -> str:
        """The commit SHA a revision names. ``--end-of-options`` stops option injection."""
        result = self.git.run(
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{revision}^{{commit}}",
            check=False,
        )
        if result.returncode != 0:
            raise VcsError(
                f"{revision!r} doesn't name a commit in {self.root}",
                hint="Use HEAD, a branch, a tag or a commit SHA (and commit at least once).",
            )
        return result.text

    def dirty_state(self) -> DirtyState:
        """Uncommitted changes, which a run doesn't include. Doesn't touch the index."""
        out = self.git.run("status", "--porcelain=v1", "-z", "--untracked-files=normal").stdout
        modified: list[str] = []
        untracked: list[str] = []
        entries = out.split(b"\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            status, path = entry[:2], entry[3:].decode("utf-8", errors="replace")
            if status[:1] in (b"R", b"C"):
                i += 1  # the original path follows a rename or copy
            if path == f"{STATE_DIR_NAME}/" or path.startswith(f"{STATE_DIR_NAME}/"):
                continue
            (untracked if status == b"??" else modified).append(path)
        return DirtyState(modified=modified, untracked=untracked)

    def fingerprint(self) -> RepoFingerprint:
        """What the user's checkout looks like now; never touches the index (I-11)."""
        symbolic = self.git.run("symbolic-ref", "--quiet", "HEAD", check=False).text
        head = self.git.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).text
        refs = [
            line
            for line in self.git.text(
                "for-each-ref", "--format=%(refname) %(objectname)"
            ).splitlines()
            if not line.startswith(f"refs/heads/{BRANCH_PREFIX}")
        ]
        dirty = self.dirty_state()
        files = hashlib.sha256()
        for path in sorted([*dirty.modified, *dirty.untracked]):
            files.update(path.encode("utf-8", "surrogateescape") + b"\0")
            try:
                stat = (self.root / path).stat()
                files.update(f"{stat.st_size}:{stat.st_mtime_ns}\0".encode())
            except OSError:
                files.update(b"missing\0")
        return RepoFingerprint(
            head=f"{symbolic} {head}",
            refs=hashlib.sha256("\n".join(sorted(refs)).encode("utf-8")).hexdigest(),
            files=files.hexdigest(),
        )

    def git_path(self, name: str) -> Path:
        """Location of a file in the git directory, resolved for linked worktrees too."""
        return self.root / self.git.text("rev-parse", "--git-path", name)

    def branch_exists(self, branch: str) -> bool:
        result = self.git.run(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
        return result.returncode == 0

    def run_branches(self) -> list[str]:
        out = self.git.text(
            "for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}"
        )
        return sorted(b for b in out.splitlines() if is_run_branch(b))

    def worktrees(self) -> list[WorktreeEntry]:
        # Newline-separated porcelain: `-z` needs git 2.36, and the minimum is 2.30.
        out = self.git.run("worktree", "list", "--porcelain").stdout.decode("utf-8", "replace")
        entries: list[WorktreeEntry] = []
        for block in out.replace("\r\n", "\n").split("\n\n"):
            fields = {
                key: value
                for key, _, value in (line.partition(" ") for line in block.split("\n"))
                if key
            }
            if "worktree" not in fields:
                continue
            branch = fields.get("branch") or None
            entries.append(
                WorktreeEntry(
                    path=Path(fields["worktree"]),
                    head=fields.get("HEAD") or None,
                    branch=branch.removeprefix("refs/heads/") if branch else None,
                )
            )
        return entries

    # ------------------------------------------------------------------ allowed writes

    def ensure_excluded(self) -> bool:
        """Add ``/.codecoremorph/`` to ``.git/info/exclude`` (never ``.gitignore``).

        Returns True if the line was added. The file's line endings are kept.
        """
        path = self.git_path("info/exclude")
        existing = path.read_bytes() if path.exists() else b""
        lines = existing.decode("utf-8", errors="replace").splitlines()
        if any(line.strip() in _EXCLUDE_EQUIVALENTS for line in lines):
            return False
        newline = b"\r\n" if b"\r\n" in existing else b"\n"
        separator = newline if existing and not existing.endswith(b"\n") else b""
        addition = b"# CodeCoreMorph state directory" + newline + EXCLUDE_ENTRY.encode() + newline
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".codecoremorph-tmp")
        tmp.write_bytes(existing + separator + addition)
        tmp.replace(path)
        log.debug("added %s to %s", EXCLUDE_ENTRY, path)
        return True

    def add_worktree(self, path: Path, branch: str, base_sha: str) -> Worktree:
        """Create ``branch`` at ``base_sha`` and check it out in a new worktree at ``path``."""
        if not is_run_branch(branch):
            raise SafetyViolationError(f"refusing to create non-CodeCoreMorph branch {branch!r}")
        if not _SHA_RE.match(base_sha):
            raise ValueError(f"base must be a full commit SHA, got {base_sha!r}")
        if self.branch_exists(branch):
            raise VcsError(
                f"branch {branch} already exists",
                hint="Each run gets a new branch. Delete the old one with "
                "`codecoremorph clean --branches`, or start a new run.",
            )
        if path.exists() and any(path.iterdir()):
            raise VcsError(f"worktree directory {path} already exists and isn't empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git.run("worktree", "add", "--quiet", "-b", branch, str(path), base_sha)
        return Worktree(path, branch)

    def submodules_at(self, commit: str) -> list[tuple[str, str]]:
        """``(path, commit)`` of every submodule (gitlink) in ``commit``'s tree."""
        return _gitlinks(self.git, commit)

    def populate_submodules(self, worktree: Worktree, commit: str) -> list[str]:
        """Check out ``commit``'s submodules in a run worktree, cloned from the user's own
        submodule checkouts: no network access, and nothing in the user's repository changes
        (``git worktree add`` leaves submodules empty).

        Returns the submodule paths that couldn't be populated because the repository doesn't
        have them checked out at a version containing the pinned commit.
        """
        return _populate(self.root, worktree.path, commit, prefix="")

    def remove_worktree(self, path: Path) -> None:
        """Remove a run worktree this repository registered. Never touches other worktrees."""
        entry = next((w for w in self.worktrees() if same_path(w.path, path)), None)
        if entry is None:
            if path.exists() and (path / ".git").is_file():
                raise SafetyViolationError(f"{path} is not a worktree of {self.root}")
            self.git.run("worktree", "prune")
            return
        if same_path(entry.path, self.root) or not (entry.branch and is_run_branch(entry.branch)):
            raise SafetyViolationError(f"refusing to remove worktree {path}: not a run worktree")
        try:
            self.git.run("worktree", "remove", "--force", "--force", str(path))
        except GitCommandError:
            # Windows can refuse while a file is open; remove what we can, then prune.
            shutil.rmtree(path, ignore_errors=True)
        self.git.run("worktree", "prune")

    def delete_run_branch(self, branch: str) -> None:
        """Delete a ``codecoremorph/<run-id>`` branch. Other branches are refused (FR-6.5)."""
        if not is_run_branch(branch):
            raise SafetyViolationError(f"refusing to delete non-CodeCoreMorph branch {branch!r}")
        if self.branch_exists(branch):
            self.git.run("branch", "--delete", "--force", "--", branch)


def _gitlinks(git: Git, commit: str) -> list[tuple[str, str]]:
    out = git.run("ls-tree", "-r", "-z", "--full-tree", commit).stdout.decode("utf-8", "replace")
    links: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[0] == "160000":
            links.append((path, parts[2]))
    return links


def _populate(source_root: Path, target_root: Path, commit: str, *, prefix: str) -> list[str]:
    missing: list[str] = []
    for path, sha in _gitlinks(Git(source_root), commit):
        name = prefix + path
        source = source_root / path
        target = target_root / path
        if not (source / ".git").exists():
            missing.append(name)
            continue
        has_commit = Git(source).run("cat-file", "-e", f"{sha}^{{commit}}", check=False)
        if has_commit.returncode != 0:
            missing.append(name)
            continue
        if target.exists() and any(target.iterdir()):
            continue  # already populated
        target.mkdir(parents=True, exist_ok=True)
        # --shared borrows the user's objects (read-only) instead of copying them.
        Git(target).run("clone", "--quiet", "--no-checkout", "--shared", str(source), ".")
        Git(target).run("checkout", "--quiet", "--detach", sha)
        missing += _populate(source, target, sha, prefix=f"{name}/")
    return missing
