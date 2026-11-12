"""Whole C# → C++ ports with the real toolchains (Phase 17, M11, D-78, D-79): each C# fixture's
port, every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/csharp/edits/<fixture>-cpp.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits, and its calls
are recorded (``pytest --record-cassettes``) and replayed like the others
(``run-all-<fixture>-cpp-docker.json``). The run uses the image for ports from C#, which holds
the C++ toolchain and the .NET SDK, so these runs need ``codecoremorph-csharp-port:local`` and
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
from .test_csharp_e2e import BUILD_OUTPUT, short_home  # noqa: F401  # pyright: ignore[reportUnusedImport]
from .test_run_e2e import _checkout, _report  # pyright: ignore[reportPrivateUsage]

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "csharp"
CASSETTES = ROOT / "tests" / "cassettes" / "csharp"
EDITS = CASSETTES / "edits"
SUFFIX = "-cpp"

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging", "short_home")]


def _manifest(name: str, source: str, target: str, rules: list[str], keep: bool) -> str:
    kept = ", keep_source_api: true" if keep else ""
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: [src]
  test_dirs: [tests]
migration:
  source: {{language: csharp, standard: {source}, build_system: dotnet}}
  target: {{language: cpp, standard: {target}, rules: [{", ".join(rules)}]{kept}}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: docker}}
approvals: {{plan: auto}}
"""


def _edits_files() -> list[str]:
    if not EDITS.is_dir():
        return []
    return sorted(p.stem for p in EDITS.glob(f"*{SUFFIX}.json"))


@pytest.mark.slow
@pytest.mark.parametrize("edits", _edits_files())
def test_every_unit_of_a_csharp_port_to_cpp(
    edits: str, tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> None:
    """The fixture's port to C++: every unit committed as recorded, in plan order, the user's
    checkout untouched, the C# tests passing through the façades until the retirement, and the
    final verification a clean CMake build whose tests, each a translated C# test, pass under
    the sanitizers. No C#, project, solution or MSBuild file is left on the branch, nor the
    bridge; the C++, its CMake build and both maps are. With ``keep_source_api`` (an edits
    file's ``kept`` projects, D-79), only the programs go: the library stays a .NET library over
    the C++, its façades calling the bridge, and the final verification runs both builds, the
    C# tests too. Nothing under orchestrator/ knows C# or C++ (NFR-3.1)."""
    data: dict[str, Any] = json.loads((EDITS / f"{edits}.json").read_text(encoding="utf-8"))
    fixture = data["fixture"]
    kept: list[str] = data.get("kept", [])
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / data.get("source_fixture", fixture), root, ignore=BUILD_OUTPUT)
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    config = root / "migration_config.yaml"
    config.write_text(
        _manifest(fixture, data["source"], data["target"], data["rules"], bool(kept)),
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
    # C# tests' (none weaker): only the tests C++ can't have lack a translation, with a note.
    i4 = "I-4: Translated tests check less than their originals: "
    weaker = [
        item
        for u in report.units
        for warning in u.warnings
        if warning.startswith(i4)
        for item in re.split(r"; (?=dotnet:)", warning.removeprefix(i4).rstrip("."))
        if "has no translation" not in item
    ]
    assert weaker == []
    tracked = git(root, "ls-tree", "-r", "--name-only", result.branch).split()
    csharp = (".cs", ".csproj", ".sln", ".slnx", ".props", ".targets")
    projects = sorted(f for f in tracked if f.endswith(".csproj"))
    if kept:
        # The kept projects, and nothing of the programs; the bridge stays under the façades.
        assert projects == kept
        assert any(f.startswith("cpp/bridge/") for f in tracked)
        assert "SHARED" in git(root, "show", f"{result.branch}:cpp/CMakeLists.txt")
    else:
        # The branch is C++: no C#, no MSBuild, no bridge.
        assert not [f for f in tracked if f.endswith(csharp)]
        assert not [f for f in tracked if f.startswith("cpp/bridge/")]
        assert "SHARED" not in git(root, "show", f"{result.branch}:cpp/CMakeLists.txt")
    # The CMake build, the C++ and the maps of where each C# member and test went.
    assert {
        "CMakeLists.txt",
        "cpp/CMakeLists.txt",
        "cpp/symbol-map.toml",
        "cpp/test-map.toml",
        "cpp/tests/CMakeLists.txt",
        "cpp/include/codecoremorph_decimal.hpp",
        "cpp/include/codecoremorph_exceptions.hpp",
    } <= set(tracked)
    top = git(root, "show", f"{result.branch}:CMakeLists.txt")
    assert "CODECOREMORPH_SANITIZE" in top
    # Every baseline test is mapped: to the GoogleTest tests the final verification ran, or to
    # none with the note that says why.
    assert report.port is not None
    assert report.port.tests_mapped == data["tests"]
    assert sorted(report.port.tests_without_counterpart) == data["without_counterpart"]
