"""Finishing a port in the unit runner (D-60): a port runs the target's tests beside the
source's, the `test_map` post-condition, parity through the map, and the retirement, after which
the target alone builds and tests."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.agent.loop import UnitRunner
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    LanguageConfig,
    LoadedRule,
    PortUnit,
    PortUnits,
    Postcondition,
    PostconditionType,
    Rule,
    RuleKind,
    SymbolIndex,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    ToolchainSpec,
    WorkUnit,
)
from codecoremorph.llm.replay import Responder

from .conftest import FakeAdapter, GitRunner, World, reply, submit

TEST_LINE = re.compile(r"^// test (\S+) (PASS|FAIL)$", re.MULTILINE)
MAP_HEADER = "# the test map\n"
TEST_STUB = "//! translated\n"


def _rule(rule_id: str, units: PortUnits, tool: str) -> LoadedRule:
    return LoadedRule(
        rule=Rule(
            id=rule_id,
            language="cpp",
            kind=RuleKind.PORT,
            port_units=units,
            target_language="rust",
            description=rule_id,
            codemods=[CodemodSpec(tool=tool)],
            extra_scope=["rust/**"],
            postconditions=[Postcondition(type=PostconditionType.TEST_MAP)],
        ),
        pack="test",
        source=Path(f"rules/{rule_id}.yaml"),
    )


TESTS = _rule("port_tests", PortUnits.TESTS, "rust-tests")
RETIRE = _rule("retire_cpp", PortUnits.RETIREMENT, "rust-retire")


class TestsTarget(LanguageAdapter):
    """A fake Rust adapter: `// test ID PASS|FAIL` lines in the crates' files are its tests; the
    tests scaffold writes a test file and the map; the retirement deletes the C++ it is given."""

    __test__ = False  # not a pytest test class
    language = "rust"

    def __init__(self) -> None:
        self.options: list[dict[str, str]] = []
        self.calls: list[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(codemod_tools=["rust-tests", "rust-retire"])

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(language="rust", image="x")

    def builds(self, ctx: AdapterContext) -> bool:
        return (ctx.worktree / "rust").is_dir()

    def configure(self, ctx: AdapterContext) -> BuildResult:
        self.calls.append("configure")
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "c.log")

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        self.calls.append("build")
        return BuildResult(success=True, duration_s=0, log_path=ctx.log_dir / "b.log")

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self.calls.append("tests")
        cases = [
            TestCaseResult(
                test_id=test_id,
                status=TestStatus.PASSED if result == "PASS" else TestStatus.FAILED,
                duration_s=0,
            )
            for path in sorted((ctx.worktree / "rust").rglob("*.rs"))
            for test_id, result in TEST_LINE.findall(path.read_text(encoding="utf-8"))
        ]
        return TestReport(cases=cases, duration_s=0, log_path=ctx.log_dir / "t.log")

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        raise NotImplementedError

    def test_map_path(self) -> str:
        return "rust/test-map.toml"

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        changes: list[CodemodChange] = []
        for spec in codemods:
            self.options.append(dict(spec.options))
            if spec.tool == "rust-tests":
                test = ctx.worktree / "rust" / "geo_rs" / "tests" / "geo_test.rs"
                test.parent.mkdir(parents=True, exist_ok=True)
                test.write_text(TEST_STUB, encoding="utf-8")
                (ctx.worktree / "rust" / "test-map.toml").write_text(MAP_HEADER, encoding="utf-8")
                changes.append(CodemodChange(file="rust/geo_rs/tests/geo_test.rs", replacements=1))
            else:
                for rel in files:
                    if not rel.startswith("rust/") and (ctx.worktree / rel).is_file():
                        (ctx.worktree / rel).unlink()
                        changes.append(CodemodChange(file=rel, replacements=1))
        return CodemodResult(changes=changes, duration_s=0)


class RetiringSource(FakeAdapter):
    """The fake C++ adapter, whose build is there while CMakeLists.txt is."""

    def builds(self, ctx: AdapterContext) -> bool:
        return (ctx.worktree / "CMakeLists.txt").is_file()


def _runner(world: World, responses: list[Responder]) -> tuple[UnitRunner, TestsTarget]:
    runner, _ = world.runner(responses)
    target = TestsTarget()
    runner.target_adapter = target
    runner.target_ctx = replace(
        world.ctx,
        cache_dir=world.ctx.cache_dir / "rust",
        lang=LanguageConfig(language="rust", standard="rust2021", target_standard="rust2021"),
    )
    return runner, target


def _tests_unit() -> WorkUnit:
    return WorkUnit(
        id="U-0001",
        seq=1,
        rules=["port_tests"],
        files=["src/geo.cpp"],
        reference_files=["src/geo.cpp"],
        port=PortUnit(
            build_target="geo", crate="geo_rs", module="geo_test", tests=["gtest:Geo.Area"]
        ),
    )


def _test_file(*lines: str) -> dict[str, str]:
    return {
        "op": "replace",
        "path": "rust/geo_rs/tests/geo_test.rs",
        "search": TEST_STUB,
        "replace": TEST_STUB + "".join(f"{line}\n" for line in lines),
    }


ENTRY = '[[test]]\ncpp = "gtest:Geo.Area"\nrust = "nextest:geo_rs::geo_test::area"\n'


def test_a_test_files_unit_maps_every_test(world: World) -> None:
    passing = "// test nextest:geo_rs::geo_test::area PASS"
    mapped = {"op": "replace", "path": "rust/test-map.toml", "search": MAP_HEADER,
              "replace": MAP_HEADER + ENTRY}  # fmt: skip
    runner, target = _runner(
        world, [reply(submit("s1", _test_file(passing))), reply(submit("s2", mapped))]
    )
    outcome = runner.run(_tests_unit(), [TESTS])
    assert outcome.succeeded, outcome.reason
    # The scaffold learns the unit's tests too (a Rust → C test file's names come from them).
    assert target.options == [
        {"crate": "geo_rs", "module": "geo_test", "build_target": "geo", "tests": "gtest:Geo.Area"}
    ]
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "port_tests: the test map rust/test-map.toml isn't whole yet" in feedback
    assert "gtest:Geo.Area has no entry" in feedback
    # Both sides' tests ran in the verification.
    assert "test" in world.adapter.calls
    assert "tests" in target.calls


def test_a_mapped_test_must_pass_while_the_original_runs(world: World) -> None:
    failing = "// test nextest:geo_rs::geo_test::area FAIL"
    first = [
        _test_file(failing),
        {"op": "replace", "path": "rust/test-map.toml", "search": MAP_HEADER,
         "replace": MAP_HEADER + ENTRY},
    ]  # fmt: skip
    fix = {
        "op": "replace",
        "path": "rust/geo_rs/tests/geo_test.rs",
        "search": "area FAIL",
        "replace": "area PASS",
    }
    runner, _ = _runner(world, [reply(submit("s1", *first)), reply(submit("s2", fix))])
    outcome = runner.run(_tests_unit(), [TESTS])
    assert outcome.succeeded, outcome.reason
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "nextest:geo_rs::geo_test::area" in feedback  # a regression through the map (I-5)


def test_the_retirement_leaves_the_target_alone(world: World, git: GitRunner) -> None:
    rust = world.worktree.path / "rust"
    (rust / "geo_rs" / "tests").mkdir(parents=True)
    (rust / "geo_rs" / "tests" / "geo_test.rs").write_text(
        "// test nextest:geo_rs::geo_test::area PASS\n", encoding="utf-8"
    )
    (rust / "test-map.toml").write_text(MAP_HEADER + ENTRY, encoding="utf-8")
    git(world.worktree.path, "add", "--all")
    git(world.worktree.path, "commit", "--quiet", "-m", "the port so far")
    runner, target = _runner(world, [])
    source = RetiringSource()
    runner.adapter = source
    runner.keep_source_api = True
    unit = WorkUnit(
        id="U-0001", seq=1, rules=["retire_cpp"], files=["CMakeLists.txt", "src/geo.cpp"]
    )
    outcome = runner.run(unit, [RETIRE])
    assert outcome.succeeded, outcome.reason
    assert outcome.agent_skipped  # the codemod did it all
    assert sorted(outcome.changed_files) == ["CMakeLists.txt", "src/geo.cpp"]
    assert target.options == [{"keep": "true"}]
    # The source's build is gone: the target alone configured, built and tested the tree, and
    # its test stood for the baseline's C++ test.
    assert "build" not in source.calls
    assert "test" not in source.calls
    assert target.calls == ["configure", "build", "tests"]
