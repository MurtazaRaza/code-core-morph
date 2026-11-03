"""Characterization tests written by a model: the ``test_writer`` role (SPEC §7.11, D-46).

A job's context lists the untested code, the full text of the files declaring and defining it,
its uses elsewhere, how the tests are built and an existing test of the project. The writer
gets two attempts, the second with the checks' feedback (FR-7.3's regeneration); then the
checker keeps what passed. A host agent (D-41) gets the same content as one document.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.agent.context import UnitContext, _demote, fenced, read_text
from codecoremorph.agent.loop import UnitRunner, _Turns
from codecoremorph.agent.tools import ToolBox
from codecoremorph.core.errors import ContextOverflowError
from codecoremorph.core.models import (
    Attempt,
    AttemptOutcome,
    CharacterizationJob,
    FileRole,
    JobResult,
    Message,
    Role,
    SystemBlock,
    TextBlock,
    ToolSpec,
    WorkUnit,
)
from codecoremorph.parity.characterize import Characterizer, JobCheck
from codecoremorph.planner.estimate import estimate_tokens, unit_token_limit
from codecoremorph.prompts import PromptError, render
from codecoremorph.store.index import IndexStore

ATTEMPTS = 2  # the first and one regeneration (SPEC §7.11 step 4)
USES_PER_SYMBOL = 3
MAX_USES = 40
EXAMPLE_LINES = 80
CHARACTERIZE_RULE = "characterize"  # the job's stand-in rule ID in records


def job_unit(job: CharacterizationJob) -> WorkUnit:
    """The job as the agent loop and the records see it: one file, a stand-in rule."""
    return WorkUnit(id=job.id, seq=1, rules=[CHARACTERIZE_RULE], files=[job.test_file])


def _symbols(job: CharacterizationJob) -> str:
    lines: list[str] = []
    for s in job.symbols:
        where = f"defined in {s.file}:{s.line}"
        if s.declared_in and s.declared_in != [s.file]:
            where = f"declared in {', '.join(s.declared_in)}; {where}"
        signature = f": `{s.signature}`" if s.signature else ""
        lines.append(f"- `{s.qualified_name}` ({s.kind.value}){signature}; {where}")
    return "\n".join(lines)


def _files(job: CharacterizationJob, worktree: Path) -> str:
    paths: list[str] = []
    for s in job.symbols:
        for path in [*s.declared_in, s.file]:
            if path not in paths:
                paths.append(path)
    headers = [p for p in paths if p not in job.sources]
    blocks: list[str] = []
    for path in [*headers, *job.sources]:
        text = read_text(worktree, path)
        missing = f"### {path}\n\n(not a text file)"
        blocks.append(fenced(path, text) if text is not None else missing)
    return "\n\n".join(blocks)


def _line(worktree: Path, path: str, number: int, cache: dict[str, list[str]]) -> str:
    if path not in cache:
        text = read_text(worktree, path)
        cache[path] = text.split("\n") if text is not None else []
    lines = cache[path]
    return lines[number - 1].strip() if 0 < number <= len(lines) else ""


def _usages(job: CharacterizationJob, store: IndexStore | None, worktree: Path) -> str:
    if store is None:
        return "The index isn't available."
    tests = {f.path for f in store.files(FileRole.TEST)}
    own = {s.file for s in job.symbols}
    cache: dict[str, list[str]] = {}
    lines: list[str] = []
    for s in job.symbols:
        shown = 0
        for ref in store.references_to(s.id):
            if ref.from_file in tests or (ref.from_file in own and ref.from_symbol == s.id):
                continue
            text = _line(worktree, ref.from_file, ref.line, cache)
            lines.append(f"- `{s.name}` at {ref.from_file}:{ref.line}: `{text}`")
            shown += 1
            if shown >= USES_PER_SYMBOL or len(lines) >= MAX_USES:
                break
        if len(lines) >= MAX_USES:
            break
    return "\n".join(lines) or "None: nothing else in the project uses this code."


def _example(job: CharacterizationJob, store: IndexStore | None, worktree: Path) -> str:
    if store is None:
        return "The index isn't available."
    tests = [f.path for f in store.files(FileRole.TEST)]
    if not tests:
        return "The project has no test file yet."
    headers = {h for s in job.symbols for h in s.declared_in}
    near = [t for h in sorted(headers) for t in store.dependents_of(h) if t in tests]
    path = near[0] if near else tests[0]
    text = read_text(worktree, path)
    if text is None:
        return "The project has no readable test file."
    lines = text.split("\n")
    if len(lines) > EXAMPLE_LINES:
        text = "\n".join([*lines[:EXAMPLE_LINES], f"[{len(lines) - EXAMPLE_LINES} more lines]"])
    return fenced(path, text)


def job_message(
    job: CharacterizationJob,
    *,
    worktree: Path,
    index_path: Path | None,
    language: str,
    source: str,
    scope_all: bool = False,
) -> str:
    """The job's description: its code, files, uses, and how its tests are built."""
    try:
        framework = render(f"test_framework_{language}", source=source).strip()
    except PromptError:
        framework = f"Use the project's own {language} test framework."
    store = IndexStore.open(index_path) if index_path is not None and index_path.is_file() else None
    try:
        usages = _usages(job, store, worktree)
        example = _example(job, store, worktree)
    finally:
        if store is not None:
            store.close()
    note = "`scope: all` asks for tests of this code even where some exist." if scope_all else ""
    return render(
        "characterize_job",
        job_id=job.id,
        test_file=job.test_file,
        max_tests=str(job.max_tests),
        scope_note=note,
        symbols=_symbols(job),
        framework=framework,
        files=_files(job, worktree),
        usages=usages,
        example=example,
    ).strip()


def job_context(
    job: CharacterizationJob,
    *,
    worktree: Path,
    index_path: Path | None,
    language: str,
    source: str,
    target: str,
    tools: Sequence[ToolSpec],
    token_budget: int,
    scope_all: bool = False,
) -> UnitContext:
    system = render("test_writer_system", language=language, source=source, target=target)
    message = job_message(
        job, worktree=worktree, index_path=index_path, language=language, source=source,
        scope_all=scope_all,
    )  # fmt: skip
    tool_chars = sum(len(t.model_dump_json()) for t in tools)
    tokens = estimate_tokens(len(system) + len(message) + tool_chars)
    if tokens > token_budget:
        raise ContextOverflowError(
            f"characterization job {job.id} needs about {tokens:,} tokens of context",
            reason=f"the test writer model's budget is {token_budget:,} tokens",
            hint="Lower parity.characterization_tests.max_tests_per_unit, or use a model with "
            "a larger context window.",
        )
    return UnitContext(
        system=[SystemBlock(text=system.strip(), cache_breakpoint=True)],
        first_message=Message.user(message, cache_breakpoint=True),
        tokens=tokens,
    )


def host_job_document(
    job: CharacterizationJob,
    *,
    worktree: Path,
    index_path: Path | None,
    language: str,
    source: str,
    target: str,
    attempt: int,
    status: str,
    edits_file: str,
    scope_all: bool = False,
) -> str:
    """The context document of a host agent's characterization attempt (D-41, D-46)."""
    message = job_message(
        job, worktree=worktree, index_path=index_path, language=language, source=source,
        scope_all=scope_all,
    )  # fmt: skip
    return render(
        "host_characterize",
        job_id=job.id,
        attempt=str(attempt),
        attempts=str(ATTEMPTS),
        status=status.strip(),
        language=language,
        source=source,
        target=target,
        test_file=job.test_file,
        max_tests=str(job.max_tests),
        edits_file=edits_file,
        job=_demote(message),
    )


class TestWriterRunner(UnitRunner):
    """Runs characterization jobs with the ``test_writer`` role's model."""

    __test__ = False  # not a pytest test class

    def run_job(
        self,
        job: CharacterizationJob,
        checker: Characterizer,
        *,
        language: str,
        scope_all: bool = False,
    ) -> JobResult:
        """Two attempts, then the checker settles the job. The worktree keeps the job's file
        only when some of its tests were accepted."""
        if self.llm is None:
            raise ValueError("TestWriterRunner needs an LLMService")
        llm = self.llm
        unit = job_unit(job)
        toolbox = ToolBox(self.worktree.path, self.index_path)
        route = llm.route(Role.TEST_WRITER)
        attempts: list[Attempt] = []
        try:
            context = job_context(
                job,
                worktree=self.worktree.path,
                index_path=self.index_path,
                language=language,
                source=self.source,
                target=self.target,
                tools=toolbox.specs,
                token_budget=unit_token_limit(llm.capabilities(route).context_window),
                scope_all=scope_all,
            )
        except ContextOverflowError as exc:
            return checker.settle(job, None, attempts=0, reason=str(exc))
        turns = _Turns([context.first_message])
        check: JobCheck | None = None
        reason: str | None = None
        for number in range(1, ATTEMPTS + 1):
            if number > 1 and check is not None:
                feedback = render(
                    "characterize_retry", feedback=check.feedback, test_file=job.test_file
                )
                turns.pending.append(TextBlock(text=feedback))
            started = datetime.now(UTC)
            store = self.artifacts.attempt(job.id, number).ensure()
            self.checkpoint()
            end = self._attempt(llm, number, route, unit, [], context, turns, toolbox, store)
            attempt = Attempt(
                run_id=self.run_id,
                unit_id=job.id,
                number=number,
                role=Role.TEST_WRITER,
                model=route.name,
                started_at=started,
                outcome=end.outcome,
                summary=end.batch.summary if end.batch else end.detail,
            )
            if end.batch is None:
                attempts.append(attempt.model_copy(update={"finished_at": datetime.now(UTC)}))
                if end.outcome in (AttemptOutcome.REFUSED, AttemptOutcome.ERROR):
                    reason = end.detail
                    break
                check = JobCheck("missing", end.detail or "The attempt ended without edits.")
                continue
            check = checker.check(job)
            self.save_logs(store)
            outcome = AttemptOutcome.SUCCEEDED if check.ok else _outcome(check)
            attempts.append(
                attempt.model_copy(
                    update={
                        "finished_at": datetime.now(UTC),
                        "outcome": outcome,
                        "changed_files": [job.test_file],
                        "diagnostics": check.diagnostics[:50],
                    }
                )
            )
            if not check.ok:
                store.feedback_md.write_text(check.feedback, encoding="utf-8")
            if check.ok or not check.retryable:
                break
        result = checker.settle(job, check, attempts=len(attempts), reason=reason)
        return result.model_copy(update={"records": attempts})


def _outcome(check: JobCheck) -> AttemptOutcome:
    if check.stage in ("configure", "build"):
        return AttemptOutcome.BUILD_FAILED
    if check.stage == "tests":
        return AttemptOutcome.TESTS_FAILED
    return AttemptOutcome.INVARIANT_FAILED
