"""C ports by translation unit (D-68): one unit per library source, ordered by calls rather
than includes, the shared headers kept read-only, the headers only one source includes with
its unit, the exports, and a call cycle broken where it costs least."""

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
    SkipCondition,
    UnitStatus,
)
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder

from .conftest import FakeProject
from .test_planner import EDITOR

PORT = LoadedRule(
    rule=Rule(
        id="port_c_module",
        language="c",
        kind=RuleKind.PORT,
        description="port",
        target_language="rust",
        port_units=PortUnits.TRANSLATION_UNIT,
        detector=Detector(type=DetectorType.ADAPTER_QUERY, query="c_abi_definitions"),
        extra_scope=["rust/**"],
        skip=[SkipCondition.TEST],
    ),
    pack="test",
    source=Path("rules/port_c_module.yaml"),
)
LIBRARY = ("src/conf.c", "src/htab.c", "src/sbuf.c", "src/tok.c", "src/wf.c")


def _function(p: FakeProject, name: str, header: str, source: str, line: int) -> None:
    p.symbol(f"c:@F@{name}", header, (line, 1), (line, 30), definition=False, boundary="c_abi")
    p.symbol(f"c:@F@{name}", source, (line, 1), (line + 2, 1), boundary="c_abi")


def _project(p: FakeProject) -> ProjectModel:
    """c-legacy's shape: one internal header that declares every module and includes the
    public one, so by includes alone wf.c (the API) would sort with the leaves."""
    p.file("Makefile", "all: libwf.a\n", role="build", language="make")
    p.file("include/wf.h", "int wf_count(void);\n", public_api=True)
    p.file("src/wfint.h", '#include "wf.h"\nvoid sbuf_init(void);\n')
    p.file("src/htab_priv.h", "#define HTAB_LOAD 3\n")
    for path in LIBRARY:
        p.file(path, '#include "wfint.h"\n')
        p.dep(path, "src/wfint.h")
    p.file("tools/wordfreq.c", '#include "wf.h"\nint main(void) { return wf_count(); }\n')
    p.file("tests/test_sbuf.c", '#include "wfint.h"\n')
    p.dep("src/wfint.h", "include/wf.h")
    p.dep("src/htab.c", "src/htab_priv.h")
    p.dep("tools/wordfreq.c", "include/wf.h")
    p.dep("tests/test_sbuf.c", "src/wfint.h")
    _function(p, "sbuf_init", "src/wfint.h", "src/sbuf.c", 2)
    _function(p, "sbuf_putc", "src/wfint.h", "src/sbuf.c", 5)
    _function(p, "htab_add", "src/wfint.h", "src/htab.c", 8)
    _function(p, "tok_next", "src/wfint.h", "src/tok.c", 11)
    _function(p, "wf_set_min", "include/wf.h", "src/conf.c", 4)
    _function(p, "wf_count", "include/wf.h", "src/wf.c", 1)
    p.symbol("c:htab.c@F@grow", "src/htab.c", (20, 1), (25, 1), boundary="c_abi")  # static
    p.symbol("c:@min_length", "src/wfint.h", (14, 1), (14, 30), kind="variable",
             definition=False, boundary="c_abi")  # fmt: skip
    p.symbol("c:@min_length", "src/conf.c", (2, 1), (2, 20), kind="variable", boundary="c_abi")
    p.ref("src/htab.c", "c:htab.c@F@grow", 9)
    p.ref("src/tok.c", "c:@F@sbuf_putc", 12)
    p.ref("src/tok.c", "c:@min_length", 13)
    p.ref("src/wf.c", "c:@F@htab_add", 3)
    p.ref("src/wf.c", "c:@F@tok_next", 4)
    p.ref("src/wf.c", "c:@F@sbuf_init", 5)
    p.ref("tools/wordfreq.c", "c:@F@wf_count", 2)
    p.ref("tests/test_sbuf.c", "c:@F@sbuf_init", 3)
    p.target("wf", "library", list(LIBRARY), artifacts=("libwf.a", "libwf.so.1"))
    p.target("wordfreq", "executable", ["tools/wordfreq.c"], ("wf",))
    p.target("test_sbuf", "executable", ["tests/test_sbuf.c"], ("wf",))
    p.build()
    return ProjectModel.load(
        p.index_path, p.worktree, source_dirs=["src", "include", "tools"], test_dirs=["tests"]
    )


def _plan(project: ProjectModel, with_sites: tuple[str, ...] = LIBRARY) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        assert name == "c_abi_definitions"
        return [RuleSite(file=f, line=1, text="defined here") for f in files if f in with_sites]

    order = StandardOrder("c", ("c89", "c99"))
    scan = scan_rule(
        PORT, project, order=order, source="c89", target="rust2021", query=query, automatic=False
    )
    return build_plan(
        project,
        [scan],
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="c",
        source="c89",
        target="rust2021",
        target_language="rust",
    )


def test_sources_go_leaves_first_by_calls(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project))
    ported = [u for u in plan.units if u.port is not None]
    # tok.c calls sbuf.c and conf.c, and wf.c calls tok.c, htab.c and sbuf.c.
    assert [u.port.module for u in ported if u.port] == ["conf", "htab", "sbuf", "tok", "wf"]
    assert {u.port.crate for u in ported if u.port} == {"wf"}
    assert all(u.status is UnitStatus.PENDING and not u.needs for u in ported)


def test_shared_headers_stay_and_own_headers_go(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project))
    units = {u.port.module: u for u in plan.units if u.port}
    sbuf, htab = units["sbuf"], units["htab"]
    assert sbuf.files == ["Makefile", "include/wf.h", "src/sbuf.c", "src/wfint.h"]
    assert sbuf.reference_files == ["include/wf.h", "src/wfint.h"]  # read-only
    assert sbuf.rule_sites == {"src/sbuf.c": 1}
    # Only htab.c includes htab_priv.h, so it is the unit's to remove.
    assert "src/htab_priv.h" in htab.files
    assert "src/htab_priv.h" not in htab.reference_files
    assert htab.note is not None
    assert "with src/htab_priv.h, which only it includes" in htab.note


def test_exports_and_callers(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project))
    ports = {u.port.module: u.port for u in plan.units if u.port}
    assert ports["sbuf"].exports == ["sbuf_init", "sbuf_putc"]
    assert ports["sbuf"].c_headers == ["src/wfint.h"]
    assert ports["sbuf"].callers == ["src/tok.c", "src/wf.c", "tests/test_sbuf.c"]
    assert ports["sbuf"].sys_headers == ["include/wf.h", "src/wfint.h"]
    assert ports["sbuf"].archive == "libwf.a"  # what the crate is folded into
    assert ports["sbuf"].codemod_options() == {
        "crate": "wf",
        "module": "sbuf",
        "build_target": "wf",
        "headers": "include/wf.h,src/wfint.h",
        "archive": "libwf.a",
    }
    # The static helper isn't exported; the global that tok.c reads is.
    assert ports["htab"].exports == ["htab_add"]
    assert ports["conf"].exports == ["min_length", "wf_set_min"]
    assert ports["conf"].c_headers == ["include/wf.h", "src/wfint.h"]
    assert ports["wf"].callers == ["tools/wordfreq.c"]
    conf = next(u for u in plan.units if u.port and u.port.module == "conf")
    assert conf.note is not None
    assert "exporting 1 function and 1 variable" in conf.note


def test_variadic_functions_stay_c(fake_project: FakeProject) -> None:
    """Stable Rust can't define a function that takes ``...`` or a ``va_list`` (D-70): the
    unit exports the file's other functions, and its note says which stay in the C file."""
    p = fake_project
    p.file("Makefile", "all: libk.a\n", role="build", language="make")
    p.file("src/k.h", "void k_log(const char *fmt, ...);\n")
    p.file("src/log.c", '#include "k.h"\n')
    p.file("src/use.c", '#include "k.h"\n')
    for path in ("src/log.c", "src/use.c"):
        p.dep(path, "src/k.h")
    for name, signature in (
        ("k_log", "void k_log(const char *, ...)"),
        ("k_vlog", "void k_vlog(const char *, va_list)"),
        ("k_write", "void k_write(const char *)"),
    ):
        for file, definition in (("src/k.h", False), ("src/log.c", True)):
            p.symbol(f"c:@F@{name}", file, (1, 1), (1, 30), definition=definition,
                     boundary="c_abi", signature=signature)  # fmt: skip
        p.ref("src/use.c", f"c:@F@{name}", 2)
    p.target("k", "library", ["src/log.c", "src/use.c"], artifacts=("libk.a",))
    p.build()
    project = ProjectModel.load(p.index_path, p.worktree, source_dirs=["src"])
    plan = _plan(project, ("src/log.c",))
    unit = next(u for u in plan.units if u.port and u.port.module == "log")
    assert unit.port is not None
    assert unit.port.exports == ["k_write"]
    assert unit.note is not None
    assert "k_log and k_vlog stay C in src/log.c, over the Rust code" in unit.note


def test_a_program_and_an_uncompiled_source_are_skipped(fake_project: FakeProject) -> None:
    fake_project.file("src/unused.c", "int unused(void) { return 0; }\n")
    project = _project(fake_project)
    plan = _plan(project, (*LIBRARY, "tools/wordfreq.c", "src/unused.c"))
    skipped = {tuple(u.files): u.note for u in plan.units if u.status is UnitStatus.SKIPPED}
    assert skipped == {
        ("tools/wordfreq.c",): "skipped: a program's code stays C until a port_executable rule",
        ("src/unused.c",): "skipped: no library target of the build compiles it",
    }
    assert len([u for u in plan.units if u.port]) == len(LIBRARY)


def test_a_call_cycle_breaks_where_it_costs_least(fake_project: FakeProject) -> None:
    p = fake_project
    p.file("Makefile", "all: liba.a\n", role="build", language="make")
    p.file("src/ab.h", "void a1(void);\n")
    for path in ("src/a.c", "src/b.c"):
        p.file(path, '#include "ab.h"\n')
        p.dep(path, "src/ab.h")
    for name, source in (("a1", "src/a.c"), ("b1", "src/b.c"), ("b2", "src/b.c")):
        _function(p, name, "src/ab.h", source, 1)
    p.ref("src/a.c", "c:@F@b1", 2)  # a.c calls two of b.c's functions
    p.ref("src/a.c", "c:@F@b2", 3)
    p.ref("src/b.c", "c:@F@a1", 2)  # b.c calls one of a.c's
    p.target("ab", "library", ["src/a.c", "src/b.c"])
    p.build()
    project = ProjectModel.load(p.index_path, p.worktree, source_dirs=["src"])
    plan = _plan(project, ("src/a.c", "src/b.c"))
    ported = [u for u in plan.units if u.port is not None]
    assert [u.port.module for u in ported if u.port] == ["b", "a"]
    assert ported[0].note is not None
    assert "calls src/a.c while they are still C (a call cycle)" in ported[0].note
