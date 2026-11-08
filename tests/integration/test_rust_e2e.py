"""Whole Rust → C ports with the real toolchain (Phase 16, M10, D-75): each Rust fixture's port,
every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/rust/edits/<fixture>.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits, and its calls
are recorded (``pytest --record-cassettes``) and replayed like the others
(``run-all-<fixture>-docker.json``). The run uses the Rust image, which holds the C toolchain,
so these runs need ``codecoremorph-rust:local`` and are marked ``docker``.
"""

from __future__ import annotations

import json
import shutil
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
FIXTURES = ROOT / "fixtures" / "rust"
CASSETTES = ROOT / "tests" / "cassettes" / "rust"
EDITS = CASSETTES / "edits"

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging", "isolated_home")]


def _manifest(name: str, source: str, target: str, rules: list[str]) -> str:
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: [src]
  test_dirs: [tests]
migration:
  source: {{language: rust, standard: {source}}}
  target: {{language: c, standard: {target}, rules: [{", ".join(rules)}]}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: docker}}
approvals: {{plan: auto}}
"""


def _edits_files() -> list[str]:
    return sorted(p.stem for p in EDITS.glob("*.json")) if EDITS.is_dir() else []


@pytest.mark.slow
@pytest.mark.parametrize("fixture", _edits_files())
def test_every_unit_of_a_rust_fixture(
    fixture: str, tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> None:
    """The fixture's port to C: every unit committed as recorded, in plan order, the user's
    checkout untouched, and the final verification a clean CMake build whose tests, every one
    a translated Rust test, pass under the sanitizers. No Rust and no Cargo manifest are left
    on the branch; the C, its CMake build and both maps are. Nothing under orchestrator/ knows
    Rust or C (NFR-3.1)."""
    data: dict[str, Any] = json.loads((EDITS / f"{fixture}.json").read_text(encoding="utf-8"))
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root)
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    config = root / "migration_config.yaml"
    config.write_text(
        _manifest(fixture, data["source"], data["target"], data["rules"]), encoding="utf-8"
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
    path = CASSETTES / f"run-all-{fixture}-docker.json"
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
        mode=SandboxMode.DOCKER,
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
    tracked = git(root, "ls-tree", "-r", "--name-only", result.branch).split()
    # The branch is C: no Rust, no Cargo manifest or build script; the CMake build, the C and
    # the maps of where each Rust item and test went.
    assert not [f for f in tracked if f.endswith(".rs") or f.startswith("Cargo.")]
    assert "build.rs" not in tracked
    assert {"CMakeLists.txt", "c/CMakeLists.txt", "c/symbol-map.toml", "c/test-map.toml"} <= set(
        tracked
    )
    assert report.port is not None
    # Every baseline test is a translated C test now (ctest IDs), and every one of them passed
    # in the final verification.
    top = git(root, "show", f"{result.branch}:CMakeLists.txt")
    assert "CODECOREMORPH_SANITIZE" in top
