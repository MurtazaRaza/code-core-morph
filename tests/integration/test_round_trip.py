"""The round trip as an eval (Phase 16, D-75): ``fixtures/c/c-legacy`` ported to Rust, then that
Rust ported back to C, with every test of the original C still passing.

The first leg is Phase 15's recorded run of c-legacy (``tests/cassettes/c/``, replayed; its
cassette is recorded by ``test_c_e2e.py``). Its branch becomes a new repository: the Cargo
workspace under ``rust/`` with the library's crate and the program's, the C → Rust maps, and the
data files under ``tests/``. The second leg ports it to C from a host-agent run's recorded edits
(``tests/cassettes/rust/round-trip/c-legacy.json``), its calls recorded (``pytest
--record-cassettes``) and replayed like the others (``run-all-c-legacy-round-trip-docker.json``).

An original C test still passes when its Rust tests (``rust/test-map.toml``) each map to C tests
(``c/test-map.toml``) and those pass in the final verification, which checks every baseline
test's counterparts. Both legs run in the Rust image (``codecoremorph-rust:local``).
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.config.models import SandboxMode
from codecoremorph.invariants.test_map import by_source, read_test_map
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Cassette, RecordingProvider, ReplayProvider, ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.lifecycle import RunResult, start_run
from codecoremorph.orchestrator.run import RunOptions

from .test_agent_e2e import reply as agent_reply
from .test_agent_e2e import submit
from .test_run_e2e import _checkout, _report  # pyright: ignore[reportPrivateUsage]

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
LEG1_EDITS = ROOT / "tests" / "cassettes" / "c" / "edits" / "c-legacy.json"
LEG1_CASSETTE = ROOT / "tests" / "cassettes" / "c" / "run-all-c-legacy-docker.json"
LEG2_EDITS = ROOT / "tests" / "cassettes" / "rust" / "round-trip" / "c-legacy.json"
LEG2_CASSETTE = ROOT / "tests" / "cassettes" / "rust" / "run-all-c-legacy-round-trip-docker.json"

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging", "isolated_home")]

LEG1 = """\
version: "1.0"
project:
  name: c-legacy
  source_dirs: [src, include, tools]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {language: c, standard: c89, build_system: make}
  target: {language: rust, standard: rust2021, rules: [port_c_module, port_c_executable, port_c_tests, retire_c]}
parity: {baseline_runs: 1, characterization_tests: {enabled: false}}
sandbox: {mode: docker}
approvals: {plan: auto}
"""  # noqa: E501

LEG2 = """\
version: "1.0"
project:
  name: c-legacy-round-trip
  source_dirs: [rust]
  test_dirs: [rust/wf_rs/tests, rust/wordfreq_rs/tests]
migration:
  source: {language: rust, standard: rust2021}
  target: {language: c, standard: c99, rules: [port_rust_module, port_rust_program, port_rust_tests, retire_rust]}
parity: {baseline_runs: 1, characterization_tests: {enabled: false}}
sandbox: {mode: docker}
approvals: {plan: auto}
"""  # noqa: E501


def _script(data: dict[str, Any]) -> list[Any]:
    return [
        agent_reply(
            submit(f"toolu_{unit}_{n}", batch["edits"], batch["summary"], batch["review_notes"])
        )
        for unit, batches in data["submissions"].items()
        for n, batch in enumerate(batches, start=1)
    ]


def _leg(root: Path, manifest: str, provider: Any, git: GitRunner) -> RunResult:
    """One leg's run from ``root``'s main branch, with the manifest committed first."""
    config = root / "migration_config.yaml"
    config.write_text(manifest, encoding="utf-8")
    git(root, "add", "migration_config.yaml")
    git(root, "commit", "--quiet", "-m", "manifest")

    def factory(ledger: UsageLedger) -> LLMService:
        from codecoremorph.config import load_config

        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": provider})

    checkout = _checkout(root, git)
    result = start_run(
        config, base=None, mode=SandboxMode.DOCKER, options=RunOptions(yes=True),
        llm_factory=factory,
    )  # fmt: skip
    assert _checkout(root, git) == checkout
    return result


def _map(root: Path, git: GitRunner, branch: str, path: str, source: str, target: str) -> Any:
    entries, problems = read_test_map(git(root, "show", f"{branch}:{path}"), source, target)
    assert not problems
    return by_source(entries)


@pytest.mark.slow
def test_c_legacy_to_rust_and_back_to_c(
    tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> None:
    if not LEG1_CASSETTE.is_file():
        pytest.skip(f"no cassette {LEG1_CASSETTE.name}; record it with test_c_e2e.py")
    leg2: dict[str, Any] = json.loads(LEG2_EDITS.read_text(encoding="utf-8"))

    # Leg 1: C → Rust, Phase 15's recorded run.
    c_root = tmp_path / "c-legacy"
    shutil.copytree(ROOT / "fixtures" / "c" / "c-legacy", c_root)
    git(tmp_path, "init", "--quiet", str(c_root))
    git(c_root, "add", "--all")
    git(c_root, "commit", "--quiet", "-m", "legacy")
    leg1 = json.loads(LEG1_EDITS.read_text(encoding="utf-8"))
    assert leg1["rules"] == ["port_c_module", "port_c_executable", "port_c_tests", "retire_c"]
    replay1 = ReplayProvider(Cassette.load(LEG1_CASSETTE), "anthropic")
    first = _leg(c_root, LEG1, replay1, git)
    assert first.status.value == "completed", first.reason
    assert replay1.unused == 0

    # Its branch, a repository of its own: the Rust project the second leg starts from.
    rust_root = tmp_path / "c-legacy-round-trip"
    rust_root.mkdir()
    archive = subprocess.run(
        ["git", "archive", first.branch], cwd=c_root, capture_output=True, check=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(rust_root, filter="data")
    (rust_root / "migration_config.yaml").unlink()
    git(tmp_path, "init", "--quiet", str(rust_root))
    git(rust_root, "add", "--all")
    git(rust_root, "commit", "--quiet", "-m", "rust")

    # Leg 2: Rust → C, the host-agent run's recorded edits.
    provider: Any
    if record_cassettes:
        provider = RecordingProvider(ScriptedProvider(_script(leg2)), Cassette(LEG2_CASSETTE))
    else:
        if not LEG2_CASSETTE.is_file():
            pytest.skip(f"no cassette {LEG2_CASSETTE.name}; record it with --record-cassettes")
        provider = ReplayProvider(Cassette.load(LEG2_CASSETTE), "anthropic")
    second = _leg(rust_root, LEG2, provider, git)
    report = _report(rust_root / "migration_config.yaml", second.run_id)
    assert report.unit_counts == leg2["unit_counts"], report.review
    assert second.status.value == leg2["status"], second.reason
    assert report.final is not None
    assert report.final.passed, report.final
    assert report.final.clean
    assert not report.final.regressions
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0
    tracked = git(rust_root, "ls-tree", "-r", "--name-only", second.branch).split()
    cargo = [f for f in tracked if f.rsplit("/", 1)[-1] in ("Cargo.toml", "Cargo.lock")]
    assert not cargo
    assert not [f for f in tracked if f.endswith(".rs")]
    assert {"CMakeLists.txt", "c/CMakeLists.txt", "c/test-map.toml", "tests/sample.txt"} <= set(
        tracked
    )

    # Every original C test that Phase 15 gave Rust tests still has passing tests: its Rust
    # tests each map to C tests, which the final verification ran and passed.
    to_rust = _map(c_root, git, first.branch, "rust/test-map.toml", "c", "rust")
    to_c = _map(rust_root, git, second.branch, "c/test-map.toml", "rust", "c")
    kept: dict[str, set[str]] = {}
    for original, pair in to_rust.items():
        if not pair.targets:
            assert pair.note, original  # a C test with no Rust meaning (a NULL pointer, freeing)
            continue
        assert all(rust in to_c for rust in pair.targets), (original, pair.targets)
        kept[original] = {c for rust in pair.targets for c in to_c[rust].targets}
        assert kept[original], original
        assert all(c.startswith("ctest:") for c in kept[original])
    assert "make:test" in kept  # the program's output, compared with tests/sample*.out
    assert len(kept) >= 37
    assert report.final.tests_run >= len(to_c)
