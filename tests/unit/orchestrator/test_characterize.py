"""The run's characterization step with the fake adapter (FR-7.2 to FR-7.6, SPEC §7.11, D-46):
jobs before the plan, the writer's two attempts, acceptance case by case, the commit before
every unit, the extended baseline, keep: false, and host-agent jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.gtest import assertions, drop_tests, find_cases
from codecoremorph.cli.run import _awaiting  # pyright: ignore[reportPrivateUsage]
from codecoremorph.core.models import (
    AdapterCapabilities,
    Baseline,
    CharacterizationPlan,
    CompletionRequest,
    CompletionResult,
    JobStatus,
    RunReport,
    RunStatus,
    SymbolIndex,
    TestAssertions,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    TextBlock,
)
from codecoremorph.llm.replay import Responder
from codecoremorph.orchestrator.lifecycle import RunPlace
from codecoremorph.orchestrator.run import AgentAction, RunOptions
from codecoremorph.store.index import IndexStore
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.messages import parse_trailers

from ..agent.conftest import replace_edit, reply, submit
from .conftest import FIXED, MACRO, EngineWorld, IndexingAdapter

TEST_DIR = "tests/codecoremorph_characterization"
TEST_FILE = f"{TEST_DIR}/geo_test.cpp"
TWICE = "gtest:GeoCharacterization.Twice"
NEGATIVE = "gtest:GeoCharacterization.TwiceNegative"
TESTS = """\
#include <gtest/gtest.h>
#include "geo.h"

TEST(GeoCharacterization, Twice) {
  EXPECT_EQ(4, twice(2));
}

TEST(GeoCharacterization, TwiceNegative) {
  EXPECT_EQ(-4, twice(-2));  // WRONG
}
"""


class CharacterizingAdapter(IndexingAdapter):
    """The fake adapter, able to add characterization tests. The index holds one untested
    function, ``twice`` in include/geo.h. A test case passes unless its body says WRONG; one
    that says FLAKY fails every other run; one that uses rand( fails the lint."""

    def __init__(self) -> None:
        super().__init__()
        self.test_runs = 0

    def capabilities(self) -> AdapterCapabilities:
        return (
            super()
            .capabilities()
            .model_copy(update={"register_test_files": True, "extract_assertions": True})
        )

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        index = super().index(ctx, files)
        with IndexStore.open(index.path) as store:
            store.write(
                symbols=[("c:@F@twice", "twice", "twice", "function", "include/geo.h", 2, 1, 2,
                          30, "Scalar twice(Scalar)", "none", 1, "none")]
            )  # fmt: skip
        return index

    def characterization_test_file(self, test_dir: str, stem: str) -> str:
        return f"{test_dir}/{stem}_test.cpp"

    def register_test_files(
        self, ctx: AdapterContext, test_dir: str, files: list[str], sources: list[str]
    ) -> list[str]:
        listing = ctx.worktree / test_dir / "CMakeLists.txt"
        root = ctx.worktree / "CMakeLists.txt"
        text = root.read_text(encoding="utf-8")
        line = f"add_subdirectory({test_dir})\n"
        if files:
            listing.parent.mkdir(parents=True, exist_ok=True)
            listing.write_text("\n".join(files) + "\n", encoding="utf-8")
            if line not in text:
                root.write_text(text + line, encoding="utf-8")
        else:
            listing.unlink(missing_ok=True)
            root.write_text(text.replace(line, ""), encoding="utf-8")
        return [f"{test_dir}/CMakeLists.txt", "CMakeLists.txt"]

    def characterization_tests(self, test_ids: list[str]) -> TestSelection:
        return TestSelection(test_ids=list(test_ids))

    def check_determinism(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        return [
            f"{f}:1: uses randomness (rand()"
            for f in files
            if "rand(" in (ctx.worktree / f).read_text(encoding="utf-8")
        ]

    def drop_tests(self, ctx: AdapterContext, file: str, test_ids: list[str]) -> int:
        path = ctx.worktree / file
        text = drop_tests(path.read_text(encoding="utf-8"), {i[6:] for i in test_ids})
        path.write_text(text, encoding="utf-8")
        return len(find_cases(text))

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        found: list[TestAssertions] = []
        for rel in files:
            path = ctx.worktree / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for block in find_cases(text):
                found.append(
                    TestAssertions(file=rel, test=block.name, test_id=f"gtest:{block.name}",
                                   line=block.line, assertions=assertions(text, block))
                )  # fmt: skip
        return found

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        report = super().run_tests(ctx, selection)
        self.test_runs += 1
        cases = list(report.cases)
        listing = ctx.worktree / TEST_DIR / "CMakeLists.txt"
        for rel in listing.read_text(encoding="utf-8").split() if listing.is_file() else []:
            path = ctx.worktree / rel
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            for block in find_cases(text):
                body = text[block.body_start : block.body_end]
                failed = "WRONG" in body or ("FLAKY" in body and self.test_runs % 2 == 0)
                cases.append(
                    TestCaseResult(
                        test_id=f"gtest:{block.name}",
                        status=TestStatus.FAILED if failed else TestStatus.PASSED,
                        duration_s=0,
                        message="Expected equality of these values: -4 and twice(-2), which is 4"
                        if failed
                        else None,
                    )
                )
        if selection is not None and selection.test_ids is not None:
            cases = [c for c in cases if c.test_id in selection.test_ids]
        return TestReport(cases=cases, duration_s=0, log_path=report.log_path)


def _world(engine_world: EngineWorld) -> EngineWorld:
    engine_world.adapter = CharacterizingAdapter()
    return engine_world


def create(content: str = TESTS) -> dict[str, str]:
    return {"op": "create", "path": TEST_FILE, "content": content}


def write(call_id: str, *edits: dict[str, str]) -> CompletionResult:
    return reply(submit(call_id, *edits, summary="Tests for twice()."))


def fix_macro(call_id: str = "u1") -> CompletionResult:
    return reply(submit(call_id, replace_edit("include/geo.h", MACRO, FIXED)))


def _text(request: CompletionRequest, index: int) -> str:
    """The text that starts (``0``) or ends (``-1``) the request's first or last message."""
    block = request.messages[index].content[index]
    assert isinstance(block, TextBlock)
    return block.text


def _baseline(world: EngineWorld, run_id: str) -> Baseline:
    path = StateDir.for_repo(world.repo).run(run_id).root / "baseline.json"
    return Baseline.model_validate_json(path.read_bytes())


def _report(world: EngineWorld, run_id: str) -> RunReport:
    path = StateDir.for_repo(world.repo).run(run_id).report_json
    return RunReport.model_validate_json(path.read_bytes())


def _show(world: EngineWorld, branch: str, path: str) -> str:
    return world.git(world.repo, "show", f"{branch}:{path}")


def test_tests_are_regenerated_once_then_committed_before_the_units(
    engine_world: EngineWorld,
) -> None:
    world = _world(engine_world)
    config = world.manifest()
    script: list[Responder] = [
        write("t1", create()),
        write("t2", replace_edit(TEST_FILE, "  // WRONG", "")),
        fix_macro(),
    ]
    result, scripted, _ = world.run(config, script)
    assert result.status is RunStatus.COMPLETED, result.reason
    # The test writer's first request: its own system prompt, the job, the file to write.
    first = scripted.requests[0]
    assert "You are the test writer of CodeCoreMorph" in first.system[0].text
    job_text = _text(first, 0)
    assert f"`{TEST_FILE}`" in job_text
    assert "`twice` (function)" in job_text
    # The regeneration attempt carries the failure, with the actual value.
    retry = _text(scripted.requests[1], -1)
    assert f"{NEGATIVE}: it failed on the current code" in retry
    assert "twice(-2), which is 4" in retry
    # The characterization commit comes before the unit's.
    unit_message, characterize_message = world.log(result.branch)
    assert characterize_message.startswith("codecoremorph(characterize): add 2 characterization")
    assert parse_trailers(characterize_message)["CodeCoreMorph-Characterize"] == "2 tests"
    assert parse_trailers(unit_message)["CodeCoreMorph-Unit"] == "U-0001"
    assert "WRONG" not in _show(world, result.branch, TEST_FILE)
    assert f"add_subdirectory({TEST_DIR})" in _show(world, result.branch, "CMakeLists.txt")
    # The baseline holds them now, so every unit's parity covers them (FR-7.4).
    baseline = _baseline(world, result.run_id)
    assert baseline.characterization_tests == [TWICE, NEGATIVE]
    assert {TWICE, NEGATIVE} <= set(baseline.tests.passing)
    report = _report(world, result.run_id)
    summary = report.characterization
    assert summary is not None
    assert summary.ran
    assert summary.untested_symbols == 1
    (job,) = summary.jobs
    assert (job.job_id, job.plan_unit, job.status) == ("C-0001", "U-0001", JobStatus.ACCEPTED)
    assert (job.tests, job.attempts) == ([TWICE, NEGATIVE], 2)
    assert report.final is not None
    assert report.final.passed
    markdown = StateDir.for_repo(world.repo).run(result.run_id).report_md.read_text("utf-8")
    assert "## Characterization tests" in markdown


def test_cases_still_failing_after_the_regeneration_are_dropped(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    script: list[Responder] = [
        write("t1", create()),
        write("t2", replace_edit(TEST_FILE, "// WRONG", "// WRONG again")),
        fix_macro(),
    ]
    result, _, _ = world.run(world.manifest(), script)
    assert result.status is RunStatus.COMPLETED, result.reason
    kept = _show(world, result.branch, TEST_FILE)
    assert "TwiceNegative" not in kept
    assert "TEST(GeoCharacterization, Twice)" in kept
    summary = _report(world, result.run_id).characterization
    assert summary is not None
    (job,) = summary.jobs
    assert job.status is JobStatus.PARTIAL
    assert job.tests == [TWICE]
    assert [r.test for r in job.rejected] == [NEGATIVE]
    assert _baseline(world, result.run_id).characterization_tests == [TWICE]


def test_a_job_with_nothing_accepted_leaves_nothing(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    flaky = TESTS.replace("WRONG", "FLAKY").replace("twice(2));", "twice(2));  // WRONG")
    same = "TEST(GeoCharacterization, Twice) {"
    script: list[Responder] = [
        write("t1", create(flaky)),
        write("t2", replace_edit(TEST_FILE, same, same)),
        fix_macro(),
    ]
    result, _, _ = world.run(world.manifest(), script)
    assert result.status is RunStatus.COMPLETED, result.reason
    (unit_message,) = world.log(result.branch)
    assert parse_trailers(unit_message)["CodeCoreMorph-Unit"] == "U-0001"
    summary = _report(world, result.run_id).characterization
    assert summary is not None
    (job,) = summary.jobs
    assert job.status is JobStatus.DROPPED
    reasons = {r.test: r.reason for r in job.rejected}
    assert "isn't deterministic" in reasons[NEGATIVE]
    assert _baseline(world, result.run_id).characterization_tests == []


def test_the_lint_rejects_nondeterministic_tests(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    random = TESTS.replace("  // WRONG", "  std::srand(1); (void)rand();")
    script: list[Responder] = [
        write("t1", create(random)),
        write("t2", replace_edit(TEST_FILE, "  std::srand(1); (void)rand();", "")),
        fix_macro(),
    ]
    result, scripted, _ = world.run(world.manifest(), script)
    assert result.status is RunStatus.COMPLETED, result.reason
    retry = _text(scripted.requests[1], -1)
    assert "may not read the clock, use randomness" in retry
    assert f"{TEST_FILE}:1: uses randomness" in retry
    assert _baseline(world, result.run_id).characterization_tests == [TWICE, NEGATIVE]


def test_characterization_can_be_turned_off(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    config = world.manifest(parity="{baseline_runs: 1, characterization_tests: {enabled: false}}")
    result, scripted, _ = world.run(config, [fix_macro()])
    assert result.status is RunStatus.COMPLETED, result.reason
    assert len(scripted.requests) == 1
    summary = _report(world, result.run_id).characterization
    assert summary is not None
    assert not summary.ran
    assert summary.reason == "parity.characterization_tests.enabled is false"


def test_keep_false_removes_the_tests_before_the_final_verification(
    engine_world: EngineWorld,
) -> None:
    world = _world(engine_world)
    config = world.manifest(parity="{baseline_runs: 1, characterization_tests: {keep: false}}")
    script: list[Responder] = [write("t1", create(TESTS.replace("  // WRONG", ""))), fix_macro()]
    result, _, _ = world.run(config, script)
    assert result.status is RunStatus.COMPLETED, result.reason
    removed, unit_message, added = world.log(result.branch)
    assert removed.startswith("codecoremorph(characterize): remove the characterization tests")
    assert parse_trailers(removed)["CodeCoreMorph-Characterize"] == "removed"
    assert parse_trailers(unit_message)["CodeCoreMorph-Unit"] == "U-0001"
    assert added.startswith("codecoremorph(characterize): add 2")
    assert world.git(world.repo, "ls-tree", "-r", "--name-only", result.branch).count(TEST_DIR) == 0
    assert "add_subdirectory" not in _show(world, result.branch, "CMakeLists.txt")
    report = _report(world, result.run_id)
    assert report.final is not None
    assert report.final.passed
    assert report.characterization is not None
    assert report.characterization.removed


class NoTestsAdapter(CharacterizingAdapter):
    """A project whose own tests don't exist: only characterization tests run."""

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        report = super().run_tests(ctx, selection)
        cases = [c for c in report.cases if not c.test_id.startswith("gtest:Geo.")]
        return TestReport(cases=cases, duration_s=0, log_path=report.log_path)


def test_a_project_without_tests_runs_once_characterization_gives_it_some(
    engine_world: EngineWorld,
) -> None:
    world = engine_world
    world.adapter = NoTestsAdapter()
    script: list[Responder] = [write("t1", create(TESTS.replace("  // WRONG", ""))), fix_macro()]
    result, _, _ = world.run(world.manifest(), script)
    assert result.status is RunStatus.COMPLETED, result.reason
    assert _baseline(world, result.run_id).tests.passing == [TWICE, NEGATIVE]


def test_a_project_still_without_tests_stops_before_the_plan(engine_world: EngineWorld) -> None:
    world = engine_world
    world.adapter = NoTestsAdapter()
    silent = reply()  # the writer submits nothing, twice
    result, _, _ = world.run(world.manifest(), [silent, silent, silent, silent])
    assert result.status is RunStatus.FAILED
    assert result.baseline_failed
    assert "characterization added none" in (result.reason or "")
    with world.store() as store:
        assert store.units(result.run_id) == []  # no plan was made


# --------------------------------------------------------------------------- host agents

HOST = RunOptions(host=True, poll_s=0.01)


def _edits(world: EngineWorld, run_id: str, *edits: dict[str, str]) -> Path:
    path = StateDir.for_repo(world.repo).run(run_id).root / "edits.json"
    body = {"edits": list(edits), "summary": "Tests for twice().", "review_notes": []}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _again(world: EngineWorld, run_id: str, agent: AgentAction | None = None) -> Any:
    config = world.repo / "migration_config.yaml"
    result, scripted, _ = world.run(
        config, [], options=HOST, run_id=run_id, resume=True, agent=agent
    )
    assert scripted.requests == []
    return result


def test_a_host_agent_writes_the_tests_then_the_run_goes_on(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    started, scripted, _ = world.run(world.manifest(), [], options=HOST)
    assert scripted.requests == []
    assert started.awaiting
    waiting = started.waiting
    assert waiting is not None
    assert (waiting["gate"], waiting["kind"], waiting["unit"]) == (
        "agent",
        "characterize",
        "C-0001",
    )
    assert waiting["test_file"] == TEST_FILE
    context = Path(waiting["context_file"]).read_text(encoding="utf-8")
    assert context.startswith("# CodeCoreMorph characterization job C-0001: attempt 1 of 2")
    assert "`codecoremorph unit submit`" in context
    assert any("Characterization job C-0001: write tests" in n for n in started.notes)
    place = RunPlace.for_config(world.repo / "migration_config.yaml")
    told = _awaiting(place, started)  # what the command tells the host agent to do next
    assert told[0] == (
        f"Characterization job C-0001 waits for your tests in {TEST_FILE}: attempt 1 of 2."
    )
    assert "`codecoremorph unit submit`" in told[1]

    _edits(world, started.run_id, create())
    second = _again(world, started.run_id, AgentAction("submit"))
    assert second.awaiting
    assert second.waiting is not None
    assert (second.waiting["unit"], second.waiting["attempt"]) == ("C-0001", 2)
    assert any("attempt 1 failed its checks" in n for n in second.notes)

    _edits(world, started.run_id, replace_edit(TEST_FILE, "  // WRONG", ""))
    third = _again(world, started.run_id, AgentAction("submit"))
    assert third.waiting is not None
    assert third.waiting["unit"] == "U-0001"  # the job is done; the first unit waits
    assert any("all 2 test(s) accepted" in n for n in third.notes)

    _edits(world, started.run_id, replace_edit("include/geo.h", MACRO, FIXED))
    done = _again(world, started.run_id, AgentAction("submit"))
    assert done.status is RunStatus.COMPLETED, done.reason
    unit_message, characterize_message = world.log(done.branch)
    assert characterize_message.startswith("codecoremorph(characterize): add 2")
    assert parse_trailers(unit_message)["CodeCoreMorph-Unit"] == "U-0001"
    plan = CharacterizationPlan.model_validate_json(
        StateDir.for_repo(world.repo).run(done.run_id).characterization_json.read_bytes()
    )
    assert [j.id for j in plan.jobs] == ["C-0001"]
    summary = _report(world, done.run_id).characterization
    assert summary is not None
    assert [j.status for j in summary.jobs] == [JobStatus.ACCEPTED]
    assert [r.model for r in summary.jobs[0].records] == []  # the report leaves records out


def test_a_host_agent_can_give_a_job_up(engine_world: EngineWorld) -> None:
    world = _world(engine_world)
    started, _, _ = world.run(world.manifest(), [], options=HOST)
    given_up = _again(world, started.run_id, AgentAction("abort", "no interface reaches it"))
    assert given_up.waiting is not None
    assert given_up.waiting["unit"] == "U-0001"
    assert any("no interface reaches it" in n for n in given_up.notes)
    _edits(world, started.run_id, replace_edit("include/geo.h", MACRO, FIXED))
    done = _again(world, started.run_id, AgentAction("submit"))
    assert done.status is RunStatus.COMPLETED, done.reason
    (unit_message,) = world.log(done.branch)
    assert parse_trailers(unit_message)["CodeCoreMorph-Unit"] == "U-0001"
    summary = _report(world, done.run_id).characterization
    assert summary is not None
    assert [j.status for j in summary.jobs] == [JobStatus.ABORTED]
