"""A unit driven by a host agent, with a fake adapter (D-41, SPEC §7.6.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from codecoremorph.agent.host import HostFiles, HostStep, HostUnit
from codecoremorph.agent.loop import AgentSettings, UnitRunner
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    AttemptOutcome,
    Baseline,
    HostFeedbackKind,
    HostStatus,
    HostUnitState,
    Role,
    TestBaseline,
)
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.messages import parse_trailers

from .conftest import ORDER, RUN_ID, TYPEDEFS, GitRunner, World, replace_edit, unit

MACRO = "#define DECLARE(T, N) typedef T N"
FIXED = "#define DECLARE(T, N) using N = T"
AUTHOR = "Test User <test@example.com>"


def _host(world: World, *, budget: int = 2, edit_retries: int = 1) -> HostUnit:
    artifacts = StateDir(world.tmp / "state").run(RUN_ID).ensure()
    runner = UnitRunner(
        adapter=world.adapter,
        ctx=world.ctx,
        worktree=world.worktree,
        baseline=Baseline(
            base_commit=world.worktree.head(),
            standard="cpp11",
            tests=TestBaseline(runs=1, passing=["gtest:Geo.Area"], failing=["gtest:Geo.Flaky"]),
            configure_s=1,
            build_s=1,
            test_s=1,
        ),
        llm=None,
        order=ORDER,
        source="cpp11",
        target="cpp20",
        run_id=RUN_ID,
        artifacts=artifacts,
        index_path=None,
        build_files=["CMakeLists.txt"],
        scope_dirs=["src", "include"],
        settings=AgentSettings(max_retry_budget=budget, max_edit_retries=edit_retries),
    )
    return HostUnit(runner, [TYPEDEFS], HostFiles(artifacts.root), language="cpp", author=AUTHOR)


def _start(host: HostUnit, *files: str) -> HostStep:
    head = host.worktree.head()
    now = datetime.now(UTC)
    settings = host.runner.settings
    state = HostUnitState(
        run_id=RUN_ID,
        config_path="/project/migration_config.yaml",
        worktree=str(host.worktree.path),
        sandbox="native",
        base_commit=head,
        unit=unit(*files),
        source="cpp11",
        target="cpp20",
        snapshot=head,
        head=head,
        configured_standard="cpp11",
        max_retry_budget=settings.max_retry_budget,
        max_edit_retries=settings.max_edit_retries,
        status=HostStatus.WAITING,
        started_at=now,
        updated_at=now,
    )
    return host.start(state)


def _submit(
    host: HostUnit, state: HostUnitState, *edits: dict[str, str], **extra: object
) -> HostStep:
    body = {"edits": list(edits), "summary": "Use alias declarations.", "review_notes": []}
    host.files.edits.write_text(json.dumps(body | extra), encoding="utf-8")
    return host.submit(state)


def _commits(git: GitRunner, world: World, since: str) -> list[str]:
    out = git(world.worktree.path, "log", "--format=%B%x00", f"{since}..HEAD")
    return [m.strip() for m in out.split("\0") if m.strip()]


def test_codemods_alone_finish_the_unit(world: World, git: GitRunner) -> None:
    (world.worktree.path / "src" / "geo.cpp").write_text("typedef int Count;\n", encoding="utf-8")
    world.worktree.commit("a simple typedef")
    snapshot = world.worktree.head()
    step = _start(_host(world), "src/geo.cpp")
    state = step.state
    assert state.status is HostStatus.SUCCEEDED
    assert state.agent_skipped
    assert state.changed_files == ["src/geo.cpp"]
    (message,) = _commits(git, world, snapshot)
    trailers = parse_trailers(message)
    assert trailers["CodeCoreMorph-Attempts"] == "0"
    assert trailers["CodeCoreMorph-Models"] == "none"
    assert "succeeded" in step.message
    assert world.read("src/geo.cpp") == "using Count = int;\n"
    assert not world.worktree.has_changes()


def test_the_host_reads_the_context_and_submits(world: World, git: GitRunner) -> None:
    snapshot = world.worktree.head()
    host = _host(world)
    step = _start(host, "include/geo.h")
    state = step.state
    assert state.status is HostStatus.WAITING
    assert (state.attempt, state.attempts_allowed) == (1, 3)
    assert state.head != snapshot  # the codemods' changes are a checkpoint
    assert not world.worktree.has_changes()
    assert str(host.files.edits) in step.message

    context = host.files.context.read_text(encoding="utf-8")
    assert context.startswith("# CodeCoreMorph work unit U-0001: attempt 1 of 3")
    assert "codecoremorph unit submit" in context
    assert str(host.files.edits) in context
    assert "## Rules of this unit" in context
    assert "### replace_typedefs_with_using (api_preserving)" in context
    assert "using Scalar = double;" in context  # the file after codemods
    assert "include/geo.h:3:" in context  # the site left for the agent
    assert "submit_edits" not in context  # host instructions, not the API agent's
    saved = StateDir(world.tmp / "state").run(RUN_ID).attempt("U-0001", 1).root / "context.md"
    assert saved.read_text(encoding="utf-8") == context

    done = _submit(host, state, replace_edit("include/geo.h", MACRO, FIXED), review_notes=["ok"])
    final = done.state
    assert final.status is HostStatus.SUCCEEDED, done.message
    assert [a.outcome for a in final.attempts] == [AttemptOutcome.SUCCEEDED]
    assert final.attempts[0].model == "host"
    assert final.review_notes == ["ok"]
    assert final.changed_files == ["include/geo.h"]
    (message,) = _commits(git, world, snapshot)  # the checkpoints are squashed
    assert message.startswith("codecoremorph(replace_typedefs_with_using): include/geo.h")
    assert "Use alias declarations." in message
    trailers = parse_trailers(message)
    assert trailers["CodeCoreMorph-Models"] == "host"
    assert trailers["CodeCoreMorph-Attempts"] == "1"
    text = world.read("include/geo.h")
    assert FIXED in text
    assert "using Scalar = double;" in text
    assert host.files.load() == final
    assert not host.files.edits.exists()
    kept = StateDir(world.tmp / "state").run(RUN_ID).attempt("U-0001", 1).root / "edits-1.json"
    assert MACRO in kept.read_text(encoding="utf-8")


def test_a_failed_build_is_fed_back_and_fixed(world: World, git: GitRunner) -> None:
    snapshot = world.worktree.head()
    host = _host(world)
    state = _start(host, "include/geo.h").state
    broken = "#define DECLARE(T, N) using N = BROKEN"
    step = _submit(host, state, replace_edit("include/geo.h", MACRO, broken))
    state = step.state
    assert state.status is HostStatus.WAITING
    assert (state.attempt, state.feedback_kind) == (2, HostFeedbackKind.FAILED)
    assert [a.outcome for a in state.attempts] == [AttemptOutcome.BUILD_FAILED]
    assert "The build failed with 1 error." in step.message
    assert "Attempt 2 of 3" in step.message
    context = host.files.context.read_text(encoding="utf-8")
    assert "Attempt 1 was applied and failed" in context
    assert broken in context  # the files show the earlier edits
    assert not world.worktree.has_changes()

    done = _submit(host, state, replace_edit("include/geo.h", "using N = BROKEN", "using N = T"))
    assert done.state.status is HostStatus.SUCCEEDED, done.message
    assert [a.role for a in done.state.attempts] == [Role.EDITOR, Role.FIXER]
    (message,) = _commits(git, world, snapshot)
    assert parse_trailers(message)["CodeCoreMorph-Attempts"] == "2"
    assert FIXED in world.read("include/geo.h")


def test_rejections_stay_in_the_attempt_until_its_retries_run_out(world: World) -> None:
    host = _host(world, edit_retries=1)
    state = _start(host, "include/geo.h").state
    calls = len(world.adapter.calls)
    step = _submit(host, state, replace_edit("src/other.cpp", "return 1", "return 2"))
    state = step.state
    assert (state.attempt, state.edit_rejections) == (1, 1)
    assert state.feedback_kind is HostFeedbackKind.REJECTED
    assert "outside the unit" in step.message
    assert "1 of 2 tries of this attempt used" in host.files.context.read_text(encoding="utf-8")
    assert world.read("src/other.cpp") == "int other() { return 1; }\n"

    host.files.edits.write_text('{"edits": [', encoding="utf-8")
    step = host.submit(state)
    state = step.state
    assert (state.attempt, state.edit_rejections) == (2, 0)
    assert [a.outcome for a in state.attempts] == [AttemptOutcome.EDITS_REJECTED]
    assert "doesn't match the format" in (state.feedback or "")
    assert len(world.adapter.calls) == calls  # rejections never build


def test_an_exhausted_budget_rolls_back(world: World) -> None:
    host = _host(world, budget=1)
    before = world.read("include/geo.h")
    snapshot = world.worktree.head()
    state = _start(host, "include/geo.h").state
    state = _submit(host, state, replace_edit("include/geo.h", MACRO, FIXED + " // REGRESS")).state
    step = _submit(host, state, replace_edit("include/geo.h", "// REGRESS", "// REGRESS again"))
    state = step.state
    assert state.status is HostStatus.FAILED
    assert [a.outcome for a in state.attempts] == [AttemptOutcome.TESTS_FAILED] * 2
    assert "retry budget (1) ran out" in (state.reason or "")
    assert "gtest:Geo.Area" in step.message
    assert world.worktree.head() == snapshot
    assert world.read("include/geo.h") == before
    with pytest.raises(UsageError, match="has already ended"):
        _submit(host, state, replace_edit("include/geo.h", MACRO, FIXED))


def test_changes_outside_codecoremorph_are_discarded(world: World, git: GitRunner) -> None:
    host = _host(world)
    state = _start(host, "include/geo.h").state
    # The host edits the worktree directly and even commits there: neither counts.
    (world.worktree.path / "src" / "other.cpp").write_text("int other() { return 9; }\n")
    git(world.worktree.path, "commit", "--quiet", "--all", "-m", "sneaky")
    (world.worktree.path / "include" / "new.h").write_text("#pragma once\n")
    step = _submit(host, state, replace_edit("include/geo.h", MACRO, FIXED))
    assert step.state.status is HostStatus.SUCCEEDED, step.message
    assert any("were discarded" in w and "src/other.cpp" in w for w in step.warnings)
    assert world.read("src/other.cpp") == "int other() { return 1; }\n"
    assert not (world.worktree.path / "include" / "new.h").exists()
    assert step.state.changed_files == ["include/geo.h"]


def test_a_crash_during_verification_leaves_the_attempt_to_rerun(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(world)
    state = _start(host, "include/geo.h").state
    head = world.worktree.head()

    def crash(*args: object) -> None:
        raise OSError("the sandbox went away")

    monkeypatch.setattr(world.adapter, "build", crash)
    with pytest.raises(OSError, match="sandbox went away"):
        _submit(host, state, replace_edit("include/geo.h", MACRO, FIXED))
    assert world.worktree.head() == head
    assert not world.worktree.has_changes()  # the applied edits were rolled back
    assert MACRO in host.files.edits.read_text(encoding="utf-8")  # ready to submit again
    monkeypatch.undo()
    step = host.submit(host.files.load())
    assert step.state.status is HostStatus.SUCCEEDED, step.message
    assert step.warnings == []


def test_submit_needs_an_edits_file(world: World) -> None:
    host = _host(world)
    state = _start(host, "include/geo.h").state
    with pytest.raises(UsageError, match="no edits file"):
        host.submit(state)
    assert host.files.load().status is HostStatus.WAITING


def test_the_context_document_nests_the_unit_message(world: World) -> None:
    host = _host(world)
    state = _start(host, "include/geo.h").state
    context = host.context(state)
    headings = [line for line in context.splitlines() if line.startswith("#")]
    assert headings[0].startswith("# CodeCoreMorph work unit")
    assert all(not h.startswith("# ") for h in headings[1:]), headings
    assert "## Work unit U-0001" in headings
    assert "#### include/geo.h" in headings
    assert "#pragma once" in context  # file text inside a fence is never re-headed
