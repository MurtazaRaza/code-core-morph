"""Checking characterization tests on the code before the migration (SPEC §7.11, D-46).

A job's test file is accepted case by case: the file must exist, hold at most the job's number
of test cases with names no other test uses, pass the determinism lint (FR-7.3), configure and
build, and then each case must pass every one of ``RUNS`` consecutive runs. A failed check is
fed back to the writer for one regeneration attempt; after it, ``settle`` drops the cases that
still fail (or the file, when nothing passes) and confirms what is left.

The checker registers the job's file together with the files earlier jobs kept, so the build
it checks is the one the run will commit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.agent.feedback import compiler_feedback, scrub_paths
from codecoremorph.core.models import (
    CharacterizationJob,
    Diagnostic,
    JobResult,
    JobStatus,
    RejectedTest,
    TestCaseResult,
    TestStatus,
)

RUNS = 3  # consecutive passes on the base code (FR-7.3)
MAX_MESSAGE = 1500
REVERSE = (
    "A characterization test records what the code does now: when a test fails, change its "
    "expected value to the actual one shown, or drop the test case."
)


@dataclass
class JobCheck:
    stage: str  # ok | missing | empty | duplicate | too_many | lint | configure | build | tests
    feedback: str = ""
    cases: list[str] = field(default_factory=list[str])  # the file's test IDs
    accepted: list[str] = field(default_factory=list[str])
    rejected: list[RejectedTest] = field(default_factory=list[RejectedTest])
    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])

    @property
    def ok(self) -> bool:
        return self.stage == "ok"

    @property
    def retryable(self) -> bool:
        """A failed configure is the build setup's problem, which new tests can't fix."""
        return self.stage not in ("ok", "configure")


@dataclass
class Kept:
    """What earlier jobs of the run kept: their files, the sources they test, their tests."""

    files: list[str] = field(default_factory=list[str])
    sources: list[str] = field(default_factory=list[str])
    tests: list[str] = field(default_factory=list[str])

    @classmethod
    def from_results(
        cls, jobs: Sequence[CharacterizationJob], results: dict[str, JobResult]
    ) -> Kept:
        kept = cls()
        for job in jobs:
            result = results.get(job.id)
            if result is None or not result.tests:
                continue
            kept.files.append(job.test_file)
            kept.sources += [s for s in job.sources if s not in kept.sources]
            kept.tests += result.tests
        return kept


class Characterizer:
    """Checks one job's tests at a time, against the build with the kept files."""

    def __init__(
        self,
        adapter: LanguageAdapter,
        ctx: AdapterContext,
        *,
        test_dir: str,
        kept: Kept,
        known_tests: Sequence[str] = (),
        runs: int = RUNS,
    ):
        """``known_tests`` are the project's own test IDs (the baseline's): new names must
        differ from them, so parity tells the two apart."""
        self.adapter = adapter
        self.ctx = ctx
        self.test_dir = test_dir
        self.kept = kept
        self.known = set(known_tests) | set(kept.tests)
        self.runs = runs

    @property
    def roots(self) -> list[str]:
        return [self.ctx.sandbox(self.ctx.worktree), str(self.ctx.worktree)]

    def register(self, extra: CharacterizationJob | None = None) -> list[str]:
        """Put the kept files, and ``extra``'s, in the build; returns the files written."""
        files = list(self.kept.files)
        sources = list(self.kept.sources)
        if extra is not None and extra.test_file not in files:
            files.append(extra.test_file)
            sources += [s for s in extra.sources if s not in sources]
        return self.adapter.register_test_files(self.ctx, self.test_dir, files, sources)

    def check(self, job: CharacterizationJob) -> JobCheck:
        file = job.test_file
        if not (self.ctx.worktree / file).is_file():
            return JobCheck(
                "missing",
                f"There is no `{file}`: create it with a `create` edit. It is the only file "
                "the job may write.",
            )
        found = self.adapter.extract_assertions(self.ctx, [file])
        ids = [c.test_id for c in found]
        if not ids:
            return JobCheck("empty", f"`{file}` has no test case the test framework would run.")
        clashing = sorted({i for i in ids if ids.count(i) > 1 or i in self.known})
        if clashing:
            return JobCheck(
                "duplicate",
                "These test names are already taken, by the project's tests, another job's "
                f"or the file itself: {', '.join(clashing)}. Rename their suites.",
                cases=ids,
            )
        if len(ids) > job.max_tests:
            return JobCheck(
                "too_many",
                f"`{file}` has {len(ids)} test cases; the job allows {job.max_tests}. Merge "
                "cases or drop the least useful ones.",
                cases=ids,
            )
        lint = self.adapter.check_determinism(self.ctx, [file])
        if lint:
            listed = "\n".join(f"- {line}" for line in lint[:20])
            return JobCheck(
                "lint",
                "Characterization tests must give the same result on every run and machine, "
                "so they may not read the clock, use randomness, touch files or the network, "
                f"or depend on thread timing:\n\n{listed}",
                cases=ids,
            )
        return self._build_and_run(job, ids)

    def _compile_failure(
        self, job: CharacterizationJob, ids: list[str], diagnostics: list[Diagnostic], step: str
    ) -> JobCheck:
        feedback = compiler_feedback(
            diagnostics,
            unit_files=[job.test_file],
            worktree=self.ctx.worktree,
            changed_files=[job.test_file],
            step=step,
            roots=self.roots,
        )
        return JobCheck(step, feedback, cases=ids, diagnostics=diagnostics)

    def _build_and_run(self, job: CharacterizationJob, ids: list[str]) -> JobCheck:
        self.register(job)
        configured = self.adapter.configure(self.ctx)
        if not configured.success:
            return self._compile_failure(job, ids, configured.diagnostics, "configure")
        built = self.adapter.build(self.ctx)
        if not built.success:
            return self._compile_failure(job, ids, built.diagnostics, "build")
        runs: dict[str, list[TestCaseResult | None]] = {i: [] for i in ids}
        selection = self.adapter.characterization_tests(ids)
        for _ in range(self.runs):
            report = self.adapter.run_tests(self.ctx, selection)
            statuses = {c.test_id: c for c in report.cases}
            for test_id in ids:
                runs[test_id].append(statuses.get(test_id))
        accepted: list[str] = []
        rejected: list[RejectedTest] = []
        for test_id in ids:
            results = runs[test_id]
            passed = [r is not None and r.status is TestStatus.PASSED for r in results]
            if all(passed):
                accepted.append(test_id)
            elif all(r is None for r in results):
                rejected.append(RejectedTest(test=test_id, reason="it didn't run"))
            elif any(passed):
                rejected.append(
                    RejectedTest(
                        test=test_id,
                        reason=f"it passed in {sum(passed)} of {len(passed)} runs, so it "
                        "isn't deterministic",
                    )
                )
            else:
                failure = next(r for r in results if r is not None)
                message = scrub_paths((failure.message or "").strip(), self.roots)
                status = failure.status.value
                reason = f"it {status} on the current code"
                if message:
                    reason += f": {message[:MAX_MESSAGE]}"
                rejected.append(RejectedTest(test=test_id, reason=reason))
        if not rejected:
            return JobCheck("ok", cases=ids, accepted=accepted)
        lines = [f"{len(rejected)} of {len(ids)} test case(s) don't pass every run:"]
        lines += [f"- {r.test}: {r.reason}" for r in rejected]
        lines += ["", REVERSE]
        return JobCheck("tests", "\n".join(lines), cases=ids, accepted=accepted, rejected=rejected)

    def settle(
        self, job: CharacterizationJob, check: JobCheck | None, *, attempts: int,
        reason: str | None = None,
    ) -> JobResult:  # fmt: skip
        """End the job after its last attempt: keep what passed, drop the rest."""
        path = self.ctx.worktree / job.test_file
        rejected = list(check.rejected) if check is not None else []
        if check is not None and check.stage == "tests" and check.accepted:
            self.adapter.drop_tests(self.ctx, job.test_file, [r.test for r in check.rejected])
            confirmed = self._build_and_run(job, list(check.accepted))
            if confirmed.ok:
                return self._kept(job, confirmed.accepted, rejected, attempts, JobStatus.PARTIAL)
            rejected += confirmed.rejected
            check = confirmed
        if check is not None and check.ok:
            return self._kept(job, check.accepted, rejected, attempts, JobStatus.ACCEPTED)
        if check is not None and check.stage not in ("tests", "ok"):
            rejected.append(RejectedTest(test=job.test_file, reason=_first_line(check.feedback)))
        path.unlink(missing_ok=True)
        self.register()
        return JobResult(
            job_id=job.id,
            plan_unit=job.plan_unit,
            status=JobStatus.DROPPED,
            test_file=job.test_file,
            symbols=len(job.symbols),
            rejected=rejected,
            attempts=attempts,
            reason=reason or "no test case passed the checks",
        )

    def _kept(
        self,
        job: CharacterizationJob,
        tests: list[str],
        rejected: list[RejectedTest],
        attempts: int,
        status: JobStatus,
    ) -> JobResult:
        self.kept.files.append(job.test_file)
        self.kept.sources += [s for s in job.sources if s not in self.kept.sources]
        self.kept.tests += tests
        self.known.update(tests)
        return JobResult(
            job_id=job.id,
            plan_unit=job.plan_unit,
            status=status,
            test_file=job.test_file,
            symbols=len(job.symbols),
            tests=tests,
            rejected=rejected,
            attempts=attempts,
        )


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "the file failed the checks"
    return line[:300]
