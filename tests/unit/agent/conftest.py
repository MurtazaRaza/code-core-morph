"""A scripted world for agent tests: a temporary repository and run worktree, a fake adapter
whose build and tests react to the files' text, and a scripted model."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.agent.loop import AgentSettings, UnitRunner
from codecoremorph.config.models import LlmConfig, RoleModelConfig
from codecoremorph.core.models import (
    AdapterCapabilities,
    Baseline,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    CompletionResult,
    Detector,
    DetectorType,
    Diagnostic,
    DiagnosticOrigin,
    LanguageConfig,
    LlmGuidance,
    LoadedRule,
    Postcondition,
    PostconditionType,
    Role,
    Rule,
    RuleKind,
    Severity,
    StopReason,
    SymbolIndex,
    TestBaseline,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    TextBlock,
    ToolCall,
    ToolchainSpec,
    Usage,
    WorkUnit,
)
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Responder, ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.sandbox.executor import Executor
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.repository import Repository
from codecoremorph.vcs.worktree import Worktree

GitRunner = Callable[..., str]
ORDER = StandardOrder("cpp", ("cpp98", "cpp03", "cpp11", "cpp14", "cpp17", "cpp20", "cpp23"))
RUN_ID = "20261102-150000-ab12"

TYPEDEFS = LoadedRule(
    rule=Rule(
        id="replace_typedefs_with_using",
        language="cpp",
        kind=RuleKind.API_PRESERVING,
        description="Replace typedef declarations with alias declarations.",
        detector=Detector(type=DetectorType.PATTERN, patterns=[r"\btypedef\b"]),
        codemods=[CodemodSpec(tool="clang-tidy", checks=["modernize-use-using"])],
        llm=LlmGuidance(guidance="missing.md"),
        postconditions=[Postcondition(type=PostconditionType.DETECTOR_EMPTY, severity="error")],
    ),
    pack="test",
    source=Path("rules/replace_typedefs_with_using.yaml"),
)
_SIMPLE_TYPEDEF = re.compile(r"^typedef (\w+) (\w+);$", re.MULTILINE)


class FakeAdapter(LanguageAdapter):
    """The build fails on a line containing BROKEN; the test fails on REGRESS; NEWTEST_PASS or
    NEWTEST_FAIL adds a test the baseline doesn't have; the codemod rewrites simple
    `typedef T N;` lines."""

    language = "cpp"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(standards=list(ORDER.standards))

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(language="cpp", image="x")

    def _files(self, ctx: AdapterContext) -> dict[str, str]:
        return {
            p.relative_to(ctx.worktree).as_posix(): p.read_text(encoding="utf-8")
            for p in ctx.worktree.rglob("*.*")
            if p.is_file() and ".git" not in p.parts
        }

    def configure(self, ctx: AdapterContext) -> BuildResult:
        self.calls.append(f"configure {ctx.lang.standard}")
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "configure.log")

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        self.calls.append("build")
        errors = [
            Diagnostic(
                file_path=path,
                line=number,
                column=1,
                severity=Severity.ERROR,
                message="use of undeclared identifier 'BROKEN'",
                origin=DiagnosticOrigin.COMPILER,
            )
            for path, text in sorted(self._files(ctx).items())
            for number, line in enumerate(text.splitlines(), start=1)
            if "BROKEN" in line
        ]
        log = ctx.log_dir / "build.log"
        log.write_text("\n".join(e.message for e in errors), encoding="utf-8")
        return BuildResult(success=not errors, diagnostics=errors, duration_s=0, log_path=log)

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self.calls.append("test")
        regress = any("REGRESS" in text for text in self._files(ctx).values())
        status = TestStatus.FAILED if regress else TestStatus.PASSED
        cases = [
            TestCaseResult(test_id="gtest:Geo.Area", status=status, duration_s=0,
                           message="expected 4, got 5" if regress else None),
            TestCaseResult(test_id="gtest:Geo.Flaky", status=TestStatus.FAILED, duration_s=0),
        ]  # fmt: skip
        text = "\n".join(self._files(ctx).values())
        if "NEWTEST_" in text:
            fails = "NEWTEST_FAIL" in text
            cases.append(
                TestCaseResult(
                    test_id="gtest:Geo.New",
                    status=TestStatus.FAILED if fails else TestStatus.PASSED,
                    duration_s=0,
                    message="expected 0x690ce0ee, got 0xc5a92679" if fails else None,
                )
            )
        return TestReport(cases=cases, duration_s=0, log_path=ctx.log_dir / "test.log")

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        raise NotImplementedError

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        self.calls.append("codemods")
        changes: list[CodemodChange] = []
        for path in files:
            host = ctx.worktree / path
            text = host.read_text(encoding="utf-8")
            new, count = _SIMPLE_TYPEDEF.subn(r"using \2 = \1;", text)
            if count:
                host.write_text(new, encoding="utf-8")
                changes.append(CodemodChange(file=path, replacements=count))
        return CodemodResult(changes=changes, duration_s=0)


def reply(*blocks: TextBlock | ToolCall, stop: StopReason | None = None) -> CompletionResult:
    calls = [b for b in blocks if isinstance(b, ToolCall)]
    return CompletionResult(
        provider="anthropic",
        model="claude-opus-5-5",
        content=list(blocks) or [TextBlock(text="")],
        stop_reason=stop or (StopReason.TOOL_USE if calls else StopReason.END),
        usage=Usage(input_tokens=1000, output_tokens=100),
        cost_usd=0.01,
    )


def call(name: str, call_id: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def submit(
    call_id: str, *edits: dict[str, str], summary: str = "Done.", notes: tuple[str, ...] = ()
) -> ToolCall:
    return call(
        "submit_edits", call_id, edits=list(edits), summary=summary, review_notes=list(notes)
    )


def replace_edit(path: str, search: str, new: str) -> dict[str, str]:
    return {"op": "replace", "path": path, "search": search, "replace": new}


class PathOnlyExecutor:
    """The fake adapter runs no commands; only path translation is needed."""

    kind = "native"

    def to_sandbox_path(self, host_path: Path) -> str:
        return host_path.as_posix()


@dataclass
class World:
    repo: Path
    worktree: Worktree
    adapter: FakeAdapter
    ctx: AdapterContext
    tmp: Path

    def runner(
        self, responses: list[Responder], *, settings: AgentSettings | None = None
    ) -> tuple[UnitRunner, ScriptedProvider]:
        scripted = ScriptedProvider(responses)
        llm = LLMService(
            LlmConfig(roles={Role.EDITOR: RoleModelConfig(provider="anthropic",
                                                          model="claude-opus-5-5", effort="high")}),
            ledger=UsageLedger(),
            providers={"anthropic": scripted},
        )  # fmt: skip
        baseline = Baseline(
            base_commit=self.worktree.head(),
            standard="cpp11",
            tests=TestBaseline(runs=2, passing=["gtest:Geo.Area"], failing=["gtest:Geo.Flaky"]),
            configure_s=1,
            build_s=1,
            test_s=1,
        )
        runner = UnitRunner(
            adapter=self.adapter,
            ctx=self.ctx,
            worktree=self.worktree,
            baseline=baseline,
            llm=llm,
            order=ORDER,
            source="cpp11",
            target="cpp20",
            run_id=RUN_ID,
            artifacts=StateDir(self.tmp / "state").run(RUN_ID).ensure(),
            index_path=None,
            build_files=["CMakeLists.txt"],
            scope_dirs=["src", "include"],
            settings=settings or AgentSettings(max_retry_budget=2, max_edit_retries=1),
        )
        return runner, scripted

    def read(self, path: str) -> str:
        return (self.worktree.path / path).read_text(encoding="utf-8")


def unit(*files: str) -> WorkUnit:
    return WorkUnit(id="U-0001", seq=1, rules=["replace_typedefs_with_using"], files=list(files))


@pytest.fixture
def world(tmp_path: Path, git: GitRunner) -> World:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "include").mkdir()
    (repo / "CMakeLists.txt").write_text("project(geo)\n", encoding="utf-8")
    (repo / "include" / "geo.h").write_text(
        "#pragma once\ntypedef double Scalar;\n#define DECLARE(T, N) typedef T N\n"
        "DECLARE(int, Id);\n",
        encoding="utf-8",
    )
    (repo / "src" / "geo.cpp").write_text(
        '#include "geo.h"\nScalar area(Scalar w, Scalar h) { return w * h; }\n', encoding="utf-8"
    )
    (repo / "src" / "other.cpp").write_text("int other() { return 1; }\n", encoding="utf-8")
    git(tmp_path, "init", "--quiet", str(repo))
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "legacy")
    repository = Repository(repo)
    worktree = repository.add_worktree(
        tmp_path / "wt", f"codecoremorph/{RUN_ID}", repository.resolve_commit("HEAD")
    )
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    executor: Executor = PathOnlyExecutor()  # type: ignore[assignment]  # runs nothing
    ctx = AdapterContext(
        worktree=worktree.path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,
        lang=LanguageConfig(language="cpp", standard="cpp11", target_standard="cpp20"),
    )
    return World(repo, worktree, FakeAdapter(), ctx, tmp_path)
