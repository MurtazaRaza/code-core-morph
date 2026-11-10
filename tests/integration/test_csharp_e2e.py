"""Whole C# runs with the real toolchain (Phase 10, M4, D-65): each C# fixture's migration,
every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/csharp/edits/<fixture>.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits (no edits, for
a unit whose last sites should stay, D-64), and its calls are recorded
(``pytest --record-cassettes``) and replayed like the others (``run-all-<fixture>-<sandbox>.json``).
Natively the run needs ``toolchains/csharp.env``; in Docker, ``codecoremorph-csharp:local``.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
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
FIXTURES = ROOT / "fixtures" / "csharp"
CASSETTES = ROOT / "tests" / "cassettes" / "csharp"
EDITS = CASSETTES / "edits"
# An IDE's C# extension builds the fixtures in place; its output isn't part of them.
BUILD_OUTPUT = shutil.ignore_patterns("bin", "obj")

pytestmark = pytest.mark.usefixtures("restore_logging")


def _manifest(name: str, source: str, target: str, rules: list[str], mode: str) -> str:
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: ["."]
  test_dirs: ["tests"]
migration:
  source: {{language: csharp, standard: {source}, build_system: dotnet}}
  target: {{language: csharp, standard: {target}, rules: [{", ".join(rules)}]}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: {mode}}}
approvals: {{plan: auto}}
"""


@pytest.fixture
def short_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A CODECOREMORPH_HOME with a short path. NuGet's package folder and the build mirror live
    in the run's cache under it, and their deepest files (a package's satellite resources,
    copied beside a test assembly) pass Windows' 260-character limit under pytest's temporary
    directory; MSBuild then can't find them."""
    home = Path(tempfile.mkdtemp(prefix="ccm"))
    monkeypatch.setenv("CODECOREMORPH_HOME", str(home))
    yield home
    remove_long_paths(home)


def remove_long_paths(path: Path) -> None:
    """Remove ``path``, NuGet's files past 260 characters too (it writes them through long-path
    APIs, which plain Windows paths can't delete)."""
    target = f"\\\\?\\{path.resolve()}" if sys.platform == "win32" else str(path)
    shutil.rmtree(target, ignore_errors=True)


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def sandbox(request: pytest.FixtureRequest, short_home: Path) -> str:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("csharp_toolchain_env")
        (short_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, short_home / "toolchains" / "csharp.env")
    return mode


def _edits_files() -> list[str]:
    """The upgrades' edits, named as their fixtures; a port's are ``<fixture>-<target>.json``
    (``test_csharp_to_cpp_e2e.py``)."""
    if not EDITS.is_dir():
        return []
    return sorted(p.stem for p in EDITS.glob("*.json") if (FIXTURES / p.stem).is_dir())


@pytest.mark.slow
@pytest.mark.parametrize("fixture", _edits_files())
def test_every_unit_of_a_csharp_fixture(
    fixture: str,
    sandbox: str,
    tmp_path: Path,
    record_cassettes: bool,
    git: GitRunner,
) -> None:
    """The fixture's migration: every unit committed as recorded, every commit on the branch in
    plan order, the user's checkout untouched, and the final verification a clean build whose
    tests pass. Nothing under orchestrator/ knows C# (NFR-3.1)."""
    data: dict[str, Any] = json.loads((EDITS / f"{fixture}.json").read_text(encoding="utf-8"))
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root, ignore=BUILD_OUTPUT)
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
    # The branch is at the target: no project builds below the target's framework or language,
    # and no BinaryFormatter is left.
    tracked = git(root, "ls-tree", "-r", "--name-only", result.branch).split()
    projects = [p for p in tracked if p.endswith((".csproj", ".props"))]
    for project in projects:
        text = git(root, "show", f"{result.branch}:{project}")
        assert not re.search(r"<LangVersion>7\.3<", text), project
        assert not re.search(r"<TargetFramework>(?:net6\.0|netcoreapp3\.1)<", text), project
    sources = [p for p in tracked if p.endswith(".cs")]
    assert not any("BinaryFormatter" in git(root, "show", f"{result.branch}:{p}") for p in sources)
