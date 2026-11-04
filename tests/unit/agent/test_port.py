"""A port unit in the unit runner (D-56): codemods go to the adapter that lists their tool, the
kept headers are read-only while the rule's ``extra_scope`` is writable, a failed build adds
the target adapter's diagnostics, and ``c_abi_kept`` compares the C interfaces."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

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
        raise NotImplementedError

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
