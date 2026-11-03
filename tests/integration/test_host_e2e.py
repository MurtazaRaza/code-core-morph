"""A host agent drives one unit through the `codecoremorph unit` commands, with real toolchains
(D-41). The edits are the ones an agent would write; the build, tests and feedback are real.

In Docker mode this also checks that a parked run keeps its build between commands.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from codecoremorph.cli import app

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "cpp"
CACHE_H = "include/inventory/cache.h"
CACHE_CPP = "src/cache.cpp"
RULE = "convert_raw_pointers_to_smart_pointers"

pytestmark = pytest.mark.usefixtures("restore_logging")
runner = CliRunner()


def replace(path: str, search: str, new: str) -> dict[str, str]:
    return {"op": "replace", "path": path, "search": search, "replace": new}


HEADER_ONLY = [
    replace(CACHE_H, "#include <map>\n#include <string>",
            "#include <map>\n#include <memory>\n#include <string>"),
    replace(CACHE_H, "    std::map<std::string, Node*> index_;",
            "    std::map<std::string, std::unique_ptr<Node>> index_;  // owns the nodes"),
]  # fmt: skip
SOURCE = [
    replace(CACHE_CPP, '#include "inventory/cache.h"\n',
            '#include "inventory/cache.h"\n\n#include <utility>\n'),
    replace(CACHE_CPP,
            "PriceCache::~PriceCache() {\n    Node* node = head_;\n"
            "    while (node != NULL) {\n        Node* next = node->next;\n"
            "        delete node;\n        node = next;\n    }\n}",
            "PriceCache::~PriceCache() {}  // index_ owns the nodes"),
    replace(CACHE_CPP,
            "    std::map<std::string, Node*>::iterator it = index_.find(key);\n"
            "    if (it != index_.end()) {\n        it->second->price = price;\n"
            "        unlink(it->second);\n        pushFront(it->second);",
            "    std::map<std::string, std::unique_ptr<Node>>::iterator it = index_.find(key);\n"
            "    if (it != index_.end()) {\n        it->second->price = price;\n"
            "        unlink(it->second.get());\n        pushFront(it->second.get());"),
    replace(CACHE_CPP, "        index_.erase(victim->key);\n        delete victim;",
            "        std::string evicted = victim->key;\n"
            "        index_.erase(evicted);  // destroys the node"),
    replace(CACHE_CPP, "    Node* node = new Node;",
            "    std::unique_ptr<Node> owned(new Node());\n    Node* node = owned.get();"),
    replace(CACHE_CPP, "    index_[key] = node;", "    index_[key] = std::move(owned);"),
    replace(CACHE_CPP,
            "    std::map<std::string, Node*>::iterator it = index_.find(key);\n"
            "    if (it == index_.end()) {\n        return false;\n    }\n"
            "    unlink(it->second);\n    pushFront(it->second);",
            "    std::map<std::string, std::unique_ptr<Node>>::iterator it = index_.find(key);\n"
            "    if (it == index_.end()) {\n        return false;\n    }\n"
            "    unlink(it->second.get());\n    pushFront(it->second.get());"),
]  # fmt: skip


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.config = root / "migration_config.yaml"

    def cli(self, group: str, command: str, *args: str) -> Result:
        return runner.invoke(app, [group, command, "-c", str(self.config), *args])

    def json(self, command: str, *args: str) -> dict[str, Any]:
        result = self.cli("unit", command, *args, "--json")
        assert result.exit_code in (0, 1), result.output
        return json.loads(result.stdout)

    def submit(
        self, payload: dict[str, Any], edits: list[dict[str, str]], **extra: Any
    ) -> dict[str, Any]:
        body = {"edits": edits, "summary": "PriceCache owns its nodes.", "review_notes": []}
        Path(payload["edits_file"]).write_text(json.dumps(body | extra), encoding="utf-8")
        return self.json("submit")

    def snapshot(self) -> dict[str, bytes]:
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and ".git" not in p.parts and ".codecoremorph" not in p.parts
        }


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
  target: {{language: cpp, standard: cpp20, rules: []}}
parity: {{baseline_runs: 1}}
sandbox: {{mode: {mode}}}
""",
        encoding="utf-8",
    )
    git(tmp_path, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    return Project(root)


def test_a_host_agent_migrates_a_unit(project: Project, git: GitRunner) -> None:
    before = project.snapshot()
    base = git(project.root, "rev-parse", "HEAD").strip()
    started = project.json("start", "--rule", RULE, CACHE_H, CACHE_CPP)
    assert started["status"] == "waiting_for_agent", started["message"]
    context = Path(started["context_file"]).read_text(encoding="utf-8")
    assert "# CodeCoreMorph work unit U-0001: attempt 1 of 4" in context
    assert "#### src/cache.cpp" in context
    assert "delete victim;" in context
    assert not any(line.startswith("# ") for line in context.splitlines()[1:]), "one H1 only"

    read = project.cli("unit", "read", CACHE_CPP, "--from", "1", "--to", "3")
    assert read.exit_code == 0, read.output
    assert read.stdout.startswith("src/cache.cpp (lines 1-3 of ")
    grep = project.cli("unit", "grep", r"delete\b", "--glob", "src/**")
    assert "src/cache.cpp:" in grep.stdout
    assert "tests/" not in grep.stdout  # the glob arrived unexpanded

    outside = project.submit(started, [replace("src/item.cpp", "Item::", "Item::")])
    assert outside["status"] == "waiting_for_agent"
    assert outside["attempt"] == 1
    assert "outside the unit" in outside["message"]

    broken = project.submit(started, HEADER_ONLY)
    assert broken["attempt"] == 2
    assert broken["attempts"][0]["outcome"] == "build_failed"
    assert "The build failed with" in broken["message"]
    assert "src/cache.cpp:" in broken["message"]
    assert started["context_file"] in broken["message"]

    done = project.submit(started, SOURCE, review_notes=["new Node() stays: C++11."])
    assert done["status"] == "succeeded", done["message"]
    assert [a["outcome"] for a in done["attempts"]] == ["build_failed", "succeeded"]
    assert done["changed_files"] == [CACHE_H, CACHE_CPP]
    branch = done["branch"]
    log = git(project.root, "log", "--format=%s%n%b", f"{base}..{branch}")
    assert log.startswith(f"codecoremorph({RULE}): {CACHE_H}, {CACHE_CPP}")
    assert "CodeCoreMorph-Attempts: 2" in log
    assert "CodeCoreMorph-Models: host" in log
    diff = git(project.root, "diff", base, branch)
    assert "std::unique_ptr<Node>> index_;" in diff
    assert "-        delete victim;" in diff

    # The user's working tree, branch and HEAD never changed, and the run is closed.
    assert project.snapshot() == before
    assert git(project.root, "rev-parse", "HEAD").strip() == base
    assert not git(project.root, "status", "--porcelain").strip()
    assert not (project.root / ".codecoremorph" / "lock").exists()
    assert "no unit is active" in project.cli("unit", "submit").output
    status = project.cli("unit", "status")
    assert "succeeded" in status.stdout


def test_an_aborted_unit_leaves_nothing(project: Project, git: GitRunner) -> None:
    started = project.json("start", "--rule", RULE, CACHE_H, CACHE_CPP)
    assert started["status"] == "waiting_for_agent"
    busy = project.cli("unit", "start", "--rule", "replace_typedefs_with_using", CACHE_H)
    assert busy.exit_code == 3
    assert "waiting for its agent" in busy.output
    aborted = project.json("abort", "--reason", "needs an API change")
    assert aborted["status"] == "aborted"
    assert aborted["reason"] == "needs an API change"
    assert not git(project.root, "branch", "--list", "codecoremorph/*").strip()
    assert not (project.root / ".codecoremorph" / "lock").exists()


def test_a_unit_on_the_bumps_branch_builds_at_the_new_standard(project: Project) -> None:
    """Regression (D-42): units started from the standard bump's branch were configured with
    the manifest's source standard (-DCMAKE_CXX_STANDARD=11), and every unit reran the
    baseline's tests although the previous unit had just run them on the same commit."""
    bump = project.json("start", "--rule", "bump_standard")
    assert bump["status"] == "succeeded", bump["message"]
    assert bump["standard"] == "cpp20"
    second = project.json("start", "--base", bump["branch"], "--rule", RULE, CACHE_H, CACHE_CPP)
    assert second["status"] == "waiting_for_agent", second["message"]
    assert second["standard"] == "cpp20"
    run = project.root / ".codecoremorph" / "runs" / second["run_id"]
    baseline = json.loads((run / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["standard"] == "cpp20"
    assert baseline["tests_from"] == f"unit {bump['run_id']}"
    configure = (run / "logs" / "configure.log").read_text(encoding="utf-8")
    assert "-DCMAKE_CXX_STANDARD=20" in configure
    assert project.cli("unit", "abort").exit_code == 0


def test_plan_units_run_by_id_and_reuse_the_plans_baseline(project: Project) -> None:
    planned = runner.invoke(app, ["plan", str(project.config), "--json"])
    assert planned.exit_code == 0, planned.output
    listed = project.cli("unit", "list", "--json")
    assert listed.exit_code == 0, listed.output
    units = json.loads(listed.stdout)["units"]
    assert units[0]["rules"] == ["bump_standard"]
    assert all(u["outcome"] is None for u in units)
    plan_run = Path(json.loads(listed.stdout)["plan"]).parent.name

    started = project.json("start", "--plan-unit", units[0]["id"])
    assert started["unit"]["id"] == units[0]["id"]
    assert started["plan_unit"] == units[0]["id"]
    run = project.root / ".codecoremorph" / "runs" / started["run_id"]
    baseline = json.loads((run / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["tests_from"] == f"baseline {plan_run}"
    if started["status"] == "waiting_for_agent":
        assert project.cli("unit", "abort").exit_code == 0
    after = json.loads(project.cli("unit", "list", "--json").stdout)["units"]
    assert after[0]["outcome"] in ("succeeded", "aborted")

    mixed = project.cli("unit", "start", "--plan-unit", units[0]["id"], "--rule", RULE)
    assert mixed.exit_code == 2
    assert "don't add --rule" in mixed.output
