"""Whole runs with real toolchains (Phase 5, M1): ``start_run`` from the manifest to the report,
with replayed model calls.

- ``typedefs``: the deterministic tools complete the migration without a model. The user's
  checkout is byte-identical afterwards (NFR-2.1), and every commit of the branch builds and
  passes the tests when it is replayed.
- ``ownership`` to C++20: the standard bump, then the ``cache`` unit with scripted model edits;
  the plan gate excludes the other units. ``pytest --record-cassettes`` records the model calls
  (per sandbox, because compiler messages differ); a normal run replays them.
- Every fixture's designed migration, every unit included (marked ``slow``). The agent's edits
  come from ``tests/cassettes/cpp/edits/<fixture>.json``, written from a host-agent run
  (D-44, D-45): a scripted model submits each unit's edits, and its calls are recorded and
  replayed like the others (``run-all-<fixture>-<sandbox>.json``).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.config.models import SandboxMode
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    RunReport,
    RunStatus,
    UnitStatus,
)
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Cassette, RecordingProvider, ReplayProvider, ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.lifecycle import RunPlace, start_run
from codecoremorph.orchestrator.run import RunOptions, RunResult
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.messages import parse_trailers

from .test_agent_e2e import OWNERSHIP, submit
from .test_agent_e2e import reply as agent_reply

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "cpp"
CASSETTES = ROOT / "tests" / "cassettes" / "cpp"

pytestmark = pytest.mark.usefixtures("restore_logging")


def _manifest(
    name: str,
    target: str,
    rules: str,
    mode: str,
    plan: str,
    source: str = "cpp11",
    *,
    characterization: bool = False,
) -> str:
    """The run's manifest. Characterization is off unless asked for: these runs replay the
    migration's model turns (D-45), and the untested fixture's own test turns on (D-46)."""
    characterize = "true" if characterization else "false"
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: [src, include, app]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {{language: cpp, standard: {source}, build_system: cmake}}
  target: {{language: cpp, standard: {target}, rules: [{rules}]}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: {characterize}}}}}
sandbox: {{mode: {mode}}}
approvals: {{plan: {plan}}}
"""


def _checkout_digest(root: Path) -> dict[str, str]:
    """Every file of the user's checkout outside .git and .codecoremorph, by content."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in (".git", ".codecoremorph"):
            found[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


Setup = Callable[..., Path]


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def sandbox(request: pytest.FixtureRequest, isolated_home: Path) -> str:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("cpp_toolchain_env")
        (isolated_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, isolated_home / "toolchains" / "cpp.env")
    return mode


@pytest.fixture
def project(tmp_path: Path, git: GitRunner) -> Setup:
    def setup(
        fixture: str,
        target: str,
        rules: str,
        mode: str,
        plan: str = "auto",
        source: str = "cpp11",
        *,
        characterization: bool = False,
    ) -> Path:
        root = tmp_path / fixture
        shutil.copytree(FIXTURES / fixture, root)
        git(root.parent, "init", "--quiet", str(root))
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "-m", "legacy")
        config = root / "migration_config.yaml"
        text = _manifest(
            fixture, target, rules, mode, plan, source, characterization=characterization
        )
        config.write_text(text, encoding="utf-8")
        return config

    return setup


def _report(config: Path, run_id: str) -> RunReport:
    path = StateDir.for_repo(config.parent).run(run_id).report_json
    return RunReport.model_validate_json(path.read_bytes())


def test_typedefs_run_without_a_model(project: Setup, sandbox: str, git: GitRunner) -> None:
    config = project("typedefs", "cpp11", "replace_typedefs_with_using", sandbox)
    root = config.parent
    before = _checkout_digest(root)
    head = git(root, "rev-parse", "HEAD").strip()
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/main")

    scripted = ScriptedProvider([])

    def factory(ledger: UsageLedger) -> LLMService:
        from codecoremorph.config import load_config

        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": scripted})

    result = start_run(
        config, base=None, mode=SandboxMode(sandbox), options=RunOptions(), llm_factory=factory
    )
    assert result.status is RunStatus.COMPLETED, result.reason
    assert scripted.requests == []
    report = _report(config, result.run_id)
    assert report.final is not None
    assert report.final.passed
    assert report.final.clean
    assert report.usage_total.calls == 0
    assert all(u.agent_skipped for u in report.units)
    # The user's checkout, HEAD and branch are untouched (NFR-2.1).
    assert _checkout_digest(root) == before
    assert git(root, "rev-parse", "HEAD").strip() == head
    assert git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/main") == refs
    commits = git(root, "rev-list", "--reverse", f"main..{result.branch}").split()
    assert len(commits) == len(report.units)
    message = git(root, "log", "-1", "--format=%B", commits[0])
    assert parse_trailers(message)["CodeCoreMorph-Run"] == result.run_id
    migrated = git(root, "show", f"{result.branch}:include/geo/types.h")
    assert "\ntypedef " not in migrated
    assert "using " in migrated
    assert "\ntypedef " in (root / "include" / "geo" / "types.h").read_text(encoding="utf-8")


def test_ownership_bump_and_one_unit_with_the_agent(
    project: Setup, sandbox: str, record_cassettes: bool, git: GitRunner
) -> None:
    config = project(
        "ownership", "cpp20", "convert_raw_pointers_to_smart_pointers", sandbox, plan="required"
    )
    flavor = "docker" if sandbox == "docker" else f"native-{sys.platform}"
    path = CASSETTES / f"run-ownership-{flavor}.json"
    provider: Any
    if record_cassettes:
        provider = RecordingProvider(ScriptedProvider(list(OWNERSHIP.script)), Cassette(path))
    else:
        if not path.is_file():
            pytest.skip(f"no cassette {path.name}; record it with --record-cassettes")
        provider = ReplayProvider(Cassette.load(path), "anthropic")
    asked: list[dict[str, Any]] = []

    def approve_two(request: dict[str, Any]) -> ApprovalRecord:
        asked.append(request)
        return ApprovalRecord(
            run_id=str(request["run_id"]),
            gate=ApprovalGate.PLAN,
            decision=ApprovalDecision.APPROVE,
            exclude=["U-0003", "U-0004", "U-0005", "U-0006"],
            source="test",
            created_at=datetime.now(UTC),
        )

    def factory(ledger: UsageLedger) -> LLMService:
        from codecoremorph.config import load_config

        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": provider})

    result: RunResult = start_run(
        config,
        base=None,
        mode=SandboxMode(sandbox),
        options=RunOptions(ask=approve_two),
        llm_factory=factory,
    )
    assert [r["gate"] for r in asked] == ["plan"]
    assert result.status is RunStatus.COMPLETED_WITH_FAILURES, result.reason
    report = _report(config, result.run_id)
    statuses = {u.unit_id: u.status for u in report.units}
    assert statuses == {"U-0001": UnitStatus.COMMITTED, "U-0002": UnitStatus.COMMITTED}
    assert report.unit_counts["excluded"] == 4
    assert report.final is not None
    assert report.final.passed, report.final
    root = config.parent
    commits = git(root, "rev-list", "--reverse", f"main..{result.branch}").split()
    units = [
        parse_trailers(git(root, "log", "-1", "--format=%B", c))["CodeCoreMorph-Unit"]
        for c in commits
    ]
    assert units == ["U-0001", "U-0002"]
    assert "CMAKE_CXX_STANDARD 20" in git(root, "show", f"{commits[0]}:CMakeLists.txt")
    cache = git(root, "show", f"{commits[1]}:include/inventory/cache.h")
    assert "std::unique_ptr<Node>" in cache
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0
    place = RunPlace.for_config(config)
    with place.store() as store:
        assert {u.id: u.status for u in store.units(result.run_id)}[
            "U-0002"
        ] is UnitStatus.COMMITTED


EDITS = CASSETTES / "edits"


def _edits_files() -> list[str]:
    return sorted(p.stem for p in EDITS.glob("*.json")) if EDITS.is_dir() else []


@pytest.mark.slow
@pytest.mark.parametrize("fixture", _edits_files())
def test_every_unit_of_a_fixture(
    fixture: str, project: Setup, sandbox: str, record_cassettes: bool, git: GitRunner
) -> None:
    """The fixture's designed migration, every unit included (M1): each unit is committed or
    fails as recorded, and the final verification passes on a clean build. A fixture recorded
    with characterization (``untested``, D-46) first gets its characterization tests: the
    jobs' submissions come before the units'."""
    data: dict[str, Any] = json.loads((EDITS / f"{fixture}.json").read_text(encoding="utf-8"))
    characterization = bool(data.get("characterization", False))
    config = project(
        fixture,
        data["target"],
        ", ".join(data["rules"]),
        sandbox,
        source=data["source"],
        characterization=characterization,
    )
    script: list[Any] = [
        agent_reply(
            submit(f"toolu_{unit}_{n}", batch["edits"], batch["summary"], batch["review_notes"])
        )
        for unit, batches in data["submissions"].items()
        for n, batch in enumerate(batches, start=1)
    ]
    flavor = "docker" if sandbox == "docker" else f"native-{sys.platform}"
    path = CASSETTES / f"run-all-{fixture}-{flavor}.json"
    provider: Any
    if not script:
        provider = ScriptedProvider([])  # the codemods do everything: no model calls to replay
    elif record_cassettes:
        provider = RecordingProvider(ScriptedProvider(script), Cassette(path))
    else:
        if not path.is_file():
            pytest.skip(f"no cassette {path.name}; record it with --record-cassettes")
        provider = ReplayProvider(Cassette.load(path), "anthropic")

    def factory(ledger: UsageLedger) -> LLMService:
        from codecoremorph.config import load_config

        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": provider})

    result = start_run(
        config,
        base=None,
        mode=SandboxMode(sandbox),
        options=RunOptions(yes=True),
        llm_factory=factory,
    )
    report = _report(config, result.run_id)
    assert report.unit_counts == data["unit_counts"], report.review
    assert result.status.value == data["status"], result.reason
    assert report.final is not None
    assert report.final.passed, report.final
    assert report.final.clean
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0
    # Every unit's commit is on the branch, in plan order, after the characterization commit.
    root = config.parent
    commits = git(root, "rev-list", "--reverse", f"main..{result.branch}").split()
    trailers = [parse_trailers(git(root, "log", "-1", "--format=%B", c)) for c in commits]
    if characterization:
        first = trailers.pop(0)
        tests = data["characterization_tests"]
        assert first["CodeCoreMorph-Characterize"] == f"{tests} tests"
        assert report.characterization is not None
        assert len(report.characterization.tests) == tests
    units = [t["CodeCoreMorph-Unit"] for t in trailers]
    # (A committed unit that needed no changes has no commit.)
    committed = sorted(u.unit_id for u in report.units if u.commit is not None)
    assert units == committed
