"""``codecoremorph plan`` against the fixture projects with real toolchains (Phase 3 exit).

Native runs are marked ``toolchain``; Docker runs are marked ``docker`` and use the C++ image.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from codecoremorph.cli import app
from codecoremorph.core.errors import ExitCode
from codecoremorph.core.models import Plan, UnitStatus

GitRunner = Callable[..., str]
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "cpp"

# fixture: (source standard, target standard, rules)
MIGRATIONS: dict[str, tuple[str, str, list[str]]] = {
    "typedefs": ("cpp11", "cpp20", ["replace_typedefs_with_using"]),
    "ownership": ("cpp11", "cpp20", ["convert_raw_pointers_to_smart_pointers"]),
    "templates": ("cpp11", "cpp20", ["use_concepts_for_templates"]),
    "removed-features": ("cpp11", "cpp20", []),
    "c-compat": ("cpp11", "cpp20", ["replace_typedefs_with_using", "modernize_basics"]),
    "multi-module": (
        "cpp11",
        "cpp20",
        ["replace_typedefs_with_using", "convert_raw_pointers_to_smart_pointers"],
    ),
    "cpp98-legacy": ("cpp98", "cpp20", ["replace_typedefs_with_using"]),
    "cpp17-legacy": ("cpp17", "cpp23", ["modernize_basics"]),
    "untested": ("cpp11", "cpp20", ["replace_typedefs_with_using"]),
    "injection": (
        "cpp11",
        "cpp20",
        ["replace_typedefs_with_using", "convert_raw_pointers_to_smart_pointers"],
    ),
    "ffi-module": ("cpp17", "cpp20", ["modernize_basics"]),  # Phase 12's port fixture
}

pytestmark = pytest.mark.usefixtures("restore_logging")


def _manifest(
    name: str, source: str, target: str, rules: list[str], mode: str, target_language: str = "cpp"
) -> str:
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: [src, include, app]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {{language: cpp, standard: {source}, build_system: cmake}}
  target: {{language: {target_language}, standard: {target}, rules: [{", ".join(rules)}]}}
sandbox: {{mode: {mode}}}
"""


MakeProject = Callable[..., Path]


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def make_project(
    request: pytest.FixtureRequest, tmp_path: Path, git: GitRunner, isolated_home: Path
) -> MakeProject:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("cpp_toolchain_env")
        (isolated_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, isolated_home / "toolchains" / "cpp.env")

    def make(
        fixture: str,
        source: str,
        target: str,
        rules: list[str],
        *,
        source_dir: Path | None = None,
        target_language: str = "cpp",
    ) -> Path:
        if target_language != "cpp" and mode == "native":
            toolchain: Path = request.getfixturevalue(f"{target_language}_toolchain_env")
            shutil.copyfile(toolchain, isolated_home / "toolchains" / toolchain.name)
        root = tmp_path / fixture
        shutil.copytree(source_dir or FIXTURES_DIR / fixture, root)
        manifest = _manifest(fixture, source, target, rules, mode, target_language)
        (root / "migration_config.yaml").write_text(manifest, encoding="utf-8")
        git(root.parent, "init", "--quiet", str(root))
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "-m", "legacy")
        return root

    return make


def _plan(root: Path) -> tuple[int, dict[str, Any], str]:
    result = CliRunner().invoke(app, ["plan", str(root / "migration_config.yaml"), "--json"])
    output = result.stdout
    payload: dict[str, Any] = json.loads(output) if result.exit_code == 0 else {}
    return result.exit_code, payload, result.stderr


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".codecoremorph" not in p.parts
    }


@pytest.mark.parametrize("fixture", sorted(MIGRATIONS))
def test_plan_every_fixture(make_project: MakeProject, fixture: str, git: GitRunner) -> None:
    source, target, rules = MIGRATIONS[fixture]
    root = make_project(fixture, source, target, rules)
    before = _snapshot(root)

    code, payload, stderr = _plan(root)
    assert code == 0, stderr
    plan = Plan.model_validate(payload["plan"])
    assert plan.base_commit == git(root, "rev-parse", "HEAD").strip()
    assert [r.id for r in plan.rules] == ["bump_standard", *rules]
    first = plan.units[0]
    assert first.rules == ["bump_standard"]
    assert "CMakeLists.txt" in first.files
    assert all((root / f).is_file() for u in plan.units for f in u.files)
    assert any(u.status is UnitStatus.PENDING for u in plan.units[1:])
    assert plan.estimate is not None
    assert plan.estimate.cost_usd is not None
    assert payload["baseline"]["tests"]["passing"]
    assert payload["baseline"]["tests"]["flaky"] == []
    # plan.json holds exactly the printed plan
    written = Path(payload["plan_path"]).read_text(encoding="utf-8")
    assert Plan.model_validate_json(written) == plan

    # no code changed, and the run branch is gone
    assert _snapshot(root) == before
    assert git(root, "branch", "--list").split() == ["*", "main"]


def test_a_port_plan(make_project: MakeProject) -> None:
    """``ffi-module`` to Rust (D-56): no standard bump, and one port unit for the C header, with
    the module's source and the build files; the header is a read-only reference file."""
    root = make_project(
        "ffi-module", "cpp17", "rust2021", ["port_c_abi_module"], target_language="rust"
    )
    code, payload, stderr = _plan(root)
    assert code == 0, stderr
    plan = Plan.model_validate(payload["plan"])
    assert plan.migration == "cpp cpp17 -> rust rust2021"
    assert [r.id for r in plan.rules] == ["port_c_abi_module"]
    [unit] = plan.units
    assert unit.status is UnitStatus.PENDING
    assert unit.files == [
        "CMakeLists.txt",
        "include/textkit/ring.h",
        "src/ring.cpp",
        "tests/CMakeLists.txt",
    ]
    assert unit.reference_files == ["include/textkit/ring.h"]
    assert unit.rule_sites == {"src/ring.cpp": 8}  # the eight functions of the C API
    assert unit.note == "ports src/ring.cpp to rust behind include/textkit/ring.h"


def test_plans_are_deterministic(make_project: MakeProject) -> None:
    source, target, rules = MIGRATIONS["multi-module"]
    root = make_project("multi-module", source, target, rules)
    first = _plan(root)[1]
    second = _plan(root)[1]
    assert first["run_id"] != second["run_id"]
    assert Path(first["plan_path"]).read_bytes() == Path(second["plan_path"]).read_bytes()


def test_c_compatible_headers_are_skipped(make_project: MakeProject) -> None:
    source, target, rules = MIGRATIONS["c-compat"]
    _, payload, _ = _plan(make_project("c-compat", source, target, rules))
    plan = Plan.model_validate(payload["plan"])
    skipped = [u for u in plan.units if u.status is UnitStatus.SKIPPED]
    assert {u.rules[0] for u in skipped} == {"replace_typedefs_with_using", "modernize_basics"}
    for unit in skipped:
        assert "c_compatible" in unit.flags
        assert all(f.startswith("include/ccore/") and f.endswith(".h") for f in unit.files)
    pending = {f for u in plan.units if u.status is UnitStatus.PENDING for f in u.files}
    assert not pending & {f for u in skipped for f in u.files}


def test_a_failing_baseline_build_stops_the_plan(make_project: MakeProject, git: GitRunner) -> None:
    root = make_project("typedefs", "cpp11", "cpp20", ["replace_typedefs_with_using"])
    broken = root / "src" / "vector.cpp"
    broken.write_text(broken.read_text() + "\nint broken() { return undeclared; }\n")
    git(root, "commit", "--quiet", "--all", "-m", "break the build")
    code, _, stderr = _plan(root)
    assert code == int(ExitCode.ENVIRONMENT)
    assert "doesn't build at the base commit" in stderr
    assert "src/vector.cpp" in stderr
    assert "Full log:" in stderr


FLAKY_CMAKE = """\
cmake_minimum_required(VERSION 3.21)
project(flaky CXX)
enable_testing()
add_library(core src/core.cpp)
target_include_directories(core PUBLIC include)
add_executable(steady tests/steady.cpp)
target_link_libraries(steady core)
add_test(NAME steady COMMAND steady)
add_executable(alternating tests/alternating.cpp)
add_test(NAME alternating COMMAND alternating)
"""
# Fails on every second run: a counter in its working directory (the build tree) flips it.
ALTERNATING = """\
#include <cstdio>
int main() {
    int runs = 0;
    if (std::FILE* in = std::fopen("alternating.count", "r")) {
        if (std::fscanf(in, "%d", &runs) != 1) runs = 0;
        std::fclose(in);
    }
    if (std::FILE* out = std::fopen("alternating.count", "w")) {
        std::fprintf(out, "%d", runs + 1);
        std::fclose(out);
    }
    return runs % 2;
}
"""


def test_flaky_tests_are_found_and_excluded(make_project: MakeProject, tmp_path: Path) -> None:
    source = tmp_path / "flaky-source"
    files = {
        "CMakeLists.txt": FLAKY_CMAKE,
        "include/core.h": "int answer();\n",
        "src/core.cpp": '#include "core.h"\nint answer() { return 42; }\n',
        "tests/steady.cpp": '#include "core.h"\nint main() { return answer() == 42 ? 0 : 1; }\n',
        "tests/alternating.cpp": ALTERNATING,
    }
    for name, text in files.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text(text, encoding="utf-8")
    root = make_project("flaky", "cpp11", "cpp14", [], source_dir=source)
    code, payload, stderr = _plan(root)
    assert code == 0, stderr
    tests = payload["baseline"]["tests"]
    assert tests["passing"] == ["ctest:steady"]
    assert tests["flaky"] == ["ctest:alternating"]
