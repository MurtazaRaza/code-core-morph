"""A C port's retirement (D-69), and what it keeps with ``keep_source_api`` (D-72): the public
headers and what they include, the C files whose functions taking ``...`` stayed C, the tests
of the kept API with their helpers, and the Make build."""

from __future__ import annotations

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
    WorkUnit,
)
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder

from .conftest import FakeProject
from .test_planner import EDITOR
from .test_translation_units import PORT

RETIRE = LoadedRule(
    rule=Rule(
        id="retire_c",
        language="c",
        kind=RuleKind.PORT,
        description="retire",
        target_language="rust",
        port_units=PortUnits.RETIREMENT,
        detector=Detector(type=DetectorType.FILES),
        extra_scope=["rust/**"],
    ),
    pack="test",
    source=Path("rules/retire_c.yaml"),
)
LIBRARY = ("src/buf.c", "src/count.c", "src/log.c")


def _project(p: FakeProject) -> ProjectModel:
    """A library whose public header declares a variadic logger (log.c keeps it in C), with an
    internal header log.c needs, a private header only buf.c and its test include, and a test
    of the public API that includes a test helper."""
    p.file("Makefile", "all: libk.a\n", role="build", language="make")
    header = "int k_count(void);\nvoid k_log(const char *, ...);\n"
    p.file("include/k.h", header, language="c", public_api=True)
    p.file("src/kint.h", '#include "k.h"\nvoid k_buf(void);\n', language="c")
    p.file("src/buf_priv.h", "#define BUF_SIZE 8\n", language="c")
    for path in LIBRARY:
        p.file(path, '#include "kint.h"\n')
        p.dep(path, "src/kint.h")
    p.dep("src/kint.h", "include/k.h")
    p.dep("src/buf.c", "src/buf_priv.h")
    p.file("tests/check.h", "#define CHECK(x) ((void)(x))\n", language="c")
    p.file("tests/test_api.c", '#include "check.h"\n#include "k.h"\n')
    p.file("tests/test_buf.c", '#include "check.h"\n#include "buf_priv.h"\n')
    for test in ("tests/test_api.c", "tests/test_buf.c"):
        p.dep(test, "tests/check.h")
    p.dep("tests/test_api.c", "include/k.h")
    p.dep("tests/test_buf.c", "src/buf_priv.h")
    for name, header, source, signature in (
        ("k_count", "include/k.h", "src/count.c", "int (void)"),
        ("k_log", "include/k.h", "src/log.c", "void (const char *, ...)"),
        ("k_buf", "src/kint.h", "src/buf.c", "void (void)"),
    ):
        for file, definition in ((header, False), (source, True)):
            p.symbol(f"c:@F@{name}", file, (1, 1), (1, 30), definition=definition,
                     boundary="c_abi", signature=signature)  # fmt: skip
    p.ref("src/count.c", "c:@F@k_buf", 2)
    p.ref("tests/test_api.c", "c:@F@k_count", 3)
    p.target("k", "library", list(LIBRARY), artifacts=("libk.a",))
    for test in ("test_api", "test_buf"):
        p.target(test, "executable", [f"tests/{test}.c"], ("k",))
    p.build()
    return ProjectModel.load(
        p.index_path, p.worktree, source_dirs=["src", "include"], test_dirs=["tests"]
    )


def _retirement(project: ProjectModel, *, keep: bool) -> WorkUnit:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        return [RuleSite(file=f, line=1, text="defined here") for f in files if f in LIBRARY]

    order = StandardOrder("c", ("c89", "c99"))
    scans = [
        scan_rule(rule, project, order=order, source="c89", target="rust2021", query=query)
        for rule in (PORT, RETIRE)
    ]
    plan: Plan = build_plan(
        project,
        scans,
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="c",
        source="c89",
        target="rust2021",
        target_language="rust",
        keep_source_api=keep,
    )
    [unit] = [u for u in plan.units if u.rules == ["retire_c"]]
    return unit


def test_the_retirement_deletes_every_c_file_and_the_build(fake_project: FakeProject) -> None:
    unit = _retirement(_project(fake_project), keep=False)
    assert unit.files == [
        "Makefile",
        "include/k.h",
        "src/buf.c",
        "src/buf_priv.h",
        "src/count.c",
        "src/kint.h",
        "src/log.c",
        "tests/check.h",
        "tests/test_api.c",
        "tests/test_buf.c",
    ]
    assert unit.kept_api == []
    assert unit.reference_files == []


def test_keeping_the_api_keeps_what_outside_c_code_needs(fake_project: FakeProject) -> None:
    unit = _retirement(_project(fake_project), keep=True)
    # Deleted: the ported sources, the private header and the test that uses it. The public
    # header is a read-only reference, whose functions the crates must keep exporting.
    assert unit.files == [
        "include/k.h",
        "src/buf.c",
        "src/buf_priv.h",
        "src/count.c",
        "tests/test_buf.c",
    ]
    assert unit.reference_files == ["include/k.h"]
    assert unit.kept_api == ["include/k.h"]
    # Kept, so not in the unit: log.c (k_log takes `...`), the internal header it includes,
    # the public API's test with its helper, and the Makefile.
    assert unit.rule_sites == {
        "src/buf.c": 1,
        "src/buf_priv.h": 1,
        "src/count.c": 1,
        "tests/test_buf.c": 1,
    }
    assert unit.note is not None
    assert "deletes 4 files, keeps 5 for outside C consumers" in unit.note
    assert "the crates keep exporting what include/k.h declares, and the build stays" in unit.note
    assert "src/log.c stays for k_log, which takes `...` or a `va_list`" in unit.note
