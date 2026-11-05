"""Finishing a port (D-60): an executable's unit, a unit per test file, the retirement, what
each needs, the kept API, and a later run that plans only what is left."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from codecoremorph.core.models import (
    Detector,
    DetectorType,
    LoadedRule,
    Plan,
    PortUnits,
    Rule,
    RuleKind,
    RuleSite,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder

from .conftest import FakeProject
from .test_planner import EDITOR
from .test_ports import PORT, WITH_SITES


def _rule(rule_id: str, units: PortUnits, detector: Detector) -> LoadedRule:
    return LoadedRule(
        rule=Rule(
            id=rule_id,
            language="cpp",
            kind=RuleKind.PORT,
            description=rule_id,
            target_language="rust",
            port_units=units,
            detector=detector,
            extra_scope=["rust/**"],
        ),
        pack="test",
        source=Path(f"rules/{rule_id}.yaml"),
    )


FILES = Detector(type=DetectorType.FILES)
EXECUTABLES = _rule("port_executable", PortUnits.EXECUTABLE, FILES)
TESTS = _rule(
    "port_tests",
    PortUnits.TESTS,
    Detector(type=DetectorType.ADAPTER_QUERY, query="test_cases"),
)
RETIRE = _rule("retire_cpp", PortUnits.RETIREMENT, FILES)
CASES = {"tests/a_test.cpp": ["gtest:A.One", "gtest:A.Two"], "tests/b_test.cpp": ["gtest:B.One"]}


def _project(p: FakeProject, *, glue: bool = False) -> ProjectModel:
    p.file("CMakeLists.txt", "add_library(lib src/a.cpp src/b.cpp)\n")
    p.file("include/m/a.h", "int a_f();\n", public_api=True)
    p.file("src/a.cpp", '#include "m/a.h"\nint a_f() { return 1; }\n')
    p.file("src/internal.h", "int helper();\n")
    p.file("src/b.cpp", '#include "internal.h"\nint helper() { return 2; }\n')
    p.file("src/kept.h", "template <class T> T thrice(T t) { return 3 * t; }\n")
    p.file("app/main.cpp", '#include "m/a.h"\nint main() { return a_f(); }\n')
    p.file("app/flags.h", "int flag();\n")
    p.file("tests/a_test.cpp", '#include "m/a.h"\n#include "kept.h"\nTEST(A, One) {}\n')
    p.file("tests/b_test.cpp", '#include "internal.h"\nTEST(B, One) {}\n')
    p.file("tests/CMakeLists.txt", "add_test(NAME t COMMAND tests)\n")
    p.dep("src/a.cpp", "include/m/a.h")
    p.dep("src/b.cpp", "src/internal.h")
    p.dep("app/main.cpp", "include/m/a.h")
    p.dep("app/main.cpp", "app/flags.h")
    p.dep("tests/a_test.cpp", "include/m/a.h")
    p.dep("tests/a_test.cpp", "src/kept.h")
    p.dep("tests/b_test.cpp", "src/internal.h")
    if glue:  # a module port's C++ glue header, under the new code
        p.file("rust/lib/include/lib/glue.hpp", '#include "m/a.h"\ninline int g() { return 1; }\n')
        p.dep("rust/lib/include/lib/glue.hpp", "include/m/a.h")
    # A vendored googletest and a library of test helpers are no project libraries to port.
    p.file("third_party/gtest/gtest.cc", "int gtest;\n")
    p.file("tests/support.cpp", "int support() { return 0; }\n")
    p.target("lib", "library", ["src/a.cpp", "src/b.cpp"])
    p.target("gtest", "library", ["third_party/gtest/gtest.cc"])
    p.target("support", "library", ["tests/support.cpp"])
    p.target("app", "executable", ["app/main.cpp"], dependencies=("gtest", "lib"))
    p.target(
        "tests",
        "executable",
        ["tests/a_test.cpp", "tests/b_test.cpp"],
        ("gtest", "lib", "support"),
    )
    p.test("ctest:tests", "tests", "tests/CMakeLists.txt")
    p.test("ctest:app_runs", "app", "tests/CMakeLists.txt")
    p.test("ctest:script", None, "tests/CMakeLists.txt")
    p.build()
    return ProjectModel.load(
        p.index_path,
        p.worktree,
        source_dirs=["src", "include", "app", "rust"],
        test_dirs=["tests"],
    )


def _plan(
    project: ProjectModel,
    rules: list[LoadedRule],
    *,
    keep: bool = False,
    mapped: Collection[str] = (),
) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        if name == "test_cases":
            return [
                RuleSite(file=f, line=n, text=case)
                for f in files
                for n, case in enumerate(CASES.get(f, []), start=1)
            ]
        # The test helpers define functions too, as snappy's do (snappy-test.cc).
        sites = (*WITH_SITES, "rust/lib/include/lib/glue.hpp", "tests/support.cpp")
        return [RuleSite(file=f, line=1, text="still C++") for f in files if f in sites]

    order = StandardOrder("cpp", ("cpp98", "cpp11", "cpp20"))
    scans = [
        scan_rule(rule, project, order=order, source="cpp98", target="rust2021", query=query)
        for rule in rules
    ]
    return build_plan(
        project,
        scans,
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="cpp",
        source="cpp98",
        target="rust2021",
        target_language="rust",
        keep_source_api=keep,
        mapped_tests=mapped,
    )


def _by_rule(plan: Plan) -> dict[str, list[WorkUnit]]:
    found: dict[str, list[WorkUnit]] = {}
    for unit in plan.units:
        found.setdefault(unit.rules[0], []).append(unit)
    return found


def test_a_whole_port(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [PORT, EXECUTABLES, TESTS, RETIRE])
    units = _by_rule(plan)
    module_a = units["port_module"][0]
    assert module_a.files == [
        "CMakeLists.txt",
        "include/m/a.h",
        "src/a.cpp",
        "tests/CMakeLists.txt",
    ]
    # The executable is its own unit now, and code only tests use goes with the retirement, as
    # do the test helpers port_module skips: nothing is skipped.
    assert [u for u in plan.units if u.status is not UnitStatus.PENDING] == []
    [program] = units["port_executable"]
    assert program.files == [
        "CMakeLists.txt",
        "app/flags.h",
        "app/main.cpp",
        "tests/CMakeLists.txt",
    ]
    assert program.port is not None
    assert (program.port.crate, program.port.bin, program.port.dependencies) == (
        "app",
        "app",
        ["lib"],
    )
    assert program.port.tests == ["ctest:app_runs"]  # the CTest entry that runs the program
    assert program.needs == [module_a.id]
    a_test, b_test = units["port_tests"]
    assert a_test.port is not None
    assert b_test.port is not None
    assert (a_test.files, a_test.reference_files) == (
        ["src/kept.h", "tests/a_test.cpp"],
        ["src/kept.h", "tests/a_test.cpp"],
    )
    assert (a_test.port.crate, a_test.port.module, a_test.port.tests) == (
        "lib",
        "a_test",
        ["gtest:A.One", "gtest:A.Two"],
    )
    assert a_test.port.wrappers == []
    assert a_test.port.dependencies == []  # neither googletest nor the test helpers
    assert a_test.needs == [module_a.id]
    # The test target's last file maps the CTest entry that only runs its binary.
    assert b_test.port.wrappers == ["ctest:tests"]
    [retire] = units["retire_cpp"]
    assert retire.files == [
        "CMakeLists.txt",
        "app/flags.h",
        "app/main.cpp",
        "include/m/a.h",
        "src/a.cpp",
        "src/b.cpp",
        "src/internal.h",
        "src/kept.h",
        "tests/CMakeLists.txt",
        "tests/a_test.cpp",
        "tests/b_test.cpp",
        "tests/support.cpp",
    ]
    assert retire.needs == [u.id for u in plan.units if u is not retire]
    assert retire.note is not None
    assert "the CMake build" in retire.note


def test_keeping_the_public_api(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [PORT, EXECUTABLES, TESTS, RETIRE], keep=True)
    [retire] = _by_rule(plan)["retire_cpp"]
    # The public header stays with its module's sources; the internal module, the program and
    # the tests that use what goes go too. The CMake build stays.
    assert retire.files == [
        "app/flags.h",
        "app/main.cpp",
        "src/b.cpp",
        "src/internal.h",
        "src/kept.h",
        "tests/a_test.cpp",
        "tests/b_test.cpp",
    ]


def test_a_later_run_plans_only_what_is_left(fake_project: FakeProject) -> None:
    project = _project(fake_project, glue=True)
    mapped = ["gtest:A.One", "gtest:A.Two", "ctest:tests"]
    plan = _plan(project, [PORT, EXECUTABLES, TESTS, RETIRE], mapped=mapped)
    units = _by_rule(plan)
    # a_test.cpp is translated already; the port's own C++ glue is no source to port.
    assert [u.files for u in units["port_tests"]] == [["tests/b_test.cpp"]]
    assert units["port_tests"][0].port is not None
    assert units["port_tests"][0].port.wrappers == []  # mapped already
    assert all("rust/lib/include/lib/glue.hpp" not in u.files for u in plan.units)


def test_without_the_executable_rule_the_program_stays(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [PORT])
    skipped = [u.note or "" for u in plan.units if u.status is UnitStatus.SKIPPED]
    assert sorted(skipped) == [
        "skipped: an executable's code stays C++ until its tests are ported (Phase 14)",
        "skipped: header-only code that C++ still uses (tests/a_test.cpp)",
        "skipped: port_module doesn't apply to test files",
    ]
