"""A whole run driven by a host agent through the CLI, with real toolchains (D-44).

The test plays Claude Code with the skill: ``run --host-agent`` parks at the plan gate,
``approve --exclude`` and ``resume`` continue to the first unit that needs edits, and
``unit submit`` verifies the agent's edits and finishes the run. The edits are the ones the
scripted model writes in ``test_agent_e2e``; the builds, tests and commits are real, and no
model is called.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from codecoremorph.cli import app
from codecoremorph.core.models import RunReport, ToolCall
from codecoremorph.vcs.messages import parse_trailers

from .test_agent_e2e import OWNERSHIP

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "cpp"

pytestmark = pytest.mark.usefixtures("restore_logging")
runner = CliRunner()


def _agent_edits() -> list[dict[str, str]]:
    """The edits the scripted model submits for the cache unit."""
    for responder in OWNERSHIP.script:
        for block in getattr(responder, "content", []):
            if isinstance(block, ToolCall) and block.name == "submit_edits":
                edits = block.arguments["edits"]
                assert isinstance(edits, list)
                return [dict(e) for e in edits]  # type: ignore[arg-type]
    raise AssertionError("the OWNERSHIP script has no submit_edits call")


def _checkout_digest(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in (".git", ".codecoremorph"):
            found[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.config = root / "migration_config.yaml"

    def cli(self, *args: str) -> Result:
        return runner.invoke(app, list(args))

    def json(self, *args: str, code: int = 0) -> dict[str, Any]:
        result = self.cli(*args, "--json")
        assert result.exit_code == code, result.output
        return json.loads(result.stdout)


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def project(
    request: pytest.FixtureRequest, tmp_path: Path, git: GitRunner, isolated_home: Path
) -> Project:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("cpp_toolchain_env")
        (isolated_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, isolated_home / "toolchains" / "cpp.env")
    root = tmp_path / "ownership"
    shutil.copytree(FIXTURES / "ownership", root)
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    (root / "migration_config.yaml").write_text(
        f"""\
version: "1.0"
project:
  name: ownership
  source_dirs: [src, include, app]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {{language: cpp, standard: cpp11, build_system: cmake}}
  target: {{language: cpp, standard: cpp20, rules: [convert_raw_pointers_to_smart_pointers]}}
parity: {{baseline_runs: 1, characterization_tests: {{enabled: false}}}}
sandbox: {{mode: {mode}}}
approvals: {{plan: required}}
""",
        encoding="utf-8",
    )
    return Project(root)


def test_a_host_agent_drives_a_whole_run(project: Project, git: GitRunner) -> None:
    root, config = project.root, str(project.config)
    before = _checkout_digest(root)

    # 1. The run plans, then parks at the plan gate: the agent asks the user.
    started = project.json("run", config, "--host-agent")
    assert started["awaiting"]
    assert started["waiting"]["gate"] == "plan"
    assert "codecoremorph approve" in started["next"]
    run_id = started["run_id"]

    # 2. The user keeps the bump and the cache unit.
    approved = project.cli(
        "approve", run_id, "-c", config, "--exclude", "U-0003,U-0004,U-0005,U-0006"
    )
    assert approved.exit_code == 0, approved.output

    # 3. The codemods finish the bump; the cache unit waits for the agent's edits.
    waiting = project.json("resume", run_id, "-c", config)
    assert waiting["awaiting"]
    request = waiting["waiting"]
    assert (request["gate"], request["unit"], request["attempt"]) == ("agent", "U-0002", 1)
    assert any("U-0001" in note and "commit" in note for note in waiting["notes"])
    context = Path(request["context_file"]).read_text(encoding="utf-8")
    assert "include/inventory/cache.h" in context
    status = project.json("unit", "status", "-c", config)
    assert (status["status"], status["unit"]["id"]) == ("waiting_for_agent", "U-0002")
    read = project.cli("unit", "read", "include/inventory/cache.h", "-c", config)
    assert read.exit_code == 0, read.output
    assert "std::map<std::string, Node*> index_;" in read.stdout
    run_status = project.json("status", run_id, "-c", config)
    assert run_status["agent"]["unit"] == "U-0002"

    # 4. The agent writes its edits and submits; the run verifies them and ends.
    body = {"edits": _agent_edits(), "summary": "PriceCache owns its nodes.", "review_notes": []}
    Path(request["edits_file"]).write_text(json.dumps(body), encoding="utf-8")
    done = project.json("unit", "submit", "-c", config, code=1)  # four units were excluded
    assert done["status"] == "completed_with_failures", done
    assert not done["parked"]
    assert any("U-0002 succeeded" in note for note in done["notes"])

    report = RunReport.model_validate_json(Path(done["report"]).with_suffix(".json").read_bytes())
    assert {u.unit_id: u.status.value for u in report.units} == {
        "U-0001": "committed",
        "U-0002": "committed",
    }
    assert report.unit_counts["excluded"] == 4
    assert report.usage_total.calls == 0
    assert report.final is not None
    assert report.final.passed, report.final
    branch = done["branch"]
    commits = git(root, "rev-list", "--reverse", f"main..{branch}").split()
    trailers = [parse_trailers(git(root, "log", "-1", "--format=%B", c)) for c in commits]
    assert [t["CodeCoreMorph-Unit"] for t in trailers] == ["U-0001", "U-0002"]
    assert [t["CodeCoreMorph-Models"] for t in trailers] == ["none", "host"]
    assert "std::unique_ptr<Node>" in git(root, "show", f"{branch}:include/inventory/cache.h")
    # The user's checkout is untouched, and the run released the repository.
    assert _checkout_digest(root) == before
    assert not (root / ".codecoremorph" / "lock").exists()
