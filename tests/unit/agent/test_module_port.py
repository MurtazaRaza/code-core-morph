"""A module port's unit in the unit runner (D-58): the scaffold codemod learns the unit's crate,
module and build target; `unsafe_in_bridge`, `lint_clean` and `symbol_map` judge the new code;
the C++ that keeps using the module is outside the unit."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.agent.loop import UnitRunner
from codecoremorph.core.models import (
    AdapterCapabilities,
    AttemptOutcome,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Detector,
    DetectorType,
    Diagnostic,
    DiagnosticOrigin,
    EditBatch,
    IndexMeta,
    LanguageConfig,
    LoadedRule,
    PortUnit,
    PortUnits,
    Postcondition,
    PostconditionType,
    Rule,
    RuleKind,
    RuleSite,
    Severity,
    SymbolIndex,
    TestReport,
    TestSelection,
    ToolchainSpec,
    WorkUnit,
)
from codecoremorph.llm.replay import Responder
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

from .conftest import World, reply, submit

MODULE = LoadedRule(
    rule=Rule(
        id="port_module",
        language="cpp",
        kind=RuleKind.PORT,
        port_units=PortUnits.MODULE,
        target_language="rust",
        description="Port a module to Rust.",
        detector=Detector(type=DetectorType.PATTERN, patterns=[r"\bw \* h\b"]),
        codemods=[CodemodSpec(tool="rust-module")],
        extra_scope=["rust/**"],
        postconditions=[
            Postcondition(type=PostconditionType.DETECTOR_EMPTY),
            Postcondition(type=PostconditionType.UNSAFE_IN_BRIDGE),
            Postcondition(type=PostconditionType.LINT_CLEAN),
            Postcondition(type=PostconditionType.SYMBOL_MAP),
        ],
    ),
    pack="test",
    source=Path("rules/port_module.yaml"),
)
STUB = "//! stub\n"
MAP = "# the map\n"
ITEM = re.compile(r"^// item (\S+)$", re.MULTILINE)


class ModuleTarget(LanguageAdapter):
    """A fake Rust adapter for module ports: `// item PATH` lines are the crate's items,
    `UNSAFE` allows unsafe code, `LINT` draws a warning."""

    language = "rust"

    def __init__(self) -> None:
        self.options: list[dict[str, str]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(codemod_tools=["rust-module"])

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(language="rust", image="x")

    def configure(self, ctx: AdapterContext) -> BuildResult:
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "c.log")

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "b.log")

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        raise NotImplementedError

    def _rust(self, ctx: AdapterContext) -> dict[str, str]:
        return {
            p.relative_to(ctx.worktree).as_posix(): p.read_text(encoding="utf-8")
            for p in (ctx.worktree / "rust").rglob("*.rs")
        }

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        path = ctx.cache_dir / INDEX_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            (f"rust:{item}", item.rsplit("::", 1)[-1], item, "function", rel, 1, 1, 1, 1, None,
             "public", 1, "none")
            for rel, text in self._rust(ctx).items()
            for item in ITEM.findall(text)
        ]  # fmt: skip
        meta = IndexMeta(adapter="rust", adapter_version="1")
        with IndexStore.open(path, reset=True) as store:
            store.write(symbols=rows)
            store.set_meta(meta)
        return SymbolIndex(path=path, meta=meta)

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        self.options += [dict(s.options) for s in codemods]
        module = ctx.worktree / "rust" / "geo_rs" / "src" / "geo.rs"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(STUB, encoding="utf-8")
        (ctx.worktree / "rust" / "symbol-map.toml").write_text(MAP, encoding="utf-8")
        changes = [
            CodemodChange(file="rust/geo_rs/src/geo.rs", replacements=1),
            CodemodChange(file="rust/symbol-map.toml", replacements=1),
        ]
        return CodemodResult(changes=changes, duration_s=0)

    def symbol_map_path(self) -> str:
        return "rust/symbol-map.toml"

    def unsafe_sites(self, ctx: AdapterContext) -> list[RuleSite]:
        return [
            RuleSite(file=rel, line=n, text="allows unsafe code")
            for rel, text in self._rust(ctx).items()
            for n, line in enumerate(text.splitlines(), start=1)
            if "UNSAFE" in line
        ]

    def lint(self, ctx: AdapterContext) -> list[Diagnostic]:
        return [
            Diagnostic(
                file_path=rel,
                line=n,
                severity=Severity.WARNING,
                code="clippy::needless_return",
                message="unneeded `return` statement",
                origin=DiagnosticOrigin.ANALYZER,
            )
            for rel, text in self._rust(ctx).items()
            for n, line in enumerate(text.splitlines(), start=1)
            if "LINT" in line
        ]


def _runner(world: World, responses: list[Responder]) -> tuple[UnitRunner, ModuleTarget]:
    runner, _ = world.runner(responses)
    target = ModuleTarget()
    runner.target_adapter = target
    runner.target_ctx = replace(
        world.ctx,
        cache_dir=world.ctx.cache_dir / "rust",
        lang=LanguageConfig(language="rust", standard="rust2021", target_standard="rust2021"),
    )
    # The source index: include/geo.h declares geo::area publicly.
    index = world.tmp / "source-index.sqlite"
    with IndexStore.open(index, reset=True) as store:
        store.write(
            files=[("include/geo.h", "cpp", "0" * 64, "header", 0, 1, "utf-8", "lf")],
            symbols=[
                ("c:@F@area", "area", "geo::area", "function", "include/geo.h", 1, 1, 1, 9,
                 None, "none", 0, "public_api"),
            ],
        )  # fmt: skip
    runner.index_path = index
    return runner, target


def _unit() -> WorkUnit:
    return WorkUnit(
        id="U-0001",
        seq=1,
        rules=["port_module"],
        files=["CMakeLists.txt", "include/geo.h", "src/geo.cpp"],
        port=PortUnit(build_target="geo", crate="geo_rs", module="geo", callers=["src/other.cpp"]),
    )


FORWARD = {
    "op": "replace",
    "path": "src/geo.cpp",
    "search": "return w * h;",
    "replace": "return geo_rs_geo_area(w, h);",
}


def _module(text: str) -> dict[str, str]:
    return {"op": "replace", "path": "rust/geo_rs/src/geo.rs", "search": STUB, "replace": text}


def test_the_checks_of_a_module_port(world: World) -> None:
    loose = STUB + "// item geo_rs::geo::area\n#[allow(unsafe_code)] UNSAFE\nreturn x; // LINT\n"
    clean = "// item geo_rs::geo::area\n"
    entry = '\n[[symbol]]\ncpp = "geo::area"\nrust = "geo_rs::geo::area"\n'
    fix = [
        {"op": "replace", "path": "rust/geo_rs/src/geo.rs", "search": loose, "replace": clean},
        {"op": "replace", "path": "rust/symbol-map.toml", "search": MAP, "replace": MAP + entry},
    ]
    runner, target = _runner(
        world, [reply(submit("s1", FORWARD, _module(loose))), reply(submit("s2", *fix))]
    )
    outcome = runner.run(_unit(), [MODULE])
    assert outcome.succeeded, outcome.reason
    # The scaffold heard the unit's names.
    assert target.options == [{"crate": "geo_rs", "module": "geo", "build_target": "geo"}]
    first, second = outcome.attempts
    assert first.outcome is not AttemptOutcome.SUCCEEDED
    assert second.outcome is AttemptOutcome.SUCCEEDED
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "port_module: unsafe code must stay in the bridge" in feedback
    assert "- rust/geo_rs/src/geo.rs:3: allows unsafe code" in feedback
    assert "port_module: the linter reports 1 problem(s) in the new code" in feedback
    assert "unneeded `return` statement [clippy::needless_return]" in feedback
    assert "geo::area (declared in include/geo.h) has no entry" in feedback


def test_the_callers_stay_as_they_are(world: World) -> None:
    runner, _ = _runner(world, [])
    unit = _unit()
    assert runner.allowed("src/geo.cpp", unit, [MODULE])
    assert runner.allowed("include/geo.h", unit, [MODULE])  # a façade now: I-6 keeps its API
    assert runner.allowed("rust/geo_rs/src/geo.rs", unit, [MODULE])
    assert not runner.allowed("src/other.cpp", unit, [MODULE])
    batch = EditBatch.model_validate(
        {
            "edits": [{"op": "replace", "path": "src/other.cpp", "search": "1", "replace": "2"}],
            "summary": "s",
        }
    )
    rejection = runner.apply_edits(batch, unit, [MODULE])
    assert rejection is not None
    assert "A module port changes its own files" in rejection
    assert "keeps using it through its headers, unchanged" in rejection


def test_the_new_code_s_scope_holds_no_way_out(world: World) -> None:
    """`rust/**` lets a port create files under rust/, and nowhere else through it: a path that
    climbs out doesn't parse, and a look-alike isn't in the scope."""
    runner, _ = _runner(world, [])
    for path in ("rust/../src/other.cpp", "rust/./x.rs", "rust/x/../../outside.txt", "/rust/x"):
        with pytest.raises(ValidationError):
            EditBatch.model_validate(
                {"edits": [{"op": "create", "path": path, "content": "pwned"}], "summary": "s"}
            )
    unit = _unit()
    assert runner.allowed("rust/geo_rs/src/new.rs", unit, [MODULE])
    for path in ("rustx/lib.rs", "src/rust/lib.rs", "rust.toml"):
        assert not runner.allowed(path, unit, [MODULE]), path
