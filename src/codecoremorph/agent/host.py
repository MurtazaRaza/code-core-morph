"""A unit driven by a host agent (D-41, SPEC §7.6.1).

When CodeCoreMorph runs as a tool of a coding agent (Claude Code, through the
``codecoremorph`` skill), that agent is the editor and fixer, and CodeCoreMorph makes no model
calls. Each ``codecoremorph unit`` command takes one step:

- **start:** snapshot → codemods → verification. The unit is done if nothing is left
  (FR-3.4). Otherwise it waits for the host, with a context document to read and an edits
  file to fill.
- **submit:** the edits file → PatchEngine (scope, all or nothing) → verification → one
  commit on the run branch; or feedback for the next attempt; or, with the retry budget
  spent, a rollback. A rejected submission is answered at once and counts against
  ``max_edit_retries``, as with the built-in agent.
- **abort** (``codecoremorph.orchestrator.unit.abort_host_unit``): the worktree and the run
  branch are removed; it needs no sandbox, so it works when Docker is down.

Between commands the worktree is clean: a step that changes files ends with a checkpoint
commit, and success squashes the checkpoints into the unit's commit. A change found in the
worktree when a command starts was made outside CodeCoreMorph: it is discarded and reported,
so every edit still goes through PatchEngine.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from codecoremorph.agent.context import host_document
from codecoremorph.agent.loop import UnitRunner
from codecoremorph.core.errors import UsageError, VcsError
from codecoremorph.core.models import (
    Attempt,
    AttemptOutcome,
    EditBatch,
    HostFeedbackKind,
    HostStatus,
    HostUnitState,
    LoadedRule,
    Role,
)
from codecoremorph.parity.baseline import BaselineCache, unit_baseline
from codecoremorph.vcs.messages import unit_commit_message
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

STATE_FILE = "host.json"
CONTEXT_FILE = "context.md"
EDITS_FILE = "edits.json"
HOST_MODEL = "host"
CHECKPOINT_AUTHOR = "CodeCoreMorph <checkpoint@codecoremorph.invalid>"
_FAILED_STAGES = {
    "configure": AttemptOutcome.BUILD_FAILED,
    "build": AttemptOutcome.BUILD_FAILED,
    "tests": AttemptOutcome.TESTS_FAILED,
}


@dataclass(frozen=True)
class HostFiles:
    """A host unit's files in the run's artifact directory (inside ``.codecoremorph/``, which
    the host may write to without touching the project's files)."""

    root: Path

    @property
    def state(self) -> Path:
        return self.root / STATE_FILE

    @property
    def context(self) -> Path:
        return self.root / CONTEXT_FILE

    @property
    def edits(self) -> Path:
        return self.root / EDITS_FILE

    def load(self) -> HostUnitState:
        return HostUnitState.model_validate_json(self.state.read_bytes())

    def save(self, state: HostUnitState) -> None:
        temporary = self.state.with_name(STATE_FILE + ".tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.state)


@dataclass
class HostStep:
    """One command's result: the new state and what to tell the host."""

    state: HostUnitState
    message: str
    warnings: list[str] = field(default_factory=list[str])


def _problems(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'the file'}: {e['msg']}" for e in exc.errors()[:10]
    )


class HostUnit:
    """Takes a host unit's steps on one run worktree. One instance per command."""

    def __init__(
        self,
        runner: UnitRunner,
        rules: Sequence[LoadedRule],
        files: HostFiles,
        *,
        language: str,
        author: str | None,
        baselines: BaselineCache | None = None,
    ):
        """``baselines`` receives the baseline of the unit's commit when it succeeds, so a unit
        started from that commit reuses its test results (D-42)."""
        self.runner = runner
        self.rules = list(rules)
        self.files = files
        self.language = language
        self.author = author
        self.baselines = baselines

    @property
    def worktree(self) -> Worktree:
        return self.runner.worktree

    # ------------------------------------------------------------------ steps

    def start(self, state: HostUnitState) -> HostStep:
        """Codemods and the first verification; ``state`` is fresh, on the snapshot."""
        if not self.worktree.can_commit(self.author):
            raise VcsError(
                "git has no user identity to commit the unit with",
                hint='Run `git config --global user.name "Your Name"` and '
                "`git config --global user.email you@example.com`, or set git.author in the "
                "manifest.",
            )
        self.files.edits.unlink(missing_ok=True)
        unit = state.unit
        ctx = self.runner.verify_context(self.rules)
        try:
            self.runner.begin(unit, self.rules)
            codemods = self.runner.codemods(unit, self.rules)
            self.runner.agent_base = self.worktree.write_tree()
            check = self.runner.verify(unit, self.rules, ctx)
        except BaseException:
            self.worktree.rollback(state.snapshot)
            raise
        state = self._update(
            state,
            codemods=codemods,
            sites=check.sites,
            configured_standard=self.runner.configured_standard,
            error_lines=self.runner.error_lines,
            agent_base=self.runner.agent_base,
        )
        if self.runner.nothing_left(check, self.rules):
            state = self._update(
                state,
                agent_skipped=True,
                warnings=check.warnings,
                approvals=check.approvals,
            )
            return self._succeed(state, "Deterministic tools completed the unit.", [])
        state = self._checkpoint(state, "codemods")
        build = None if check.stage in ("postconditions", "ok") else check.feedback
        state = self._update(state, feedback=build, feedback_kind=HostFeedbackKind.INITIAL)
        return self._wait(state, "The unit needs your edits.")

    def submit(self, state: HostUnitState) -> HostStep:
        """Apply the edits file, verify, and move to the next attempt or the end."""
        self._require_waiting(state)
        if not self.files.edits.is_file():
            raise UsageError(
                f"there is no edits file at {self.files.edits}",
                hint="Write the unit's edits there (the format is in the context document), "
                "then run `codecoremorph unit submit` again.",
            )
        warnings = self.discard_outside_changes(state)
        store = self.runner.artifacts.attempt(state.unit.id, state.attempt).ensure()
        raw = self.files.edits.read_bytes()
        kept = store.root / f"edits-{state.edit_rejections + 1}.json"
        kept.write_bytes(raw)
        self.files.edits.unlink()
        try:
            batch = EditBatch.model_validate_json(raw)
        except ValidationError as exc:
            problems = f"The edits file doesn't match the format: {_problems(exc)}"
            return self._reject(state, problems, warnings)
        unit = state.unit
        try:
            rejection = self.runner.apply_edits(batch, unit, self.rules)
            if rejection is not None:
                return self._reject(state, rejection, warnings)
            ctx = self.runner.verify_context(self.rules)
            verification = self.runner.verify(unit, self.rules, ctx)
        except BaseException:
            # The step didn't finish (the sandbox failed, or the user interrupted it): back to
            # the last checkpoint, with the edits file restored, so `submit` can simply rerun.
            self.worktree.rollback(state.head)
            self.files.edits.write_bytes(raw)
            raise
        started = state.updated_at
        self.runner.save_logs(store)
        diff = self.worktree.diff(state.head)
        store.diff_patch.write_text(diff.patch, encoding="utf-8")
        record = Attempt(
            run_id=state.run_id,
            unit_id=unit.id,
            number=state.attempt,
            role=Role.EDITOR if state.attempt == 1 else Role.FIXER,
            model=HOST_MODEL,
            started_at=started,
            finished_at=datetime.now(UTC),
            outcome=AttemptOutcome.SUCCEEDED
            if verification.ok
            else _FAILED_STAGES.get(verification.stage, AttemptOutcome.INVARIANT_FAILED),
            changed_files=[f.path for f in diff.files],
            diff_lines=sum((f.added_lines or 0) + (f.removed_lines or 0) for f in diff.files),
            diagnostics=verification.diagnostics[:50],
            regressions=verification.regressions,
            invariant_results=verification.invariant_results,
            summary=batch.summary,
            review_notes=list(batch.review_notes),
        )
        state = self._update(
            state,
            attempts=[*state.attempts, record],
            sites=verification.sites,
            configured_standard=self.runner.configured_standard,
            error_lines=self.runner.error_lines,
        )
        if verification.ok:
            state = self._update(
                state, warnings=verification.warnings, approvals=verification.approvals
            )
            step = self._succeed(state, batch.summary, list(batch.review_notes))
            step.warnings[:0] = warnings
            return step
        store.feedback_md.write_text(verification.feedback, encoding="utf-8")
        step = self._next_attempt(state, verification.feedback)
        step.warnings[:0] = warnings
        return step

    def context(self, state: HostUnitState) -> str:
        """The context document of the current attempt (written to ``context.md`` as well)."""
        unit = state.unit
        if state.feedback_kind is HostFeedbackKind.INITIAL:
            status = (
                "Deterministic tools ran first; the unit's rules still need your edits."
                if state.feedback is None
                else "Deterministic tools ran first. The project doesn't build at the moment; "
                "see the current build below."
            )
            build = state.feedback
        elif state.feedback_kind is HostFeedbackKind.REJECTED:
            status = (
                f"Your last submission was rejected and nothing was applied "
                f"({state.edit_rejections} of {state.max_edit_retries + 1} tries of this attempt "
                f"used):\n\n{state.feedback or ''}"
            )
            build = "Unchanged: nothing from the rejected submission was applied."
        else:
            status = (
                f"Attempt {state.attempt - 1} was applied and failed; the files below contain "
                f"its edits. Fix the problems and submit the additional edits.\n\n"
                f"{state.feedback or ''}"
            )
            build = f"See the result of attempt {state.attempt - 1} at the top of this document."
        text = host_document(
            unit,
            self.rules,
            worktree=self.worktree.path,
            index_path=Path(state.index_path) if state.index_path else None,
            language=self.language,
            source=state.source,
            target=state.target,
            codemods=state.codemods,
            remaining=state.sites,
            build=build,
            attempt=state.attempt,
            attempts=state.attempts_allowed,
            status=status,
            edits_file=str(self.files.edits),
            uncompiled=state.uncompiled,
        )
        return text + "\n"

    def discard_outside_changes(self, state: HostUnitState) -> list[str]:
        """Discard changes and commits in the worktree that CodeCoreMorph didn't make."""
        moved = self.worktree.head() != state.head
        if not moved and not self.worktree.has_changes():
            return []
        changed = [f.path for f in self.worktree.diff(state.head).files]
        self.worktree.rollback(state.head)
        what = ", ".join(changed[:20]) or "commits"
        return [
            f"changes in the run worktree that didn't come through CodeCoreMorph were discarded "
            f"({what}); put every edit in the edits file"
        ]

    # ------------------------------------------------------------------ transitions

    def _reject(
        self, state: HostUnitState, feedback: str, warnings: list[str] | None = None
    ) -> HostStep:
        rejections = state.edit_rejections + 1
        if rejections <= state.max_edit_retries:
            state = self._update(
                state,
                edit_rejections=rejections,
                feedback=feedback,
                feedback_kind=HostFeedbackKind.REJECTED,
            )
            step = self._wait(state, "Your submission was rejected; nothing was applied.")
            step.warnings[:0] = warnings or []
            return step
        record = Attempt(
            run_id=state.run_id,
            unit_id=state.unit.id,
            number=state.attempt,
            role=Role.EDITOR if state.attempt == 1 else Role.FIXER,
            model=HOST_MODEL,
            started_at=state.updated_at,
            finished_at=datetime.now(UTC),
            outcome=AttemptOutcome.EDITS_REJECTED,
            summary=feedback[:2000],
        )
        state = self._update(state, attempts=[*state.attempts, record])
        store = self.runner.artifacts.attempt(state.unit.id, state.attempt).ensure()
        store.feedback_md.write_text(feedback, encoding="utf-8")
        step = self._next_attempt(state, feedback)
        step.warnings[:0] = warnings or []
        return step

    def _next_attempt(self, state: HostUnitState, feedback: str) -> HostStep:
        if state.attempt >= state.attempts_allowed:
            outcome = state.attempts[-1].outcome if state.attempts else None
            reason = (
                f"the retry budget ({state.max_retry_budget}) ran out; the last attempt ended "
                f"with {outcome}"
            )
            return self._fail(state, reason, feedback)
        state = self._checkpoint(state, f"attempt {state.attempt}")
        state = self._update(
            state,
            attempt=state.attempt + 1,
            edit_rejections=0,
            feedback=feedback,
            feedback_kind=HostFeedbackKind.FAILED,
        )
        return self._wait(state, f"Attempt {state.attempt - 1} failed.")

    def _wait(self, state: HostUnitState, headline: str) -> HostStep:
        state = self._update(state, status=HostStatus.WAITING)
        text = self.context(state)
        self.files.context.write_text(text, encoding="utf-8")
        store = self.runner.artifacts.attempt(state.unit.id, state.attempt).ensure()
        (store.root / CONTEXT_FILE).write_text(text, encoding="utf-8")
        self.files.save(state)
        lines = [headline]
        if state.feedback and state.feedback_kind is not HostFeedbackKind.INITIAL:
            lines += ["", state.feedback.strip(), ""]
        lines.append(
            f"Attempt {state.attempt} of {state.attempts_allowed}. "
            f"The full context with the files' current text is in {self.files.context}. "
            f"Write the edits to {self.files.edits}, then run `codecoremorph unit submit`."
        )
        return HostStep(state, "\n".join(lines))

    def _succeed(self, state: HostUnitState, summary: str, notes: list[str]) -> HostStep:
        self.worktree.reset_soft(state.snapshot)
        commit: str | None = None
        changed: list[str] = []
        if self.worktree.has_changes():
            changed = [f.path for f in self.worktree.diff(state.snapshot).files]
            by_tools = [c.file for c in state.codemods.changes] if state.codemods else []
            message = unit_commit_message(
                run_id=state.run_id,
                unit_id=state.unit.id,
                rules=state.unit.rules,
                files=changed,
                summary=summary,
                attempts=len(state.attempts),
                models=[HOST_MODEL] if state.attempts else [],
                codemod_files=[f for f in by_tools if f in changed],
            )
            commit = self.worktree.commit(message, author=self.author)
            self._store_baseline(state, commit)
        state = self._update(
            state,
            status=HostStatus.SUCCEEDED,
            head=commit or state.snapshot,
            commit=commit,
            changed_files=changed,
            summary=summary,
            review_notes=notes,
            feedback=None,
        )
        self.files.save(state)
        if commit is None:
            text = f"Unit {state.unit.id} needed no changes."
        else:
            text = (
                f"Unit {state.unit.id} succeeded: commit {commit[:12]} on branch "
                f"{self.worktree.branch} changes {len(changed)} file(s)."
            )
        # A single unit has no gate: interface changes a run would ask about are for review.
        review = [f"needs a human's review: {a}" for a in state.approvals]
        return HostStep(state, text, [*state.warnings, *review])

    def _fail(self, state: HostUnitState, reason: str, feedback: str) -> HostStep:
        self.worktree.rollback(state.snapshot)
        state = self._update(
            state, status=HostStatus.FAILED, reason=reason, head=state.snapshot, feedback=feedback
        )
        self.files.save(state)
        return HostStep(
            state,
            f"Unit {state.unit.id} failed: {reason}. It was rolled back.\n\n"
            f"The last feedback:\n\n{feedback.strip()}",
        )

    def _store_baseline(self, state: HostUnitState, commit: str) -> None:
        """The unit's final verification ran the tests on ``commit``: keep them as that
        commit's baseline for the next unit."""
        report = self.runner.last_tests
        if self.baselines is None or report is None:
            return
        lang = self.runner.verify_context(self.rules).lang
        baseline = unit_baseline(
            self.runner.baseline,
            report,
            commit=commit,
            standard=lang.standard,
            run_id=state.run_id,
        )
        self.baselines.put(baseline, sandbox=state.sandbox, lang=lang, run_id=state.run_id)

    def _checkpoint(self, state: HostUnitState, what: str) -> HostUnitState:
        if not self.worktree.has_changes():
            return state
        sha = self.worktree.commit(f"codecoremorph: checkpoint ({what})", author=CHECKPOINT_AUTHOR)
        return self._update(state, head=sha)

    @staticmethod
    def _require_waiting(state: HostUnitState) -> None:
        if state.status is not HostStatus.WAITING:
            raise UsageError(
                f"unit {state.unit.id} of run {state.run_id} has already ended: {state.status}",
                hint="Start a new unit with `codecoremorph unit start`.",
            )

    @staticmethod
    def _update(state: HostUnitState, **changes: object) -> HostUnitState:
        changes["updated_at"] = datetime.now(UTC)
        return HostUnitState.model_validate({**state.model_dump(), **changes})
