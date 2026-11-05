"""Whole Java runs with the real toolchain (Phase 9, M3, D-63): each Java fixture's 8 → 21
migration, every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/java/edits/<fixture>.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits, and its
calls are recorded (``pytest --record-cassettes``) and replayed like the others
(``run-all-<fixture>-<sandbox>.json``). Natively the run needs ``toolchains/java.env``; in
Docker, ``codecoremorph-java:local``.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.config.models import SandboxMode
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Cassette, RecordingProvider, ReplayProvider, ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.lifecycle import start_run
from codecoremorph.orchestrator.run import RunOptions
from codecoremorph.vcs.messages import parse_trailers

from .test_agent_e2e import reply as agent_reply
from .test_agent_e2e import submit
from .test_run_e2e import _checkout, _report  # pyright: ignore[reportPrivateUsage]

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "java"
CASSETTES = ROOT / "tests" / "cassettes" / "java"
EDITS = CASSETTES / "edits"

pytestmark = pytest.mark.usefixtures("restore_logging")


def _manifest(name: str, source: str, target: str, rules: list[str], mode: str) -> str:
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: ["."]
  test_dirs: ["**/src/test"]
migration:
  source: {{language: java, standard: {source}, build_system: maven}}
  target: {{language: java, standard: {target}, rules: [{", ".join(rules)}]}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: {mode}}}
approvals: {{plan: auto}}
"""


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def sandbox(request: pytest.FixtureRequest, isolated_home: Path) -> str:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("java_toolchain_env")
        (isolated_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, isolated_home / "toolchains" / "java.env")
    return mode


def _edits_files() -> list[str]:
    return sorted(p.stem for p in EDITS.glob("*.json")) if EDITS.is_dir() else []


@pytest.mark.slow
@pytest.mark.parametrize("fixture", _edits_files())
def test_every_unit_of_a_java_fixture(
    fixture: str,
    sandbox: str,
    tmp_path: Path,
    record_cassettes: bool,
    git: GitRunner,
) -> None:
    """The fixture's 8 → 21 migration: every unit committed as recorded, every commit on the
    branch in plan order, the user's checkout untouched, and the final verification a clean
    Maven build whose tests pass. Nothing under orchestrator/ knows Java (NFR-3.1)."""
    data: dict[str, Any] = json.loads((EDITS / f"{fixture}.json").read_text(encoding="utf-8"))
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root)
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    config = root / "migration_config.yaml"
    config.write_text(
        _manifest(fixture, data["source"], data["target"], data["rules"], sandbox),
        encoding="utf-8",
    )
    git(root, "add", "migration_config.yaml")
    git(root, "commit", "--quiet", "-m", "manifest")

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
    if record_cassettes:
        provider = RecordingProvider(ScriptedProvider(script), Cassette(path))
    else:
        if not path.is_file():
            pytest.skip(f"no cassette {path.name}; record it with --record-cassettes")
        provider = ReplayProvider(Cassette.load(path), "anthropic")

    def factory(ledger: UsageLedger) -> LLMService:
        from codecoremorph.config import load_config

        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": provider})

    checkout = _checkout(root, git)
    result = start_run(
        config,
        base=None,
        mode=SandboxMode(sandbox),
        options=RunOptions(yes=True),
        llm_factory=factory,
    )
    assert _checkout(root, git) == checkout
    report = _report(config, result.run_id)
    assert report.unit_counts == data["unit_counts"], report.review
    assert result.status.value == data["status"], result.reason
    assert report.final is not None
    assert report.final.passed, report.final
    assert report.final.clean
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0
    commits = git(root, "rev-list", "--reverse", f"main..{result.branch}").split()
    units = [
        parse_trailers(git(root, "log", "-1", "--format=%B", c))["CodeCoreMorph-Unit"]
        for c in commits
    ]
    assert units == sorted(u.unit_id for u in report.units if u.commit is not None)
    # The branch is at the target: the POMs say 21, and no removed API is left.
    pom = git(root, "show", f"{result.branch}:pom.xml")
    assert "1.8" not in pom
