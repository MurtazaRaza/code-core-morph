"""A port unit in the unit runner (D-56): codemods go to the adapter that lists their tool, the
kept headers are read-only while the rule's ``extra_scope`` is writable, a failed build adds
the target adapter's diagnostics, and ``c_abi_kept`` compares the C interfaces."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

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
    LanguageConfig,
    LlmGuidance,
    LoadedRule,
    PortUnits,
    Postcondition,
    PostconditionType,
    Rule,
    RuleKind,
    Severity,
    SymbolIndex,
    TestReport,
    TestSelection,
    ToolchainSpec,
    WorkUnit,
)
from codecoremorph.llm.replay import Responder

from .conftest import FakeAdapter, World, reply, submit

PORT = LoadedRule(
    rule=Rule(
        id="port_geo",
        language="cpp",
        kind=RuleKind.PORT,
        target_language="rust",
        description="Port geo to Rust.",
        detector=Detector(type=DetectorType.PATTERN, patterns=[r"\barea\("]),
        codemods=[CodemodSpec(tool="rust-crate")],
        extra_scope=["rust/**"],
        llm=LlmGuidance(guidance="missing.md"),
        postconditions=[Postcondition(type=PostconditionType.DETECTOR_EMPTY)],
        allow_suppressions=True,
    ),
    pack="test",
    source=Path("rules/port_geo.yaml"),
)
SCAFFOLD = "//! scaffold\n"
EXPORT = re.compile(r"^// export (\w+): (.*)$", re.MULTILINE)
DECLARED = re.compile(r"^abi (\w+): (.*)$", re.MULTILINE)


class SourceAdapter(FakeAdapter):
    """The fake C++ adapter, reading C declarations: ``include/geo.h`` declares ``area``; any
    other header lists ``abi NAME: SIGNATURE`` lines."""

    def c_declarations(self, ctx: AdapterContext, header: Path) -> dict[str, str]:
        self.calls.append(f"c_declarations {header.name}")
        if header.name == "geo.h":
            return {"area": "double (double, double)"}
        return dict(DECLARED.findall(header.read_text(encoding="utf-8")))


class TargetAdapter(LanguageAdapter):
    """A fake Rust adapter: the scaffold writes ``rust/geo``; the build fails on a Rust line
    with ``E0308``; the exported header lists the ``// export NAME: SIGNATURE`` lines."""

    language = "rust"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(exported_c_headers=True, codemod_tools=["rust-crate"])

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(language="rust", image="x")

    def configure(self, ctx: AdapterContext) -> BuildResult:
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "c.log")

    def _rust(self, ctx: AdapterContext) -> dict[str, str]:
        return {
            p.relative_to(ctx.worktree).as_posix(): p.read_text(encoding="utf-8")
            for p in (ctx.worktree / "rust").rglob("*.rs")
        }

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        self.calls.append("build")
        errors = [
            Diagnostic(
                file_path=path,
                line=n,
                column=1,
                severity=Severity.ERROR,
                code="E0308",
                message="mismatched types: expected `f64`, found `i32`",
                origin=DiagnosticOrigin.COMPILER,
            )
            for path, text in sorted(self._rust(ctx).items())
            for n, line in enumerate(text.splitlines(), start=1)
            if "BROKEN" in line
        ]
        return BuildResult(
            success=not errors, diagnostics=errors, duration_s=0, log_path=ctx.log_dir / "b.log"
        )

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self.calls.append("tests")  # a port runs the new code's tests too (D-60)
        return TestReport(cases=[], duration_s=0, log_path=ctx.log_dir / "t.log")

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        raise NotImplementedError

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        self.calls.append(f"codemods {[s.tool for s in codemods]} {files}")
        lib = ctx.worktree / "rust" / "geo" / "src" / "lib.rs"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_text(SCAFFOLD, encoding="utf-8")
        (ctx.worktree / "rust" / "geo" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        changes = [
            CodemodChange(file="rust/geo/Cargo.toml", replacements=1),
            CodemodChange(file="rust/geo/src/lib.rs", replacements=1),
        ]
        return CodemodResult(changes=changes, duration_s=0)

    def exported_c_headers(self, ctx: AdapterContext, files: list[str]) -> list[Path]:
        self.calls.append(f"exported_c_headers {files}")
        exports = [f"abi {n}: {s}" for t in self._rust(ctx).values() for n, s in EXPORT.findall(t)]
        header = ctx.cache_dir / "exported.h"
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text("\n".join(exports) + "\n", encoding="utf-8")
        return [header]


def _port_runner(world: World, responses: list[Responder]) -> tuple[UnitRunner, TargetAdapter]:
    world.adapter = SourceAdapter()
    runner, _ = world.runner(responses)
    target = TargetAdapter()
    runner.adapter = world.adapter
    runner.target_adapter = target
    runner.target_ctx = replace(
        world.ctx,
        cache_dir=world.ctx.cache_dir / "rust",
        lang=LanguageConfig(language="rust", standard="rust2021", target_standard="rust2021"),
    )
    return runner, target


def _unit() -> WorkUnit:
    return WorkUnit(
        id="U-0001",
        seq=1,
        rules=["port_geo"],
        files=["CMakeLists.txt", "include/geo.h", "src/geo.cpp"],
        reference_files=["include/geo.h"],
    )


def _edits(lib: str) -> list[dict[str, str]]:
    return [
        {"op": "replace", "path": "rust/geo/src/lib.rs", "search": SCAFFOLD, "replace": lib},
        {"op": "delete", "path": "src/geo.cpp"},
    ]


def test_a_port_unit(world: World) -> None:
    lib = SCAFFOLD + "// export area: double (double, double)\n"
    runner, target = _port_runner(world, [reply(submit("s1", *_edits(lib)))])
    outcome = runner.run(_unit(), [PORT])
    assert outcome.succeeded, outcome.reason
    assert not (world.worktree.path / "src" / "geo.cpp").exists()
    # The scaffold went to the target adapter, with the unit's own files (not its header).
    assert target.calls[0] == "codemods ['rust-crate'] ['CMakeLists.txt', 'src/geo.cpp']"
    assert "codemods" not in world.adapter.calls
    assert "exported_c_headers ['rust/geo/Cargo.toml', 'rust/geo/src/lib.rs']" in target.calls
    assert sorted(outcome.changed_files) == [
        "rust/geo/Cargo.toml",
        "rust/geo/src/lib.rs",
        "src/geo.cpp",
    ]


def test_the_exported_interface_must_match(world: World) -> None:
    wrong = SCAFFOLD + "// export area: int (int, int)\n"
    right = "// export area: int (int, int)\n"
    fixed = "// export area: double (double, double)\n"
    fix = {"op": "replace", "path": "rust/geo/src/lib.rs", "search": right, "replace": fixed}
    runner, _ = _port_runner(
        world,
        [reply(submit("s1", *_edits(wrong))), reply(submit("s2", fix))],
    )
    outcome = runner.run(_unit(), [PORT])
    assert outcome.succeeded, outcome.reason
    first, second = outcome.attempts
    assert first.outcome is AttemptOutcome.INVARIANT_FAILED
    assert second.outcome is AttemptOutcome.SUCCEEDED
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "port_geo: the new code must export the C interface of include/geo.h" in feedback
    assert "`area` is exported as `int (int, int)`, but the header declares" in feedback


def test_the_kept_header_is_read_only(world: World) -> None:
    runner, _ = _port_runner(world, [])
    unit = _unit()
    assert runner.allowed("rust/geo/src/lib.rs", unit, [PORT])
    assert runner.allowed("CMakeLists.txt", unit, [PORT])
    assert runner.allowed("src/geo.cpp", unit, [PORT])
    assert not runner.allowed("include/geo.h", unit, [PORT])
    assert not runner.allowed("src/other.cpp", unit, [PORT])
    batch = {
        "edits": [{"op": "replace", "path": "include/geo.h", "search": "#pragma", "replace": "x"}],
        "summary": "s",
    }
    from codecoremorph.core.models import EditBatch

    rejection = runner.apply_edits(EditBatch.model_validate(batch), unit, [PORT])
    assert rejection is not None
    assert "A port keeps its headers as they are (include/geo.h)" in rejection
    assert "files under rust/**" in rejection


def test_a_failed_build_adds_the_target_diagnostics(world: World) -> None:
    broken = SCAFFOLD + "let x: f64 = BROKEN;\n"
    runner, target = _port_runner(world, [reply(submit("s1", *_edits(broken)))])
    runner.settings = replace(runner.settings, max_retry_budget=0)
    outcome = runner.run(_unit(), [PORT])
    assert not outcome.succeeded
    assert outcome.attempts[0].outcome is AttemptOutcome.BUILD_FAILED
    assert "build" in target.calls
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "mismatched types: expected `f64`, found `i32`" in feedback
    assert "rust/geo/src/lib.rs" in feedback


@pytest.mark.parametrize("exports", [["area"], []])
def test_a_c_unit_exports_only_its_own_functions(world: World, exports: list[str]) -> None:
    """A C translation unit's header also declares other units' functions (D-68): only its
    exports must come from the new code."""
    from codecoremorph.core.models import PortUnit

    lib = SCAFFOLD + "// export area: double (double, double)\n"
    runner, _ = _port_runner(world, [reply(submit("s1", *_edits(lib)))])
    source = runner.adapter
    assert isinstance(source, SourceAdapter)
    read = source.c_declarations

    def declarations(ctx: AdapterContext, header: Path) -> dict[str, str]:
        if header.name != "geo.h":
            return read(ctx, header)  # the crate's exported header
        return {"area": "double (double, double)", "perimeter": "double (double, double)"}

    source.c_declarations = declarations  # type: ignore[method-assign]
    port = PortUnit(
        build_target="geo",
        crate="geo",
        module="geo",
        c_headers=["include/geo.h"],
        exports=exports,
    )
    runner.settings = replace(runner.settings, max_retry_budget=0)
    outcome = runner.run(_unit().model_copy(update={"port": port}), [PORT])
    if exports:
        assert outcome.succeeded, outcome.reason
    else:  # a module port's headers are its own: all of them is the interface
        assert outcome.attempts[0].outcome is AttemptOutcome.INVARIANT_FAILED
        feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
        assert "`perimeter` isn't exported" in feedback


RETIRE = LoadedRule(
    rule=Rule(
        id="retire_geo",
        language="cpp",
        kind=RuleKind.PORT,
        target_language="rust",
        description="Retire the C.",
        port_units=PortUnits.RETIREMENT,
        detector=Detector(type=DetectorType.FILES),
        extra_scope=["rust/**"],
    ),
    pack="test",
    source=Path("rules/retire_geo.yaml"),
)


def _kept_api_retirement(world: World, export: str) -> tuple[UnitRunner, WorkUnit]:
    """A crate the earlier units committed, and a kept public header that declares ``area``
    and a variadic logger, which stays C (D-72)."""
    root = world.worktree.path
    (root / "include" / "api.h").write_text(
        "abi area: double (double, double)\nabi geo_log: void (const char *, ...)\n",
        encoding="utf-8",
    )
    lib = root / "rust" / "geo" / "src" / "lib.rs"
    lib.parent.mkdir(parents=True)
    lib.write_text(SCAFFOLD + export, encoding="utf-8")
    (root / "rust" / "geo" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    world.worktree.commit("the port so far")
    runner, _ = _port_runner(world, [])
    runner.keep_source_api = True
    unit = WorkUnit(
        id="U-0009",
        seq=9,
        rules=["retire_geo"],
        files=["include/api.h", "src/geo.cpp"],
        reference_files=["include/api.h"],
        kept_api=["include/api.h"],
    )
    return runner, unit


def test_a_kept_api_is_checked_against_every_crate(world: World) -> None:
    # The retirement changed no crate: the crates the earlier units wrote still count.
    runner, unit = _kept_api_retirement(world, "// export area: double (double, double)\n")
    problems, _, _ = runner.postconditions(unit, [RETIRE], {})
    assert problems == []  # geo_log takes `...`: it stays C, so no crate exports it


def test_a_kept_api_needs_each_of_its_functions(world: World) -> None:
    runner, unit = _kept_api_retirement(world, "// export other: void (void)\n")
    problems, _, _ = runner.postconditions(unit, [RETIRE], {})
    [problem] = problems
    assert "retire_geo: the new code must export the C interface of include/api.h" in problem
    assert "`area` isn't exported" in problem
    assert "geo_log" not in problem


def test_a_kept_api_retirement_may_edit_the_build(world: World) -> None:
    runner, unit = _kept_api_retirement(world, "")
    assert runner.allowed("CMakeLists.txt", unit, [RETIRE])  # the kept build names the C
    assert runner.allowed("src/geo.cpp", unit, [RETIRE])
    assert not runner.allowed("include/api.h", unit, [RETIRE])  # the kept API is read-only
    runner.keep_source_api = False
    assert not runner.allowed("CMakeLists.txt", unit, [RETIRE])
