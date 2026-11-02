"""A unit driven by a host agent (D-41): the state kept between its commands (SPEC §7.6.1).

The host agent (Claude Code, through the ``codecoremorph`` skill) makes the edits; each
``codecoremorph unit`` command loads this state from ``.codecoremorph/runs/<run-id>/host.json``,
takes one step and saves it again.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from codecoremorph.core.models.adapters import CodemodResult
from codecoremorph.core.models.attempts import Attempt
from codecoremorph.core.models.base import CoreModel, RunId
from codecoremorph.core.models.planning import WorkUnit
from codecoremorph.core.models.rules import RuleSite
from codecoremorph.core.models.vcs import DirtyState


class HostStatus(StrEnum):
    WAITING = "waiting_for_agent"  # the host reads the context and submits edits
    SUCCEEDED = "succeeded"  # committed to the run branch (or nothing needed changing)
    FAILED = "failed"  # the retry budget ran out; rolled back
    ABORTED = "aborted"  # the host or user gave up; rolled back

    @property
    def is_terminal(self) -> bool:
        return self is not HostStatus.WAITING


class HostFeedbackKind(StrEnum):
    INITIAL = "initial"  # the unit's first context: the current build, if it is broken
    REJECTED = "rejected"  # the last submission wasn't applied; same attempt
    FAILED = "failed"  # the last attempt was applied and failed verification


class HostUnitState(CoreModel):
    run_id: RunId
    config_path: str = Field(description="The manifest, absolute")
    worktree: str = Field(description="The run worktree, absolute")
    sandbox: str = Field(description='The executor the unit started with: "native" or "docker"')
    base_commit: str
    unit: WorkUnit
    source: str
    target: str
    snapshot: str = Field(description="HEAD when the unit started: the rollback target")
    head: str = Field(description="The last checkpoint; the worktree is clean on it")
    index_path: str | None = None
    build_files: list[str] = Field(default_factory=list[str])
    project_files: list[str] = Field(default_factory=list[str])
    scope_dirs: list[str] = Field(default_factory=list[str])
    configured_standard: str
    max_retry_budget: int = Field(ge=0)
    max_edit_retries: int = Field(ge=0)
    status: HostStatus
    attempt: int = Field(default=1, ge=1)
    edit_rejections: int = Field(default=0, ge=0, description="In the current attempt")
    attempts: list[Attempt] = Field(default_factory=list[Attempt])
    feedback_kind: HostFeedbackKind = HostFeedbackKind.INITIAL
    feedback: str | None = Field(default=None, description="What the next submission must fix")
    sites: dict[str, list[RuleSite]] = Field(default_factory=dict[str, list[RuleSite]])
    codemods: CodemodResult | None = None
    user_tree: DirtyState = Field(
        default_factory=DirtyState,
        description="The user's uncommitted files when the unit started; a change is reported",
    )
    reason: str | None = None
    summary: str | None = None
    review_notes: list[str] = Field(default_factory=list[str])
    warnings: list[str] = Field(default_factory=list[str])
    changed_files: list[str] = Field(default_factory=list[str])
    commit: str | None = None
    agent_skipped: bool = False
    started_at: AwareDatetime
    updated_at: AwareDatetime

    @property
    def attempts_allowed(self) -> int:
        return self.max_retry_budget + 1
