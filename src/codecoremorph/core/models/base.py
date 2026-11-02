"""Shared base model and constrained types for the core data contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from codecoremorph.core.ids import validate_run_id, validate_unit_id


class CoreModel(BaseModel):
    """Base of every core contract: immutable, and unknown fields are errors, not ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")


_DRIVE = re.compile(r"^[A-Za-z]:")


def check_worktree_path(value: str) -> str:
    """Validate the syntax of a worktree-relative path.

    Accepts normalized, relative, forward-slash paths that stay inside the worktree and don't
    enter ``.git``. PatchEngine adds the filesystem checks (symlinks, excluded paths) in Phase 1.
    """
    if not value:
        raise ValueError("path is empty")
    if "\x00" in value:
        raise ValueError("path contains a NUL character")
    if "\\" in value:
        raise ValueError(f"path {value!r} must use forward slashes")
    if value.startswith("/") or _DRIVE.match(value):
        raise ValueError(f"path {value!r} must be relative to the worktree")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"path {value!r} must be normalized (no empty, '.' or '..' segments)")
    if any(part.lower() == ".git" for part in parts):
        raise ValueError(f"path {value!r} points into a .git directory")
    return value


WorktreePath = Annotated[str, AfterValidator(check_worktree_path)]
RunId = Annotated[str, AfterValidator(validate_run_id)]
UnitId = Annotated[str, AfterValidator(validate_unit_id)]


class Role(StrEnum):
    """Agent roles that can be routed to different models (FR-3.7)."""

    EDITOR = "editor"
    FIXER = "fixer"
    TEST_WRITER = "test_writer"
    SUMMARIZER = "summarizer"
