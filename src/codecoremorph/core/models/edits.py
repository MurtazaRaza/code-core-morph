"""Structured edits submitted by the agent and applied by PatchEngine (SPEC §7.4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from codecoremorph.core.models.base import CoreModel, WorktreePath


class ReplaceEdit(CoreModel):
    """Replace one unique occurrence of ``search`` with ``replace``."""

    op: Literal["replace"] = "replace"
    path: WorktreePath
    search: str = Field(min_length=1, description="Exact existing text; must match exactly once")
    replace: str


class CreateEdit(CoreModel):
    """Create a new file. Fails if the file already exists."""

    op: Literal["create"] = "create"
    path: WorktreePath
    content: str


class DeleteEdit(CoreModel):
    """Delete an existing file."""

    op: Literal["delete"] = "delete"
    path: WorktreePath


Edit = Annotated[ReplaceEdit | CreateEdit | DeleteEdit, Field(discriminator="op")]


class EditBatch(CoreModel):
    """One ``submit_edits`` call: every edit applies, or none does (FR-3.3)."""

    # Empty when the agent judges the unit done as it is: what is left is a warning-level site
    # it should keep, or guidance with nothing safe to change. It is verified like any other
    # submission, so it succeeds only if the unit already holds (D-64).
    edits: list[Edit]
    summary: str = Field(min_length=1)
    review_notes: list[str] = Field(default_factory=list[str])

    @property
    def paths(self) -> list[str]:
        """Touched paths in first-seen order, without duplicates."""
        return list(dict.fromkeys(e.path for e in self.edits))


class RejectionReason(StrEnum):
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    FILE_MISSING = "file_missing"
    FILE_EXISTS = "file_exists"
    UNSAFE_PATH = "unsafe_path"
    EXCLUDED = "excluded"
    BINARY_FILE = "binary_file"
    ENCODING = "encoding"
    WRITE_FAILED = "write_failed"


class CandidateRegion(CoreModel):
    """The part of a file that most resembles a ``search`` text that wasn't found."""

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str
    similarity: float = Field(ge=0, le=1)


class EditRejection(CoreModel):
    """Why one edit of a batch could not be applied (SPEC §7.4 rule 5)."""

    edit_index: int = Field(ge=0, description="0-based position in the batch")
    op: Literal["replace", "create", "delete"]
    path: str
    reason: RejectionReason
    message: str
    match_lines: list[int] = Field(
        default_factory=list[int], description="1-based lines of ambiguous matches"
    )
    candidate: CandidateRegion | None = None

    def describe(self) -> str:
        """Feedback text for the agent."""
        lines = [f"Edit {self.edit_index + 1} ({self.op} {self.path}): {self.message}"]
        if self.candidate is not None:
            c = self.candidate
            lines.append(
                f"The closest text is at lines {c.start_line}-{c.end_line} "
                f"({c.similarity:.0%} similar):"
            )
            lines.append(c.text)
        return "\n".join(lines)


class ApplyResult(CoreModel):
    """Outcome of applying a batch. ``applied`` is all or nothing (FR-3.3)."""

    applied: bool
    changed_files: list[str] = Field(default_factory=list[str])
    rejections: list[EditRejection] = Field(default_factory=list[EditRejection])
    whitespace_tolerant: list[int] = Field(
        default_factory=list[int],
        description="0-based indexes of edits matched only after ignoring indentation",
    )

    def feedback(self) -> str:
        return "\n\n".join(r.describe() for r in self.rejections)
