"""One work unit, end to end (FR-3.4, FR-4.1 to FR-4.6, SPEC §7.5 unit subgraph, §7.6).

snapshot → codemods → [sites left or verification failing?] → agent attempts → result

- Codemods run first; if the unit then builds, passes the baseline tests and has no rule
  sites left, the agent is skipped (FR-3.4).
- An attempt is one agent turn sequence ending in ``submit_edits``: read-only tool calls,
  then edits applied through PatchEngine. Rejected edits get immediate feedback under their
  own small budget (``max_edit_retries``), without a build (FR-4.6).
- Verification: configure when needed, build, run the tests and compare with the baseline,
  then the rules' ``detector_empty`` post-conditions. A failure becomes feedback for the next
  attempt, by the ``fixer`` role, in the same conversation (append-only). A model switch
  (escalation) starts a new conversation seeded with the last feedback.
- When the retry budget runs out, the worktree rolls back to the snapshot (NFR-2.2).

A host agent (D-41) drives the same steps from outside, one command at a time:
``codemods``, ``verify`` and ``apply_edits`` are public for ``codecoremorph.agent.host``.

With an :class:`InvariantChecker`, a green verification also runs the safety invariants
(FR-4.4): a violation is feedback for the next attempt. A ``guard`` (the run's abort check) is
called at safe points, and a ``deadline`` ends the unit when its time runs out (FR-14.1).
Committing and the unit gate belong to the run graph (``codecoremorph.orchestrator.run``).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.agent.context import UnitContext, build_context, read_text
from codecoremorph.agent.feedback import compiler_feedback, regression_feedback, sites_feedback
from codecoremorph.agent.tools import ToolBox
from codecoremorph.agent.unverified import (
    echo_feedback,
    echoes,
    error_lines,
    unverified_warnings,
)
from codecoremorph.config.models import GuardrailsConfig
from codecoremorph.core.errors import ContextOverflowError, LLMError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    Attempt,
    AttemptOutcome,
    Baseline,
    BuildResult,
    CodemodResult,
    CompletionRequest,
    ContentBlock,
    DetectorType,
    Diagnostic,
    EditBatch,
    InvariantResult,
    LoadedRule,
    Message,
    PostconditionType,
    Role,
    RuleKind,
    RuleSite,
    StopReason,
    TestReport,
    TestStatus,
    TextBlock,
    ToolResult,
    WorkUnit,
    owned_files,
)
from codecoremorph.core.secrets import Json, Redactor
from codecoremorph.invariants.checker import InvariantChecker
from codecoremorph.llm.routing import Route
from codecoremorph.llm.service import LLMService
from codecoremorph.patching.engine import PatchEngine
from codecoremorph.patching.schema import SUBMIT_EDITS_TOOL
from codecoremorph.planner.estimate import unit_token_limit
from codecoremorph.planner.sites import find_sites, load_hook
from codecoremorph.prompts import PROMPTS_VERSION, render
from codecoremorph.rules.applicability import StandardOrder, codemod_applies
from codecoremorph.store.layout import AttemptArtifacts, RunArtifacts
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

MAX_CUTOFFS = 2

Guard = Callable[[], None]


class UnitTimedOut(Exception):  # noqa: N818 - a signal inside the loop, not an error report
    """The unit's wall-clock limit ran out (``guardrails.max_unit_minutes``)."""


@dataclass(frozen=True)
class AgentSettings:
    max_retry_budget: int = 3
    max_edit_retries: int = 2
    max_tool_calls: int = 30
    max_output_tokens: int = 64_000
    max_attempt_tokens: int | None = None
    store_transcripts: bool = True

    @classmethod
    def from_config(cls, guardrails: GuardrailsConfig, *, store_transcripts: bool) -> AgentSettings:
        return cls(
            max_retry_budget=guardrails.max_retry_budget,
            max_edit_retries=guardrails.max_edit_retries,
            store_transcripts=store_transcripts,
        )


@dataclass
class Verification:
    ok: bool
    stage: str
    feedback: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])
    regressions: list[str] = field(default_factory=list[str])
    sites: dict[str, list[RuleSite]] = field(default_factory=dict[str, list[RuleSite]])
    warnings: list[str] = field(default_factory=list[str])
    invariant_results: list[InvariantResult] = field(default_factory=list[InvariantResult])
    approvals: list[str] = field(default_factory=list[str])  # why the unit gate must approve
    # Failed rule checks the unit's context doesn't list as sites: a codemod_clean diff, a
    # hook's sites.
    untold: str = ""


def first_build(check: Verification) -> str | None:
    """The "current build" of a unit's first context, after the codemods: None when the unit
    builds, passes its tests and has only rule sites left (the context lists those); the
    failures of the rules' other checks; or the failed build, tests or invariants."""
    if check.stage == "ok":
        return None
    if check.stage == "postconditions":
        if not check.untold:
            return None
        return (
            f"The project builds and its tests pass, but the rules' checks fail:\n\n{check.untold}"
        )
    return check.feedback


@dataclass
class UnitOutcome:
    unit_id: str
    succeeded: bool
    agent_skipped: bool = False
    timed_out: bool = False
    attempts: list[Attempt] = field(default_factory=list[Attempt])
    changed_files: list[str] = field(default_factory=list[str])
    diff: str = ""
    reason: str | None = None
    summary: str | None = None
    review_notes: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])
    approvals: list[str] = field(default_factory=list[str])
    invariant_results: list[InvariantResult] = field(default_factory=list[InvariantResult])
    codemods: CodemodResult | None = None
    prompts_version: str = PROMPTS_VERSION


@dataclass
class _Turns:
    """The conversation: append-only messages plus the next user message being built."""

    messages: list[Message]
    pending: list[ContentBlock] = field(default_factory=list[ContentBlock])

    def flush(self) -> Message | None:
        if not self.pending:
            return None
        # Tool results lead a user message; text follows them.
        ordered = [b for b in self.pending if isinstance(b, ToolResult)] + [
            b for b in self.pending if not isinstance(b, ToolResult)
        ]
        message = Message(role="user", content=ordered)
        self.messages.append(message)
        self.pending = []
        return message


@dataclass
class _AttemptEnd:
    outcome: AttemptOutcome
    batch: EditBatch | None = None
    detail: str | None = None


class UnitRunner:
    """Runs units in one run worktree. Not thread-safe."""

    def __init__(
        self,
        *,
        adapter: LanguageAdapter,
        ctx: AdapterContext,
        worktree: Worktree,
        baseline: Baseline,
        llm: LLMService | None,
        order: StandardOrder,
        source: str,
        target: str,
        run_id: str,
        artifacts: RunArtifacts,
        index_path: Path | None,
        build_files: Sequence[str] = (),
        project_files: Sequence[str] = (),
        scope_dirs: Sequence[str] = (".",),
        settings: AgentSettings | None = None,
        uncompiled: Sequence[str] = (),
        invariants: InvariantChecker | None = None,
        guard: Guard | None = None,
        deadline: float | None = None,
    ):
        """``project_files`` are the project's editable code files: a project-wide rule (the
        standard bump) runs its codemods on all of them, not only on the unit's files.
        ``uncompiled`` are in-scope code files no compile command reaches: the build can't
        check them, so their changes and the build errors they repeat are reported (D-42).
        ``llm`` may be None when a host agent makes the edits (``run`` needs it).
        ``invariants`` checks green attempts (FR-4.4); ``guard`` raises to stop the run (an
        abort) and is called before every attempt and model call; ``deadline`` is a
        ``time.monotonic()`` value after which the unit ends as timed out."""
        self.adapter = adapter
        self.ctx = ctx
        self.worktree = worktree
        self.baseline = baseline
        self.llm = llm
        self.order = order
        self.source = source
        self.target = target
        self.run_id = run_id
        self.artifacts = artifacts
        self.index_path = index_path
        self.build_files = set(build_files)
        self.project_files = list(project_files)
        everything = "." in scope_dirs
        self._scope = None if everything else PathMatcher([d for d in scope_dirs if d != "."])
        self.settings = settings or AgentSettings()
        self.configured_standard = baseline.standard
        self.uncompiled = list(uncompiled)
        # Source lines that failed to compile during the unit, to find their echoes in the
        # uncompiled files; a host unit keeps them in its state between commands.
        self.error_lines: list[str] = []
        self.last_tests: TestReport | None = None  # the last verification's test run
        self.last_sites: dict[str, list[RuleSite]] = {}  # and the rule sites it found
        self.invariants = invariants
        self.guard = guard
        self.deadline = deadline
        # The tree after the codemods: the invariants measure the agent's own diff from it.
        self.agent_base: str | None = None
        self._engine = PatchEngine(worktree.path, exclude=ctx.lang.exclude)

    def checkpoint(self) -> None:
        """A safe point: stop if the run is stopping (the guard raises) or time is up."""
        if self.guard is not None:
            self.guard()
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise UnitTimedOut

    @staticmethod
    def project_rule(rules: Sequence[LoadedRule]) -> bool:
        return any(r.rule.kind is RuleKind.PROJECT for r in rules)

    def begin(self, unit: WorkUnit, rules: Sequence[LoadedRule]) -> None:
        """Before the codemods: what the invariants compare the unit's attempts against."""
        if self.invariants is not None:
            self.invariants.begin(
                self.ctx,
                self.worktree,
                unit,
                self.index_path,
                project_rule=self.project_rule(rules),
            )

    # ------------------------------------------------------------------ the unit

    def verify_context(self, rules: Sequence[LoadedRule]) -> AdapterContext:
        """The context to verify with: a standard bump builds at the target standard."""
        if not any(r.rule.applies_to.standard_bump for r in rules):
            return self.ctx
        return replace(self.ctx, lang=self.ctx.lang.model_copy(update={"standard": self.target}))

    @staticmethod
    def nothing_left(check: Verification, rules: Sequence[LoadedRule]) -> bool:
        """The agent is skipped only when nothing is left: green, no rule sites at all
        (warning-level ones included), and no rule that is LLM guidance alone (FR-3.4)."""
        return (
            check.ok
            and not any(check.sites.values())
            and not any(r.rule.natural_language_only for r in rules)
        )

    def run(self, unit: WorkUnit, rules: Sequence[LoadedRule]) -> UnitOutcome:
        snapshot = self.worktree.snapshot()
        verify_ctx = self.verify_context(rules)
        outcome = UnitOutcome(unit_id=unit.id, succeeded=False)
        try:
            self.checkpoint()
            self.begin(unit, rules)
            outcome.codemods = self.codemods(unit, rules)
            self.agent_base = self.worktree.write_tree()
            check = self.verify(unit, rules, verify_ctx)
            if self.nothing_left(check, rules):
                outcome.succeeded = True
                outcome.agent_skipped = True
                outcome.warnings = check.warnings
                outcome.approvals = check.approvals
                outcome.invariant_results = check.invariant_results
                return self._finish(outcome, snapshot)
            self._agent(unit, rules, verify_ctx, outcome, check)
        except UnitTimedOut:
            outcome.succeeded = False
            outcome.timed_out = True
            outcome.reason = "the unit's time limit (guardrails.max_unit_minutes) ran out"
        except BaseException:
            self.worktree.rollback(snapshot)
            raise
        return self._finish(outcome, snapshot)

    def _finish(self, outcome: UnitOutcome, snapshot: str) -> UnitOutcome:
        if outcome.succeeded:
            diff = self.worktree.diff(snapshot)
            outcome.diff = diff.patch
            outcome.changed_files = [f.path for f in diff.files]
        else:
            self.worktree.rollback(snapshot)
        return outcome

    def codemods(self, unit: WorkUnit, rules: Sequence[LoadedRule]) -> CodemodResult | None:
        specs = [
            spec
            for loaded in rules
            for spec in loaded.rule.codemods
            if codemod_applies(self.order, spec, self.target)
        ]
        if not specs:
            return None
        files = owned_files(unit)
        if any(r.rule.kind is RuleKind.PROJECT for r in rules):
            files += [f for f in self.project_files if f not in unit.files]
        return self.adapter.run_codemods(self.ctx, specs, files)

    # ------------------------------------------------------------------ verification

    def verify(
        self, unit: WorkUnit, rules: Sequence[LoadedRule], ctx: AdapterContext
    ) -> Verification:
        changed = {f.path for f in self.worktree.diff(self.worktree.head()).files}
        if ctx.lang.standard != self.configured_standard or changed & self.build_files:
            configured = self.adapter.configure(ctx)
            if not configured.success:
                return self._build_failure(configured, unit, "configure", changed, {})
            self.configured_standard = ctx.lang.standard
        # Sites first: the agent sees what is left even while the build is broken.
        sites = {
            rule_id: [s for file_sites in by_file.values() for s in file_sites]
            for rule_id, by_file in self._sites(unit, rules, ctx).items()
        }
        self.last_sites = sites
        built = self.adapter.build(ctx)
        if not built.success:
            return self._build_failure(built, unit, "build", changed, sites)
        report = self.adapter.run_tests(ctx)
        self.last_tests = report
        statuses = {c.test_id: c for c in report.cases}
        failing = [
            statuses[t]
            for t in self.baseline.tests.passing
            if t in statuses and statuses[t].status is not TestStatus.PASSED
        ]
        missing = [t for t in self.baseline.tests.passing if t not in statuses]
        if failing or missing:
            regressions = [c.test_id for c in failing] + missing
            return Verification(
                ok=False,
                stage="tests",
                feedback=regression_feedback(failing, missing=missing, roots=self._roots(ctx)),
                regressions=regressions,
                sites=sites,
                invariant_results=[
                    InvariantResult(
                        invariant="I-5",
                        passed=False,
                        message=f"{len(regressions)} baseline-passing test(s) don't pass",
                    )
                ],
            )
        problems, warnings, untold = self.postconditions(unit, rules, sites)
        if problems:
            return Verification(
                ok=False,
                stage="postconditions",
                feedback="\n\n".join(problems),
                untold="\n\n".join(untold),
                sites=sites,
                invariant_results=[
                    InvariantResult(invariant="I-9", passed=False, message="sites left")
                ],
            )
        warnings += self.unverified(unit, rules, ctx)
        results = [
            InvariantResult(invariant="I-5", passed=True),
            InvariantResult(invariant="I-9", passed=True),
        ]
        if self.invariants is None:
            return Verification(
                ok=True, stage="ok", sites=sites, warnings=warnings, invariant_results=results
            )
        checked = self.invariants.check(
            ctx=ctx,
            worktree=self.worktree,
            unit=unit,
            rules=rules,
            allowed=lambda path: self.allowed(path, unit, rules),
            baseline=self.baseline.tests,
            built=built,
            report=report,
            agent_since=self.agent_base,
        )
        results += checked.results
        if not checked.ok:
            return Verification(
                ok=False,
                stage="invariants",
                feedback=checked.feedback(),
                sites=sites,
                invariant_results=results,
            )
        return Verification(
            ok=True,
            stage="ok",
            sites=sites,
            warnings=warnings + checked.warnings,
            invariant_results=results,
            approvals=checked.approvals,
        )

    def postconditions(
        self,
        unit: WorkUnit,
        rules: Sequence[LoadedRule],
        sites: dict[str, list[RuleSite]],
    ) -> tuple[list[str], list[str], list[str]]:
        """The rules' post-conditions on a green build (I-9, FR-4.4): feedback for failed
        ``error`` ones, warnings for failed ``warning`` ones, and the feedback of the failed
        ``error`` ones other than ``detector_empty``, whose sites a unit's context lists.

        - ``detector_empty``: the rule's detector finds no site in the unit's own files.
        - ``builds_cleanly``: holds, because the unit just built at the standard it targets.
        - ``codemod_clean``: running the rule's codemods again changes nothing and reports no
          site; whatever they would change is put back and fed back to the agent.
        - ``hook``: ``module:function(worktree, files)`` returns no site."""
        problems: list[str] = []
        warnings: list[str] = []
        untold: list[str] = []
        for loaded in rules:
            rule_id = loaded.rule.id
            for post in loaded.rule.postconditions:
                if post.type is PostconditionType.DETECTOR_EMPTY:
                    found = sites.get(rule_id, [])
                    text = sites_feedback(rule_id, found) if found else ""
                elif post.type is PostconditionType.CODEMOD_CLEAN:
                    text = self._codemod_leftovers(unit, loaded)
                    found = [RuleSite(file="-", line=1)] if text else []
                elif post.type is PostconditionType.HOOK and post.hook is not None:
                    found = list(load_hook(post.hook)(self.worktree.path, owned_files(unit)))
                    text = sites_feedback(rule_id, found) if found else ""
                else:  # BUILDS_CLEANLY
                    continue
                if not found:
                    continue
                if post.severity == "error":
                    problems.append(text)
                    if post.type is not PostconditionType.DETECTOR_EMPTY:
                        untold.append(text)
                elif post.type is PostconditionType.CODEMOD_CLEAN:
                    warnings.append(f"{rule_id}: the rule's tools still find work (a warning)")
                else:
                    warnings.append(f"{rule_id}: {len(found)} site(s) left (a warning)")
        return problems, warnings, untold

    def _codemod_leftovers(self, unit: WorkUnit, loaded: LoadedRule) -> str:
        specs = [
            spec for spec in loaded.rule.codemods if codemod_applies(self.order, spec, self.target)
        ]
        if not specs:
            return ""
        files = owned_files(unit)
        before = self.worktree.write_tree()
        result = self.adapter.run_codemods(self.ctx, specs, files)
        diff = self.worktree.diff(before)
        if diff.files:
            self.worktree.restore_tree(before)
        remaining = [d for d in result.remaining if d.file_path in files]
        if not diff.files and not remaining:
            return ""
        lines = [
            f"{loaded.rule.id}: the rule's tools still find work in the unit's files. Make "
            "these changes yourself (they were not applied):"
        ]
        if diff.files:
            patch = diff.patch.splitlines()
            lines += ["", "```diff", *patch[:80], "```"]
            if len(patch) > 80:
                lines.append(f"[{len(patch) - 80} more diff lines]")
        for d in remaining[:20]:
            lines.append(f"- {d.file_path}:{d.line}: {d.message}")
        return "\n".join(lines)

    def unverified(
        self, unit: WorkUnit, rules: Sequence[LoadedRule], ctx: AdapterContext
    ) -> list[str]:
        """What a green verification couldn't check: changes to uncompiled files, build errors
        those files still repeat, and a project rule's sites in them (D-42)."""
        if not self.uncompiled:
            return []
        # Every change of the unit, checkpoints included: the run began at the base commit.
        changed = {f.path for f in self.worktree.diff(self.baseline.base_commit).files}
        warnings = unverified_warnings(
            changed, self.uncompiled, echoes(self.error_lines, self.uncompiled, self.worktree.path)
        )
        project = [r for r in rules if r.rule.kind is RuleKind.PROJECT]
        if project:
            pseudo = unit.model_copy(update={"files": self.uncompiled, "reference_files": []})
            for rule_id, by_file in self._sites(pseudo, project, ctx).items():
                found = [s for file_sites in by_file.values() for s in file_sites]
                if found:
                    shown = ", ".join(f"{s.file}:{s.line}" for s in found[:10])
                    warnings.append(
                        f"{rule_id}: files this build doesn't compile still have {len(found)} "
                        f"site(s) the target standard breaks: {shown}"
                    )
        return warnings

    def _build_failure(
        self,
        result: BuildResult,
        unit: WorkUnit,
        step: str,
        changed: set[str],
        sites: dict[str, list[RuleSite]],
    ) -> Verification:
        feedback = compiler_feedback(
            result.diagnostics,
            unit_files=unit.files,
            worktree=self.worktree.path,
            changed_files=sorted(changed),
            step=step,
            roots=self._roots(self.ctx),
        )
        if self.uncompiled:
            for line in error_lines(result.errors, self.worktree.path):
                if line not in self.error_lines:
                    self.error_lines.append(line)
            found = echoes(self.error_lines, self.uncompiled, self.worktree.path)
            if found:
                feedback = f"{feedback.rstrip()}\n\n{echo_feedback(found)}"
        return Verification(
            ok=False, stage=step, feedback=feedback, diagnostics=result.errors, sites=sites
        )

    def _roots(self, ctx: AdapterContext) -> list[str]:
        """The worktree as the host and the sandbox spell it, to make paths relative."""
        return [str(self.worktree.path), ctx.sandbox(ctx.worktree)]

    def _sites(
        self, unit: WorkUnit, rules: Sequence[LoadedRule], ctx: AdapterContext
    ) -> dict[str, dict[str, list[RuleSite]]]:
        if any(
            r.rule.detector is not None and r.rule.detector.type is DetectorType.ADAPTER_QUERY
            for r in rules
        ):
            self.adapter.index(ctx)  # queries read the index: bring it up to date first

        def query(name: str, files: list[str]) -> list[RuleSite]:
            return self.adapter.query(ctx, name, files)

        return {
            loaded.rule.id: find_sites(
                loaded,
                owned_files(unit),
                worktree=self.worktree.path,
                read=lambda path: read_text(self.worktree.path, path),
                order=self.order,
                source=self.source,
                target=self.target,
                query=query,
            )
            for loaded in rules
        }

    # ------------------------------------------------------------------ the agent

    def allowed(self, path: str, unit: WorkUnit, rules: Sequence[LoadedRule]) -> bool:
        if path in unit.files:
            return True
        project_rule = any(r.rule.kind is RuleKind.PROJECT for r in rules)
        if not project_rule:
            return False
        return path in self.build_files or self._scope is None or self._scope.matches(path)

    def _agent(
        self,
        unit: WorkUnit,
        rules: Sequence[LoadedRule],
        ctx: AdapterContext,
        outcome: UnitOutcome,
        check: Verification,
    ) -> None:
        if self.llm is None:
            raise ValueError("UnitRunner.run needs an LLMService; a host agent uses the host flow")
        llm = self.llm
        toolbox = ToolBox(self.worktree.path, self.index_path)
        editor = llm.route(Role.EDITOR)
        budget = unit_token_limit(llm.capabilities(editor).context_window)
        try:
            context = build_context(
                unit,
                rules,
                worktree=self.worktree.path,
                index_path=self.index_path,
                language=ctx.lang.language,
                source=self.source,
                target=self.target,
                codemods=outcome.codemods,
                remaining=check.sites,
                build=first_build(check),
                tools=toolbox.specs,
                token_budget=budget,
            )
        except ContextOverflowError as exc:
            outcome.reason = str(exc)
            return
        turns = _Turns([context.first_message])
        route: Route | None = None
        feedback = check.feedback
        for number in range(1, self.settings.max_retry_budget + 2):
            role = Role.EDITOR if number == 1 else Role.FIXER
            new_route = llm.route(role, failed_attempts=number - 1)
            if route is not None and new_route.name != route.name:
                # A new model can't read the old conversation's thinking: start over, with the
                # unit context and the last feedback.
                turns = _Turns([context.first_message])
                turns.pending.append(TextBlock(text=render("new_conversation", feedback=feedback)))
            elif number > 1:
                turns.pending.append(
                    TextBlock(text=render("retry", attempt=str(number - 1), feedback=feedback))
                )
            route = new_route
            started = datetime.now(UTC)
            store = self.artifacts.attempt(unit.id, number).ensure()
            try:
                self.checkpoint()
                end = self._attempt(llm, number, route, unit, rules, context, turns, toolbox, store)
            except UnitTimedOut:
                outcome.attempts.append(
                    Attempt(
                        run_id=self.run_id,
                        unit_id=unit.id,
                        number=number,
                        role=role,
                        model=route.name,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                        outcome=AttemptOutcome.TIMED_OUT,
                    )
                )
                raise
            attempt = Attempt(
                run_id=self.run_id,
                unit_id=unit.id,
                number=number,
                role=role,
                model=route.name,
                started_at=started,
                outcome=end.outcome,
                summary=end.batch.summary if end.batch else end.detail,
                review_notes=list(end.batch.review_notes) if end.batch else [],
            )
            if end.batch is None:
                outcome.attempts.append(
                    attempt.model_copy(update={"finished_at": datetime.now(UTC)})
                )
                if end.outcome in (AttemptOutcome.REFUSED, AttemptOutcome.ERROR):
                    outcome.reason = end.detail
                    return
                feedback = end.detail or "The attempt ended without edits."
                continue
            verification = self.verify(unit, rules, ctx)
            self.save_logs(store)
            diff = self.worktree.diff(self.worktree.head())
            attempt = attempt.model_copy(
                update={
                    "finished_at": datetime.now(UTC),
                    "changed_files": [f.path for f in diff.files],
                    "diff_lines": sum(
                        (f.added_lines or 0) + (f.removed_lines or 0) for f in diff.files
                    ),
                    "diagnostics": verification.diagnostics[:50],
                    "regressions": verification.regressions,
                    "invariant_results": verification.invariant_results,
                }
            )
            store.diff_patch.write_text(diff.patch, encoding="utf-8")
            if verification.ok:
                outcome.attempts.append(
                    attempt.model_copy(update={"outcome": AttemptOutcome.SUCCEEDED})
                )
                outcome.succeeded = True
                outcome.summary = end.batch.summary
                outcome.review_notes = list(end.batch.review_notes)
                outcome.warnings = verification.warnings
                outcome.approvals = verification.approvals
                outcome.invariant_results = verification.invariant_results
                return
            failed = {
                "configure": AttemptOutcome.BUILD_FAILED,
                "build": AttemptOutcome.BUILD_FAILED,
                "tests": AttemptOutcome.TESTS_FAILED,
            }.get(verification.stage, AttemptOutcome.INVARIANT_FAILED)
            outcome.attempts.append(attempt.model_copy(update={"outcome": failed}))
            feedback = verification.feedback
            store.feedback_md.write_text(feedback, encoding="utf-8")
        outcome.reason = (
            f"the retry budget ({self.settings.max_retry_budget}) ran out; the last attempt "
            f"ended with {outcome.attempts[-1].outcome}"
            if outcome.attempts
            else "no attempt ran"
        )

    def _attempt(
        self,
        llm: LLMService,
        number: int,
        route: Route,
        unit: WorkUnit,
        rules: Sequence[LoadedRule],
        context: UnitContext,
        turns: _Turns,
        toolbox: ToolBox,
        store: AttemptArtifacts,
    ) -> _AttemptEnd:
        settings = self.settings
        tool_calls = 0
        edit_failures = 0
        cutoffs = 0
        reminded = False
        tokens = 0
        for message in turns.messages:  # the conversation so far, for this attempt's record
            self._log(store, message)
        while True:
            self.checkpoint()
            flushed = turns.flush()
            if flushed is not None:
                self._log(store, flushed)
            request = CompletionRequest(
                model=route.model,
                system=context.system,
                messages=list(turns.messages),
                tools=toolbox.specs,
                max_output_tokens=settings.max_output_tokens,
                provider_options=route.options,
            )
            try:
                result = llm.complete(route, request, unit_id=unit.id, attempt=number)
            except LLMError as exc:
                if exc.retryable:
                    detail = f"{exc.message} ({exc.reason})" if exc.reason else exc.message
                    return _AttemptEnd(AttemptOutcome.ERROR, detail=detail)
                raise
            reply = result.as_message()
            turns.messages.append(reply)
            self._log(store, reply, usage=result.usage.model_dump(), model=result.model)
            tokens += result.usage.total
            if result.stop_reason is StopReason.REFUSAL:
                category = result.refusal_category or "unspecified"
                return _AttemptEnd(AttemptOutcome.REFUSED, detail=f"the model refused ({category})")
            if settings.max_attempt_tokens is not None and tokens > settings.max_attempt_tokens:
                return _AttemptEnd(AttemptOutcome.ERROR, detail="the attempt's token limit ran out")
            calls = result.tool_calls
            if result.stop_reason is StopReason.MAX_TOKENS:
                cutoffs += 1
                turns.pending += [
                    ToolResult(call_id=c.id, name=c.name, content="Not run: the reply was cut off.",
                               is_error=True)
                    for c in calls
                ]  # fmt: skip
                turns.pending.append(TextBlock(text=render("cut_off")))
                if cutoffs > MAX_CUTOFFS:
                    return _AttemptEnd(
                        AttemptOutcome.ERROR, detail="replies kept hitting the output limit"
                    )
                continue
            if not calls:
                if reminded:
                    return _AttemptEnd(
                        AttemptOutcome.NO_SUBMISSION, detail=result.text.strip()[:2000] or None
                    )
                reminded = True
                turns.pending.append(TextBlock(text=render("submit_reminder")))
                continue
            batch: EditBatch | None = None
            submit_id = ""
            for call in calls:
                if call.name == SUBMIT_EDITS_TOOL:
                    if batch is not None:
                        turns.pending.append(
                            ToolResult(call_id=call.id, name=call.name, is_error=True,
                                       content="Only one submit_edits call per reply is used.")
                        )  # fmt: skip
                        continue
                    try:
                        batch = EditBatch.model_validate(call.arguments)
                        submit_id = call.id
                    except ValidationError as exc:
                        problems = "; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()[:10]
                        )
                        turns.pending.append(
                            ToolResult(call_id=call.id, name=call.name, is_error=True,
                                       content=f"The edits don't match the schema: {problems}")
                        )  # fmt: skip
                    continue
                tool_calls += 1
                if tool_calls > settings.max_tool_calls:
                    text = render("tool_limit", limit=str(settings.max_tool_calls))
                    turns.pending.append(
                        ToolResult(call_id=call.id, name=call.name, content=text, is_error=True)
                    )
                    continue
                out = toolbox.run(call.name, call.arguments)
                turns.pending.append(
                    ToolResult(
                        call_id=call.id, name=call.name, content=out.content, is_error=out.is_error
                    )
                )
            if batch is None:
                if any(c.name == SUBMIT_EDITS_TOOL for c in calls):
                    edit_failures += 1
                    if edit_failures > settings.max_edit_retries:
                        return _AttemptEnd(
                            AttemptOutcome.EDITS_REJECTED, detail="invalid submissions"
                        )
                continue
            rejection = self.apply_edits(batch, unit, rules)
            if rejection is not None:
                edit_failures += 1
                turns.pending.append(
                    ToolResult(
                        call_id=submit_id, name=SUBMIT_EDITS_TOOL, content=rejection, is_error=True
                    )
                )
                if edit_failures > settings.max_edit_retries:
                    return _AttemptEnd(AttemptOutcome.EDITS_REJECTED, detail=rejection)
                continue
            applied = render("applied", count=str(len(batch.edits)), files=", ".join(batch.paths))
            turns.pending.append(
                ToolResult(call_id=submit_id, name=SUBMIT_EDITS_TOOL, content=applied)
            )
            return _AttemptEnd(AttemptOutcome.SUCCEEDED, batch=batch)

    def apply_edits(
        self, batch: EditBatch, unit: WorkUnit, rules: Sequence[LoadedRule]
    ) -> str | None:
        """Apply a batch through PatchEngine; None on success, else the rejection feedback."""
        outside = [p for p in batch.paths if not self.allowed(p, unit, rules)]
        if outside:
            return (
                "Rejected: these paths are outside the unit, which you may not edit: "
                + ", ".join(outside)
                + ". Change only the unit's files: "
                + ", ".join(unit.files)
            )
        result = self._engine.apply(batch)
        if result.applied:
            return None
        return "No edit was applied (they apply all or nothing):\n\n" + result.feedback()

    # ------------------------------------------------------------------ records

    def _log(
        self,
        store: AttemptArtifacts,
        message: Message,
        *,
        usage: dict[str, int] | None = None,
        model: str | None = None,
    ) -> None:
        if not self.settings.store_transcripts:
            return
        record = message.model_dump(mode="json", exclude_none=True)
        if usage is not None:
            record["usage"] = usage
            record["model"] = model
        # The transcript quotes the project's files and tool output; credentials in them stay
        # out of the stored copy (NFR-5.3). The conversation sent to the model is unchanged.
        clean = Redactor.from_environment().redact_json(cast(Json, record))
        with store.transcript_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(clean, ensure_ascii=False) + "\n")

    def save_logs(self, store: AttemptArtifacts) -> None:
        for name, target in (("build.log", store.build_log), ("test.log", store.test_log)):
            source = self.ctx.log_dir / name
            if source.is_file():
                shutil.copyfile(source, target)
