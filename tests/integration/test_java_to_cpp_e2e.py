"""Whole Java → C++ ports with the real toolchains (Phase 20, M14, D-91, D-92): each Java
fixture's port, every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/java/edits/<fixture>-cpp.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits, and its calls
are recorded (``pytest --record-cassettes``) and replayed like the others
(``run-all-<fixture>-cpp-docker.json``). The run uses the image for ports from Java, which holds
the C++ toolchain, the JDKs and Maven, so these runs need ``codecoremorph-java-port:local`` and
are marked ``docker``.
"""

from __future__ import annotations

import json
import re
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
FIXTURES = ROOT / "fixtures" / "java"
CASSETTES = ROOT / "tests" / "cassettes" / "java"
EDITS = CASSETTES / "edits"
SUFFIX = "-cpp"
# What a Maven build leaves in a fixture's copy.
BUILD_OUTPUT = shutil.ignore_patterns("target")

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging")]


def _manifest(name: str, source: str, target: dict[str, Any], keep: bool) -> str:
    kept = ", keep_source_api: true" if keep else ""
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: ["."]
  test_dirs: ["**/src/test"]
migration:
  source: {{language: java, standard: {source}, build_system: maven}}
  target: {{language: {target["language"]}, standard: {target["standard"]}, rules: [{
        ", ".join(target["rules"])
    }]{kept}}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: docker}}
approvals: {{plan: auto}}
"""


def _edits_files(suffix: str) -> list[str]:
    if not EDITS.is_dir():
        return []
    return sorted(p.stem for p in EDITS.glob(f"*{suffix}.json"))


def run_port(
    edits: str, tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> tuple[dict[str, Any], Any, Any, Path]:
    """Port the fixture an edits file names, its agent replies scripted from the edits: the
    edits file's data, the run's result and report, and the fixture's copy (a git repo)."""
    data: dict[str, Any] = json.loads((EDITS / f"{edits}.json").read_text(encoding="utf-8"))
    fixture = data["fixture"]
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / data.get("source_fixture", fixture), root, ignore=BUILD_OUTPUT)
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    config = root / "migration_config.yaml"
    target = {
        "language": data["target_language"],
        "standard": data["target"],
        "rules": data["rules"],
    }
    config.write_text(
        _manifest(fixture, data["source"], target, bool(data.get("kept"))), encoding="utf-8"
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
    path = CASSETTES / f"run-all-{edits}-docker.json"
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
    # Every unit kept the branch green, its I-4 comparing the translated tests' checks with the
    # Java tests' (none weaker): only the tests the target can't have lack a translation, with a
    # note.
    i4 = "I-4: Translated tests check less than their originals: "
    weaker = [
        item
        for u in report.units
        for warning in u.warnings
        if warning.startswith(i4)
        for item in re.split(r"; (?=junit:)", warning.removeprefix(i4).rstrip("."))
        if "has no translation" not in item
    ]
    assert weaker == []
    # Every baseline test is mapped: to the target's tests the final verification ran, or to
    # none with the note that says why.
    assert report.port is not None
    assert report.port.tests_mapped == data["tests"]
    assert sorted(report.port.tests_without_counterpart) == data["without_counterpart"]
    return data, result, report, root


@pytest.mark.slow
@pytest.mark.parametrize("edits", _edits_files(SUFFIX))
def test_every_unit_of_a_java_port_to_cpp(
    edits: str, tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> None:
    """The fixture's port to C++: every unit committed as recorded, in plan order, the user's
    checkout untouched, the Java tests passing through the façades (the C++ under the
    sanitizers inside Surefire's JVM) until the retirement, and the final verification a clean
    CMake build whose tests, each a translated Java test, pass under the sanitizers. No Java
    source, POM or façade support is left on the branch, nor the bridge and its JNI glue; the
    C++, its CMake build and both maps are. With ``keep_source_api`` (an edits file's ``kept``
    POMs), the library stays a Java library over the C++, its façades calling the bridge, and
    the final verification runs both builds, the Java tests too. Nothing under orchestrator/
    knows Java or C++ (NFR-3.1)."""
    data, result, _, root = run_port(edits, tmp_path, record_cassettes, git)
    tracked = git(root, "ls-tree", "-r", "--name-only", result.branch).split()
    if data.get("kept"):
        # The POMs, the façades and their support, and the bridge with its JNI glue under them.
        assert sorted(f for f in tracked if f.endswith("pom.xml")) == data["kept"]
        assert any(f.endswith("/codecoremorph/interop/CcmNative.java") for f in tracked)
        assert "cpp/bridge/codecoremorph_jni.g.c" in tracked
        assert "SHARED" in git(root, "show", f"{result.branch}:cpp/CMakeLists.txt")
    else:
        # The branch is C++: no Java, no Maven, no bridge.
        assert not [f for f in tracked if f.endswith((".java", "pom.xml"))]
        assert not [f for f in tracked if f.startswith("cpp/bridge/")]
        assert "SHARED" not in git(root, "show", f"{result.branch}:cpp/CMakeLists.txt")
    # The CMake build, the C++, the runtime the C++ keeps, and the maps of where each Java
    # member and test went.
    assert {
        "CMakeLists.txt",
        "cpp/CMakeLists.txt",
        "cpp/symbol-map.toml",
        "cpp/test-map.toml",
        "cpp/tests/CMakeLists.txt",
        "cpp/include/codecoremorph_exceptions.hpp",
        "cpp/include/codecoremorph_java.hpp",
    } <= set(tracked)
    top = git(root, "show", f"{result.branch}:CMakeLists.txt")
    assert "CODECOREMORPH_SANITIZE" in top
