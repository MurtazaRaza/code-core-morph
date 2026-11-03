"""The run's characterization step (FR-7.2 to FR-7.4, SPEC §7.11, D-46).

Before the plan the run carries out, untested code gets tests that pin down its behavior:

1. **Jobs.** A plan of the base index groups the untested symbols (``parity.selection``):
   one job per unit with any, each with one new test file. They are saved in
   ``characterization.json`` and don't change when the run resumes.
2. **Each job** is written by the ``test_writer`` model or, in a host-agent run, by the host
   (``codecoremorph unit submit|abort``, like a unit), and checked by ``Characterizer``. A job
   ends with a checkpoint commit (trailer ``CodeCoreMorph-Unit: C-NNNN``), then its result in
   ``attempts/C-NNNN/result.json``; a host job also checkpoints between its attempts, since a
   resumed run goes back to the branch's last commit.
3. **The end.** The checkpoints become one commit, ``codecoremorph(characterize): …`` with a
   ``CodeCoreMorph-Characterize`` trailer; the baseline gains the accepted tests (FR-7.4), and
   the index the new files, so the plan made next puts them in the units whose code they call.

A job with nothing accepted leaves nothing behind. The step is skipped when characterization is
off, when the adapter can't add tests to the build, or when no symbol is untested.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from codecoremorph.agent.host import CHECKPOINT_AUTHOR, HOST_MODEL, HostFiles
from codecoremorph.agent.loop import AgentSettings, Guard
from codecoremorph.agent.test_writer import (
    ATTEMPTS,
    TestWriterRunner,
    host_job_document,
    job_unit,
)
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    Attempt,
    AttemptOutcome,
    Baseline,
    CharacterizationJob,
    CharacterizationPlan,
    EditBatch,
    HostFeedbackKind,
    HostStatus,
    HostUnitState,
    JobResult,
    JobStatus,
    Role,
    SymbolIndex,
)
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.planning import plan_from, write_baseline
from codecoremorph.orchestrator.session import RunSession
from codecoremorph.parity.characterize import Characterizer, JobCheck, Kept
from codecoremorph.parity.selection import plan_jobs, untested_symbols
from codecoremorph.patching.engine import PatchEngine
from codecoremorph.report.build import unit_result_path
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.store.index import IndexStore
from codecoremorph.vcs.messages import TRAILER_PREFIX
from codecoremorph.vcs.worktree import Worktree

TRAILER = "Characterize"
StepSink = Callable[[str], None]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Finished:
    """The end of the step: the commit (None when no test was kept), and what changed."""

    commit: str | None
    baseline: Baseline
    index: SymbolIndex | None  # re-indexed when the tree changed


class Characterization:
    """One run's characterization step; the run graph's ``characterize`` nodes call it."""

    def __init__(
        self,
        session: RunSession,
        *,
        baseline: Baseline,
        host_files: HostFiles,
        step: StepSink,
        guard: Guard | None = None,
    ):
        self.session = session
        self.baseline = baseline
        self.host_files = host_files
        self.step = step
        self.guard = guard
        self.settings = session.config.parity.characterization_tests
        self.path = session.artifacts.characterization_json

    @property
    def worktree(self) -> Worktree:
        return self.session.workspace.worktree

    @property
    def scope_all(self) -> bool:
        return self.settings.scope.value == "all"

    # ------------------------------------------------------------------ the jobs

    def unavailable(self) -> str | None:
        """Why the step doesn't run, if it doesn't."""
        if not self.settings.enabled:
            return "parity.characterization_tests.enabled is false"
        adapter = self.session.adapter
        if not adapter.capabilities().register_test_files:
            return f"the {adapter.language} adapter can't add tests to the build"
        return None

    def load(self) -> CharacterizationPlan | None:
        if not self.path.is_file():
            return None
        return CharacterizationPlan.model_validate_json(self.path.read_bytes())

    def _save(self, plan: CharacterizationPlan) -> CharacterizationPlan:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return plan

    def skip(self, reason: str) -> CharacterizationPlan:
        return self._save(
            CharacterizationPlan(scope=self.settings.scope.value, untested_symbols=0, reason=reason)
        )

    def create(self, index: SymbolIndex) -> CharacterizationPlan:
        """The jobs, from a plan of the base index (the plan the run carries out comes after)."""
        session = self.session
        self.step("characterize: choosing the untested code")
        plan, _ = plan_from(session, self.baseline, index, step=lambda _name: None)
        adapter = session.adapter
        worktree = self.worktree.path
        with IndexStore.open(index.path) as store:
            jobs = plan_jobs(
                plan,
                store,
                test_dir=self.settings.dir,
                max_tests=self.settings.max_tests_per_unit,
                scope_all=self.scope_all,
                test_file=adapter.characterization_test_file,
                taken=lambda path: (worktree / path).exists(),
            )
            files = sorted({f for u in plan.units for f in u.files})
            untested = len(untested_symbols(store, files, scope_all=self.scope_all))
        reason = None if jobs else "no symbol of the plan's units lacks a test"
        return self._save(
            CharacterizationPlan(
                scope=self.settings.scope.value,
                untested_symbols=untested,
                jobs=jobs,
                reason=reason,
            )
        )

    def result_path(self, job_id: str) -> Path:
        return unit_result_path(self.session.artifacts, job_id)

    def results(self, plan: CharacterizationPlan) -> dict[str, JobResult]:
        found: dict[str, JobResult] = {}
        for job in plan.jobs:
            path = self.result_path(job.id)
            if path.is_file():
                found[job.id] = JobResult.model_validate_json(path.read_bytes())
        return found

    def next_job(self, plan: CharacterizationPlan) -> CharacterizationJob | None:
        done = self.results(plan)
        return next((j for j in plan.jobs if j.id not in done), None)

    def checker(self, plan: CharacterizationPlan) -> Characterizer:
        session = self.session
        return Characterizer(
            session.adapter,
            session.ctx,
            test_dir=self.settings.dir,
            kept=Kept.from_results(plan.jobs, self.results(plan)),
            known_tests=[*self.baseline.tests.passing, *self.baseline.tests.failing,
                         *self.baseline.tests.flaky, *self.baseline.tests.skipped],
        )  # fmt: skip

    def restart(self, job: CharacterizationJob) -> None:
        """Before a job (re)starts: its checkpoints and files from an earlier try go."""
        worktree = self.worktree
        base = self.session.workspace.base_commit
        mine = [sha for sha, value in worktree.trailer_commits(base, "Unit") if value == job.id]
        worktree.rollback(worktree.parent(mine[-1]) if mine else worktree.head())
        shutil.rmtree(self.session.artifacts.unit_dir(job.id), ignore_errors=True)

    def end_job(self, job: CharacterizationJob, result: JobResult) -> JobResult:
        """Checkpoint the job's tree, then record its result (in that order: a result means
        the tree is safe)."""
        worktree = self.worktree
        if worktree.has_changes():
            worktree.commit(
                f"codecoremorph: checkpoint (characterization job {job.id})",
                trailers={f"{TRAILER_PREFIX}Unit": job.id},
                author=CHECKPOINT_AUTHOR,
            )
        path = self.result_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        accepted = len(result.tests)
        self.step(f"characterize: job {job.id} kept {accepted} test(s) ({result.status.value})")
        return result

    # ------------------------------------------------------------------ a model writes

    def run_with_model(
        self,
        job: CharacterizationJob,
        plan: CharacterizationPlan,
        llm: LLMService,
        index: SymbolIndex,
    ) -> JobResult:
        session = self.session
        config = session.config
        self.step(f"characterize: job {job.id}, {len(job.symbols)} symbol(s) of {job.plan_unit}")
        runner = TestWriterRunner(
            adapter=session.adapter,
            ctx=session.ctx,
            worktree=self.worktree,
            baseline=self.baseline,
            llm=llm,
            order=StandardOrder(
                config.migration.source.language,
                tuple(session.adapter.capabilities().standards),
            ),
            source=config.migration.source.standard,
            target=config.migration.target.standard,
            run_id=session.run_id,
            artifacts=session.artifacts,
            index_path=index.path,
            settings=AgentSettings.from_config(
                config.guardrails, store_transcripts=config.reporting.store_transcripts
            ),
            guard=self.guard,
        )
        result = runner.run_job(
            job, self.checker(plan), language=session.ctx.lang.language, scope_all=self.scope_all
        )
        return self.end_job(job, result)

    # ------------------------------------------------------------------ a host writes

    def host_state(self, job: CharacterizationJob) -> HostUnitState | None:
        """The host's state of ``job``, if it waits for the host."""
        if not self.host_files.state.is_file():
            return None
        state = self.host_files.load()
        if state.job is None or state.job.id != job.id or state.status.is_terminal:
            return None
        return state

    def start_host(self, job: CharacterizationJob, index: SymbolIndex) -> HostUnitState:
        session = self.session
        config = session.config
        head = self.worktree.head()
        now = _now()
        state = HostUnitState(
            run_id=session.run_id,
            config_path=str(session.config_path.resolve()),
            worktree=str(self.worktree.path),
            sandbox=session.executor.kind,
            base_commit=session.workspace.base_commit,
            unit=job_unit(job),
            source=config.migration.source.standard,
            target=config.migration.target.standard,
            snapshot=head,
            head=head,
            index_path=str(index.path),
            configured_standard=session.ctx.lang.standard,
            max_retry_budget=ATTEMPTS - 1,
            max_edit_retries=config.guardrails.max_edit_retries,
            status=HostStatus.WAITING,
            in_run=True,
            job=job,
            user_tree=session.workspace.repository.dirty_state(),
            started_at=now,
            updated_at=now,
        )
        return self._wait(state)

    def _wait(self, state: HostUnitState) -> HostUnitState:
        job = _job_of(state)
        if state.feedback_kind is HostFeedbackKind.INITIAL:
            status = "No test exists yet: write the job's test file."
        elif state.feedback_kind is HostFeedbackKind.REJECTED:
            status = (
                "Your last submission was rejected and nothing was applied "
                f"({state.edit_rejections} of {state.max_edit_retries + 1} tries of this "
                f"attempt used):\n\n{state.feedback or ''}"
            )
        else:
            status = (
                f"Attempt {state.attempt - 1} was checked; `{job.test_file}` holds its tests. "
                f"Fix what failed:\n\n{state.feedback or ''}"
            )
        text = host_job_document(
            job,
            worktree=self.worktree.path,
            index_path=Path(state.index_path) if state.index_path else None,
            language=self.session.ctx.lang.language,
            source=state.source,
            target=state.target,
            attempt=state.attempt,
            status=status,
            edits_file=str(self.host_files.edits),
            scope_all=self.scope_all,
        )
        state = _update(state, status=HostStatus.WAITING)
        self.host_files.context.write_text(text + "\n", encoding="utf-8")
        store = self.session.artifacts.attempt(job.id, state.attempt).ensure()
        (store.root / "context.md").write_text(text + "\n", encoding="utf-8")
        self.host_files.save(state)
        return state

    def submit_host(
        self, state: HostUnitState, plan: CharacterizationPlan
    ) -> tuple[HostUnitState, JobResult | None, list[str]]:
        """Apply the host's edits file and check the tests. Returns the new state, the job's
        result when it ended, and warnings."""
        job = _job_of(state)
        files = self.host_files
        if not files.edits.is_file():
            raise UsageError(
                f"there is no edits file at {files.edits}",
                hint="Write the job's test file as a `create` edit there (the format is in the "
                "context document), then run `codecoremorph unit submit` again.",
            )
        worktree = self.worktree
        warnings: list[str] = []
        if worktree.head() != state.head or worktree.has_changes():
            changed = [f.path for f in worktree.diff(state.head).files]
            worktree.rollback(state.head)
            warnings.append(
                "changes in the run worktree that didn't come through CodeCoreMorph were "
                f"discarded ({', '.join(changed[:20]) or 'commits'}); put every edit in the "
                "edits file"
            )
        store = self.session.artifacts.attempt(job.id, state.attempt).ensure()
        raw = files.edits.read_bytes()
        (store.root / f"edits-{state.edit_rejections + 1}.json").write_bytes(raw)
        files.edits.unlink()
        try:
            batch = EditBatch.model_validate_json(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'the file'}: {e['msg']}"
                for e in exc.errors()[:10]
            )
            return (*self._reject(state, plan, f"The edits file doesn't match the format: "
                                  f"{problems}"), warnings)  # fmt: skip
        outside = [p for p in batch.paths if p != job.test_file]
        if outside:
            rejection = (
                f"Rejected: the job may write only {job.test_file}, not {', '.join(outside)}."
            )
            return (*self._reject(state, plan, rejection), warnings)
        engine = PatchEngine(worktree.path, exclude=self.session.ctx.lang.exclude)
        applied = engine.apply(batch)
        if not applied.applied:
            feedback = "No edit was applied (they apply all or nothing):\n\n" + applied.feedback()
            return (*self._reject(state, plan, feedback), warnings)
        checker = self.checker(plan)
        try:
            check = checker.check(job)
        except BaseException:
            worktree.rollback(state.head)
            files.edits.write_bytes(raw)  # `unit submit` can simply run again
            raise
        record = Attempt(
            run_id=state.run_id,
            unit_id=job.id,
            number=state.attempt,
            role=Role.TEST_WRITER,
            model=HOST_MODEL,
            started_at=state.updated_at,
            finished_at=_now(),
            outcome=AttemptOutcome.SUCCEEDED if check.ok else _outcome(check),
            changed_files=[job.test_file],
            diagnostics=check.diagnostics[:50],
            summary=batch.summary,
            review_notes=list(batch.review_notes),
        )
        state = _update(state, attempts=[*state.attempts, record])
        if not check.ok:
            store.feedback_md.write_text(check.feedback, encoding="utf-8")
        if check.ok or not check.retryable or state.attempt >= ATTEMPTS:
            state, result = self._settle(state, checker, check)
            return state, result, warnings
        return self._next_attempt(state, check.feedback), None, warnings

    def _reject(
        self, state: HostUnitState, plan: CharacterizationPlan, feedback: str
    ) -> tuple[HostUnitState, JobResult | None]:
        job = _job_of(state)
        rejections = state.edit_rejections + 1
        if rejections <= state.max_edit_retries:
            state = _update(
                state,
                edit_rejections=rejections,
                feedback=feedback,
                feedback_kind=HostFeedbackKind.REJECTED,
            )
            return self._wait(state), None
        record = Attempt(
            run_id=state.run_id,
            unit_id=job.id,
            number=state.attempt,
            role=Role.TEST_WRITER,
            model=HOST_MODEL,
            started_at=state.updated_at,
            finished_at=_now(),
            outcome=AttemptOutcome.EDITS_REJECTED,
            summary=feedback[:2000],
        )
        state = _update(state, attempts=[*state.attempts, record])
        if state.attempt < ATTEMPTS:
            return self._next_attempt(state, feedback), None
        return self._settle(state, self.checker(plan), JobCheck("missing", feedback))

    def _next_attempt(self, state: HostUnitState, feedback: str) -> HostUnitState:
        worktree = self.worktree
        head = state.head
        if worktree.has_changes():
            job = _job_of(state)
            head = worktree.commit(
                f"codecoremorph: checkpoint (characterization job {job.id}, attempt "
                f"{state.attempt})",
                trailers={f"{TRAILER_PREFIX}Unit": job.id},
                author=CHECKPOINT_AUTHOR,
            )
        state = _update(
            state,
            head=head,
            attempt=state.attempt + 1,
            edit_rejections=0,
            feedback=feedback,
            feedback_kind=HostFeedbackKind.FAILED,
        )
        return self._wait(state)

    def _settle(
        self, state: HostUnitState, checker: Characterizer, check: JobCheck
    ) -> tuple[HostUnitState, JobResult]:
        job = _job_of(state)
        reason = None if check.ok else check.feedback.strip().splitlines()[0][:300]
        result = checker.settle(job, check, attempts=len(state.attempts), reason=reason)
        result = self.end_job(job, result.model_copy(update={"records": list(state.attempts)}))
        status = HostStatus.FAILED if result.status is JobStatus.DROPPED else HostStatus.SUCCEEDED
        state = _update(state, status=status, head=self.worktree.head(), reason=result.reason)
        self.host_files.save(state)
        return state, result

    def abort_host(self, state: HostUnitState, reason: str | None) -> JobResult:
        """``unit abort``: the job is given up; its tests go, and the run goes on."""
        job = _job_of(state)
        self.worktree.rollback(state.snapshot)
        why = "the agent gave the job up" + (f": {reason}" if reason else "")
        result = JobResult(
            job_id=job.id,
            plan_unit=job.plan_unit,
            status=JobStatus.ABORTED,
            test_file=job.test_file,
            symbols=len(job.symbols),
            attempts=len(state.attempts),
            records=list(state.attempts),
            reason=why,
        )
        self.end_job(job, result)
        self.host_files.save(_update(state, status=HostStatus.ABORTED, reason=why))
        return result

    # ------------------------------------------------------------------ the end

    def committed(self) -> str | None:
        """The run's characterization commit, if the step already made it."""
        base = self.session.workspace.base_commit
        found = [sha for sha, value in self.worktree.trailer_commits(base, TRAILER)
                 if value != "removed"]  # fmt: skip
        return found[-1] if found else None

    def finish(self, plan: CharacterizationPlan) -> Finished:
        """Squash the jobs' checkpoints into the characterization commit and extend the
        baseline (FR-7.4). Safe to repeat: a resumed run finds the commit."""
        session = self.session
        worktree = self.worktree
        results = self.results(plan)
        tests = [t for job in plan.jobs if job.id in results for t in results[job.id].tests]
        commit = self.committed()
        if commit is None and plan.jobs:
            base = session.workspace.base_commit
            if tests:
                worktree.reset_soft(base)
                commit = worktree.commit(
                    _commit_message(plan, results, tests),
                    trailers={
                        f"{TRAILER_PREFIX}Run": session.run_id,
                        f"{TRAILER_PREFIX}{TRAILER}": f"{len(tests)} tests",
                    },
                    author=session.config.git.author,
                )
            else:
                worktree.rollback(base)
        baseline = self.baseline
        if tests:
            passing = sorted({*baseline.tests.passing, *tests})
            baseline = baseline.model_copy(
                update={
                    "tests": baseline.tests.model_copy(update={"passing": passing}),
                    "characterization_tests": tests,
                    "characterization_commit": commit,
                }
            )
            write_baseline(session, baseline)
        index: SymbolIndex | None = None
        if plan.jobs:
            # The build tree still lists every file the jobs tried: configure it as committed,
            # so the index (and the plan after it) sees the tests that stayed.
            self.step("characterize: configure and index the tests")
            session.adapter.configure(session.ctx)
            index = session.adapter.index(session.ctx)
        return Finished(commit=commit, baseline=baseline, index=index)

    def remove(self) -> str | None:
        """``keep: false`` (FR-7.6): a commit taking the tests out again, at the run's end."""
        session = self.session
        worktree = self.worktree
        base = session.workspace.base_commit
        if any(value == "removed" for _, value in worktree.trailer_commits(base, TRAILER)):
            return None
        tests_dir = worktree.path / self.settings.dir
        session.adapter.register_test_files(session.ctx, self.settings.dir, [], [])
        if tests_dir.is_dir():
            shutil.rmtree(tests_dir)
        if not worktree.has_changes():
            return None
        commit = worktree.commit(
            "codecoremorph(characterize): remove the characterization tests\n\n"
            "parity.characterization_tests.keep is false: the tests guarded the migration and "
            "go now that it is done (FR-7.6).\n",
            trailers={
                f"{TRAILER_PREFIX}Run": session.run_id,
                f"{TRAILER_PREFIX}{TRAILER}": "removed",
            },
            author=session.config.git.author,
        )
        plan = self.load()
        if plan is not None:
            self._save(plan.model_copy(update={"removed": commit}))
        return commit


def _commit_message(
    plan: CharacterizationPlan, results: dict[str, JobResult], tests: list[str]
) -> str:
    kept = [results[j.id] for j in plan.jobs if j.id in results and results[j.id].tests]
    symbols = sum(r.symbols for r in kept)
    lines = [
        f"codecoremorph(characterize): add {len(tests)} characterization tests",
        "",
        f"Tests that pin down the current behavior of {symbols} untested symbol(s) before the "
        "migration (SPEC §7.11):",
    ]
    for job in plan.jobs:
        result = results.get(job.id)
        if result is not None and result.tests:
            lines.append(
                f"- {job.test_file}: {len(result.tests)} test(s) of {job.plan_unit}'s code"
            )
    return "\n".join(lines) + "\n"


def _outcome(check: JobCheck) -> AttemptOutcome:
    if check.stage in ("configure", "build"):
        return AttemptOutcome.BUILD_FAILED
    if check.stage == "tests":
        return AttemptOutcome.TESTS_FAILED
    return AttemptOutcome.INVARIANT_FAILED


def _job_of(state: HostUnitState) -> CharacterizationJob:
    if state.job is None:
        raise ValueError(f"{state.unit.id} is a unit, not a characterization job")
    return state.job


def _update(state: HostUnitState, **changes: object) -> HostUnitState:
    changes["updated_at"] = _now()
    return HostUnitState.model_validate({**state.model_dump(), **changes})


__all__ = ["TRAILER", "Characterization", "Finished"]
