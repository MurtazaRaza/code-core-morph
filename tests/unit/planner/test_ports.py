"""Port planning by module (D-58): leaves first, header-only code with its last C++ user,
executables left to Phase 14, the callers and C headers of each unit, the target's names."""

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
    SkipCondition,
    UnitStatus,
)
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, PortNamer, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder

from .conftest import FakeProject
from .test_planner import EDITOR

PORT = LoadedRule(
    rule=Rule(
        id="port_module",
        language="cpp",
        kind=RuleKind.PORT,
        description="port",
        target_language="rust",
        port_units=PortUnits.MODULE,
        detector=Detector(type=DetectorType.ADAPTER_QUERY, query="unported_definitions"),
        extra_scope=["rust/**"],
        skip=[SkipCondition.TEST],
    ),
    pack="test",
    source=Path("rules/port_module.yaml"),
)
# Files with code left to port, as the C++ adapter's query would find them.
WITH_SITES = ("src/a.cpp", "src/b.cpp", "src/tmpl.h", "src/kept.h", "app/main.cpp")


def _project(p: FakeProject) -> ProjectModel:
    p.file("CMakeLists.txt", "add_library(lib src/a.cpp src/b.cpp)\n")
    p.file("include/m/a.h", 'extern "C" int a_f(void);\n', public_api=True)
    p.file("src/a.cpp", '#include "m/a.h"\n#include "tmpl.h"\nint a_f(void) { return 1; }\n')
    p.file("include/m/b.h", '#include "m/a.h"\nint b_f();\n', public_api=True)
    p.file("src/b.cpp", '#include "m/b.h"\n#include "tmpl.h"\nint b_f() { return a_f(); }\n')
    p.file("src/tmpl.h", "template <class T> T twice(T t) { return t + t; }\n")
    p.file("src/kept.h", "template <class T> T thrice(T t) { return 3 * t; }\n")
    p.file("app/main.cpp", '#include "m/b.h"\nint main() { return b_f(); }\n')
    p.file("tests/a_test.cpp", '#include "m/a.h"\n#include "kept.h"\nvoid t() { a_f(); }\n')
    p.dep("src/a.cpp", "include/m/a.h")
    p.dep("src/a.cpp", "src/tmpl.h")
    p.dep("include/m/b.h", "include/m/a.h")
    p.dep("src/b.cpp", "include/m/b.h")
    p.dep("src/b.cpp", "src/tmpl.h")
    p.dep("app/main.cpp", "include/m/b.h")
    p.dep("tests/a_test.cpp", "include/m/a.h")
    p.dep("tests/a_test.cpp", "src/kept.h")
    p.symbol("c:@F@a_f", "include/m/a.h", (1, 12), (1, 24), definition=False, boundary="c_abi")
    p.target("lib", "library", ["src/a.cpp", "src/b.cpp"])
    p.target("app", "executable", ["app/main.cpp"])
    p.target("tests", "executable", ["tests/a_test.cpp"])
    p.build()
    return ProjectModel.load(
        p.index_path, p.worktree, source_dirs=["src", "include", "app"], test_dirs=["tests"]
    )


def _plan(project: ProjectModel, names: PortNamer | None = None) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        assert name == "unported_definitions"
        return [RuleSite(file=f, line=1, text="still C++") for f in files if f in WITH_SITES]

    order = StandardOrder("cpp", ("cpp98", "cpp11", "cpp20"))
    scan = scan_rule(
        PORT, project, order=order, source="cpp98", target="rust2021", query=query, automatic=False
    )
    return build_plan(
        project,
        [scan],
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="cpp",
        source="cpp98",
        target="rust2021",
        target_language="rust",
        port_names=names,
    )


def test_modules_leaves_first(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project))
    ported = [u for u in plan.units if u.port is not None]
    assert [(u.port.crate, u.port.module) for u in ported if u.port] == [
        ("lib", "a"),
        ("lib", "b"),
    ]
    a, b = ported
    assert a.files == ["CMakeLists.txt", "include/m/a.h", "src/a.cpp"]
    assert a.port is not None
    assert a.port.build_target == "lib"
    assert a.port.c_headers == ["include/m/a.h"]  # a C interface the port keeps
    assert a.port.callers == ["include/m/b.h", "tests/a_test.cpp"]
    assert a.note is not None
    assert "stops including src/tmpl.h" in a.note
    # The template goes with its last C++ user, which then has no other caller to keep it.
    assert b.files == ["CMakeLists.txt", "include/m/b.h", "src/b.cpp", "src/tmpl.h"]
    assert b.port is not None
    assert b.port.callers == ["app/main.cpp"]
    assert b.rule_sites == {"src/b.cpp": 1, "src/tmpl.h": 1}


def test_what_stays_cpp(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project))
    skipped = {tuple(u.files): u.note for u in plan.units if u.status is UnitStatus.SKIPPED}
    assert skipped == {
        ("src/kept.h",): "skipped: header-only code that C++ still uses (tests/a_test.cpp)",
        ("app/main.cpp",): "skipped: an executable's code stays C++ until its tests are ported "
        "(Phase 14)",
    }
    # Every unit is in plan order, its cluster's place leaves first.
    assert [u.seq for u in plan.units] == list(range(1, len(plan.units) + 1))


def test_the_target_names_packages_and_modules(fake_project: FakeProject) -> None:
    seen: list[tuple[str, str, frozenset[str]]] = []

    def names(target: str, stem: str, taken: Collection[str]) -> tuple[str, str]:
        seen.append((target, stem, frozenset(taken)))
        return f"{target}_pkg", f"mod_{stem}"

    plan = _plan(_project(fake_project), names)
    assert [(u.port.crate, u.port.module) for u in plan.units if u.port] == [
        ("lib_pkg", "mod_a"),
        ("lib_pkg", "mod_b"),
    ]
    assert seen == [("lib", "a", frozenset()), ("lib", "b", frozenset({"mod_a"}))]
