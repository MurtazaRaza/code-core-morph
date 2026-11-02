"""Path safety for edits (SPEC §7.4 rule 4, invariant I-1).

On top of the syntactic checks in :data:`codecoremorph.core.models.WorktreePath` (relative,
normalized, no ``..``, no ``.git``), the filesystem checks here reject:

- paths matched by ``project.exclude``;
- paths whose final component is a symbolic link, and paths that resolve outside the
  worktree or into a ``.git`` directory through a link or junction;
- names Windows can't represent (reserved device names, trailing dots or spaces, ``<>:"|?*``
  and control characters), on every platform, so a change that works on Linux can't break
  a Windows checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models.base import check_worktree_path
from codecoremorph.core.models.edits import RejectionReason

_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
_INVALID_CHARS = frozenset('<>:"|?*') | {chr(i) for i in range(32)}


@dataclass(frozen=True)
class SafePath:
    rel: str
    target: Path  # root / rel, not resolved


@dataclass(frozen=True)
class PathProblem:
    reason: RejectionReason
    message: str


def check_edit_path(root: Path, rel: str, exclude: PathMatcher) -> SafePath | PathProblem:
    """Validate ``rel`` against the resolved worktree ``root``."""
    try:
        check_worktree_path(rel)
    except ValueError as exc:
        return PathProblem(RejectionReason.UNSAFE_PATH, str(exc))
    parts = rel.split("/")
    for part in parts:
        if part.split(".")[0].upper() in _RESERVED_NAMES:
            return PathProblem(
                RejectionReason.UNSAFE_PATH, f"{part!r} is a reserved device name on Windows"
            )
        if part.endswith((".", " ")):
            return PathProblem(RejectionReason.UNSAFE_PATH, f"{part!r} ends with a dot or space")
        if any(c in _INVALID_CHARS for c in part):
            return PathProblem(
                RejectionReason.UNSAFE_PATH,
                f"{part!r} contains a character that isn't allowed in Windows file names",
            )
    pattern = exclude.first_match(rel)
    if pattern is not None:
        return PathProblem(
            RejectionReason.EXCLUDED,
            f"the path is excluded by project.exclude ({pattern}) and is never edited",
        )

    target = root.joinpath(*parts)
    if target.is_symlink():
        return PathProblem(
            RejectionReason.UNSAFE_PATH,
            "the path is a symbolic link; edit the file it points to instead",
        )
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        return PathProblem(
            RejectionReason.UNSAFE_PATH, "the path resolves outside the worktree (through a link)"
        )
    if any(p.lower() == ".git" for p in resolved.relative_to(root).parts):
        return PathProblem(RejectionReason.UNSAFE_PATH, "the path resolves into a .git directory")
    return SafePath(rel, target)
