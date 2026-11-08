"""A port from Rust (D-75): the ``rust_to_c`` pack's units over a real index of the Phase 16
spike's ``stats`` crate (the Rust adapter's, with rust-analyzer's recorded SCIP output): the
modules leaves first by references, each with its public items as exports; the test files
after the modules they test; the retirement last, needing every other unit."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.adapters.rust.scip import Document, read_index
from codecoremorph.core.models import LanguageConfig, Plan, RuleSite, UnitStatus
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.rules.registry import RuleRegistry
from codecoremorph.store.index import IndexStore

from .test_planner import EDITOR

DATA = Path(__file__).resolve().parents[1] / "adapters" / "rust" / "data"
RULES = ("port_rust_module", "port_rust_tests", "retire_rust")
ROOT_FILES = ("Cargo.lock", "Cargo.toml", "src/lib.rs")


def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crate: Path
) -> tuple[ProjectModel, AdapterContext]:
    work = tmp_path / crate.name
    shutil.copytree(crate, work)
    adapter = RustAdapter()

    def scip(ctx: AdapterContext, package: str, problems: list[str]) -> list[Document]:
        return read_index((DATA / f"{crate.name}.scip").read_bytes())

    monkeypatch.setattr(adapter, "_scip", scip)
    executor: Any = object()  # the index runs no command but rust-analyzer's, recorded here
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,
        lang=LanguageConfig(language="rust", standard="rust2021", port_target="c"),
    )
    index = adapter.index(ctx)
    assert not index.problems
    return ProjectModel.load(index.path, work, test_dirs=["tests"]), ctx


def _plan(project: ProjectModel, ctx: AdapterContext, rules: tuple[str, ...] = RULES) -> Plan:
    registry = RuleRegistry.discover([])
    adapter = RustAdapter()

    def query(name: str, files: list[str]) -> list[RuleSite]:
        return adapter.query(ctx, name, files)

    order = StandardOrder("rust", ("rust2018", "rust2021", "rust2024"))
    scans = [
        scan_rule(
            registry.get(r), project, order=order, source="rust2021", target="c99", query=query
        )
        for r in rules
    ]
    return build_plan(
        project,
        scans,
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="rust",
        source="rust2021",
        target="c99",
        target_language="c",
    )


def test_modules_leaves_first_then_tests_then_the_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, ctx = _index(tmp_path, monkeypatch, DATA / "stats")
    plan = _plan(project, ctx)
    units = [(u.rules[0], u.files, u.status) for u in plan.units]
    assert units == [
        # text.rs uses stats.rs, so stats goes first; lib.rs only declares the modules.
        # Each module's unit has the crate root too, for its crate attributes.
        ("port_rust_module", [*ROOT_FILES, "src/stats.rs"], UnitStatus.PENDING),
        ("port_rust_module", [*ROOT_FILES, "src/text.rs"], UnitStatus.PENDING),
        ("port_rust_tests", ["Cargo.lock", "Cargo.toml", "src/stats.rs"], UnitStatus.PENDING),
        ("port_rust_tests", ["Cargo.lock", "Cargo.toml", "src/text.rs"], UnitStatus.PENDING),
        ("port_rust_tests", ["Cargo.lock", "Cargo.toml", "tests/api.rs"], UnitStatus.PENDING),
        (
            "retire_rust",
            [
                "Cargo.lock",
                "Cargo.toml",
                "src/lib.rs",
                "src/stats.rs",
                "src/text.rs",
                "tests/api.rs",
            ],
            UnitStatus.PENDING,
        ),
    ]
    stats, text, stats_tests, _text_tests, api, retire = plan.units
    assert stats.port is not None
    assert text.port is not None
    assert (stats.port.crate, stats.port.module, stats.port.build_target) == (
        "stats",
        "stats",
        "stats",
    )
    assert "stats::stats::mean" in stats.port.exports
    assert "stats::stats::Histogram::new" in stats.port.exports
    assert "stats::stats::tests::mean_of_three" not in stats.port.exports  # a private test
    assert stats.port.callers == ["src/text.rs", "tests/api.rs"]
    assert stats.note is not None
    assert "c/src/stats.c and c/include/stats/stats.h" in stats.note
    assert stats_tests.port is not None
    assert stats_tests.port.module == "test_stats"
    assert stats_tests.port.tests == [
        "nextest:stats::stats::tests::histogram_bins",
        "nextest:stats::stats::tests::mean_of_three",
        "nextest:stats::stats::tests::median_even_and_odd",
    ]
    assert stats.id in stats_tests.needs
    assert set(api.needs) == {stats.id, text.id}  # what the integration test uses
    assert api.port is not None
    assert api.port.module == "test_api"
    assert set(retire.needs) == {u.id for u in plan.units[:-1]}


def test_unused_generic_items_and_async_code_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, ctx = _index(tmp_path, monkeypatch, DATA / "stats")
    text_rs = ctx.worktree / "src" / "text.rs"
    text_rs.write_text(
        text_rs.read_text(encoding="utf-8") + "\npub fn first<T: Copy>(xs: &[T]) -> T { xs[0] }\n",
        encoding="utf-8",
    )
    project, ctx = _index(tmp_path / "again", monkeypatch, ctx.worktree)
    units = {u.files[-1]: u for u in _plan(project, ctx, ("port_rust_module",)).units}
    assert units["src/text.rs"].status is UnitStatus.SKIPPED
    assert "first" in (units["src/text.rs"].note or "")
    assert units["src/stats.rs"].status is UnitStatus.PENDING


def test_public_items_are_the_api_i6_keeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A port's source keeps its public items behind façades, so they are public API (I-6
    refuses a unit that drops one); private items and a port's target index aren't."""
    project, _ = _index(tmp_path, monkeypatch, DATA / "stats")
    with IndexStore.open(project.index_path) as store:
        boundary = {s.qualified_name: s.boundary.value for s in store.symbols()}
    assert boundary["stats::stats::mean"] == "public_api"
    assert boundary["stats::stats::Histogram::new"] == "public_api"
    assert boundary["stats::stats::tests::mean_of_three"] == "none"


def test_a_procedural_macro_crate_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crate = tmp_path / "src-crate" / "stats"
    shutil.copytree(DATA / "stats", crate)
    manifest = crate / "Cargo.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("[lib]\n", "[lib]\nproc-macro = true\n"),
        encoding="utf-8",
    )
    project, ctx = _index(tmp_path / "work", monkeypatch, crate)
    units = _plan(project, ctx).units
    assert {u.status for u in units} == {UnitStatus.SKIPPED}
    assert all("procedural macro" in (u.note or "") for u in units)


def test_a_workspace_from_a_port_to_rust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The round trip's start (D-75): the rust/ workspace a C → Rust port leaves, with the
    library's package and a program's package that uses it (``data/wf``, a small copy; the
    program's SCIP index is rust-analyzer's, recorded in its member's directory, and the
    library's references are left out). Its modules go first, then the program after the
    modules it uses, the test files, and the retirement with the workspace's manifest and
    lock; tests/test_wf.rs keeps its name."""
    work = tmp_path / "wf"
    shutil.copytree(DATA / "wf", work)
    adapter = RustAdapter()

    def scip(ctx: AdapterContext, package: str, problems: list[str]) -> list[Document]:
        if package == "rust/wordfreq_rs":
            return read_index((DATA / "wordfreq.scip").read_bytes())
        return []

    monkeypatch.setattr(adapter, "_scip", scip)
    executor: Any = object()
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,
        lang=LanguageConfig(language="rust", standard="rust2021", port_target="c"),
    )
    index = adapter.index(ctx)
    assert not index.problems
    project = ProjectModel.load(index.path, work, test_dirs=["tests"])
    build = ["rust/Cargo.lock", "rust/Cargo.toml", "rust/wf_rs/Cargo.toml",
             "rust/wordfreq_rs/Cargo.toml"]  # fmt: skip
    assert sorted(project.build_files) == build
    plan = _plan(project, ctx, ("port_rust_module", "port_rust_program", *RULES[1:]))
    units = {(u.rules[0], u.port.module if u.port else None): u for u in plan.units}
    assert list(units) == [
        ("port_rust_module", "conf"),
        ("port_rust_module", "wf"),
        ("port_rust_program", "wordfreq"),
        ("port_rust_tests", "test_wf"),
        ("port_rust_tests", "test_cli"),
        ("retire_rust", None),
    ]
    conf, wf = units["port_rust_module", "conf"], units["port_rust_module", "wf"]
    program = units["port_rust_program", "wordfreq"]
    assert conf.files == sorted([*build, "rust/wf_rs/src/conf.rs", "rust/wf_rs/src/lib.rs"])
    assert program.port is not None
    assert (program.port.crate, program.port.bin) == ("wf_rs", "wordfreq")
    assert set(program.needs) == {conf.id, wf.id}  # main.rs uses both, through the library
    assert program.note is not None
    assert "wf_rs_wordfreq_main in c/src/wordfreq.c" in program.note
    cli = units["port_rust_tests", "test_cli"]
    assert cli.port is not None
    assert cli.port.tests == ["nextest:wordfreq_rs::cli::an_unknown_option_shows_the_usage"]
    retire = units["retire_rust", None]
    assert {"rust/Cargo.toml", "rust/Cargo.lock", "rust/wordfreq_rs/src/main.rs"} <= set(
        retire.files
    )


@pytest.mark.parametrize(
    ("params", "generic"),
    [("'a", False), ("'a, 'b: 'a", False), ("T", True), ("T: Ord + Copy", True),
     ("const N: usize", True), ("'a, I: IntoIterator<Item = &'a [u8]>", False),
     ("P: AsRef<Path>", False), ("F: Fn(u8, u8) -> bool, T", True),
     ("F: FnMut(&str) -> Option<usize>", False)],
)  # fmt: skip
def test_what_makes_a_c_function_per_type(params: str, generic: bool) -> None:
    """A lifetime alone doesn't, nor a type the façade converts to one argument (an iteration
    over a given item, a conversion, a closure); a type the C must handle does, and a
    constant."""
    from codecoremorph.planner.planner import _Former  # pyright: ignore[reportPrivateUsage]

    assert _Former._type_generic(params) is generic  # pyright: ignore[reportPrivateUsage]


def test_an_unused_function_over_an_iterator_is_ported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shlex's ``bytes::try_join<'a, I: IntoIterator<Item = &'a [u8]>>``, which nothing in the
    crate calls: the façade collects the words and calls one C function, so the module isn't
    refused, and its note says so."""
    project, ctx = _index(tmp_path, monkeypatch, DATA / "stats")
    text_rs = ctx.worktree / "src" / "text.rs"
    text_rs.write_text(
        text_rs.read_text(encoding="utf-8")
        + "\npub fn total<'a, I: IntoIterator<Item = &'a f64>>(xs: I) -> f64 {\n"
        "    xs.into_iter().sum()\n}\n",
        encoding="utf-8",
    )
    project, ctx = _index(tmp_path / "again", monkeypatch, ctx.worktree)
    units = {u.files[-1]: u for u in _plan(project, ctx, ("port_rust_module",)).units}
    assert units["src/text.rs"].status is UnitStatus.PENDING
    assert "total takes an iterator, a conversion or a closure" in (units["src/text.rs"].note or "")


def test_a_packages_files_are_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M10's shlex holds fuzz/, a cargo-fuzz workspace the manifest excludes, and the root
    package's walk took its files, so the retirement would have deleted them: the index takes
    neither an excluded path's files nor those of a package below the root's."""
    crate = tmp_path / "src-crate" / "stats"
    shutil.copytree(DATA / "stats", crate)
    for rel in ("fuzz/fuzz_targets/fuzz_mean.rs", "tools/probe/src/main.rs"):
        (crate / rel).parent.mkdir(parents=True, exist_ok=True)
        (crate / rel).write_text("fn main() {}\n", encoding="utf-8")
    (crate / "fuzz/Cargo.toml").write_text(
        '[package]\nname = "stats-fuzz"\n\n[workspace]\nmembers = ["."]\n', encoding="utf-8"
    )
    (crate / "tools/probe/Cargo.toml").write_text('[package]\nname = "probe"\n', encoding="utf-8")
    project, ctx = _index(tmp_path / "work", monkeypatch, crate)
    rust = sorted(f for f in project.files if f.endswith(".rs"))
    assert "tools/probe/src/main.rs" in rust  # its own package's, not the root's
    ctx = replace(ctx, lang=ctx.lang.model_copy(update={"exclude": ["fuzz/**"]}))
    adapter = RustAdapter()
    monkeypatch.setattr(
        adapter, "_scip", lambda c, p, problems: read_index((DATA / "stats.scip").read_bytes())
    )
    index = adapter.index(ctx)
    files = ProjectModel.load(index.path, ctx.worktree, test_dirs=["tests"]).files
    assert not [f for f in files if f.startswith("fuzz/")]
    retire = _plan(ProjectModel.load(index.path, ctx.worktree, test_dirs=["tests"]), ctx).units[-1]
    assert not [f for f in retire.files if f.startswith("fuzz/")]
