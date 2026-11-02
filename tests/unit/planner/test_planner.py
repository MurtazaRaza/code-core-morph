"""Unit formation, ordering, sizing and flags (FR-5.1 to FR-5.6, SPEC §7.5.1)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.config.models import ModelPrice
from codecoremorph.core.models import (
    AppliesTo,
    CodemodSpec,
    Detector,
    DetectorType,
    LlmGuidance,
    LoadedRule,
    PatternSpec,
    Plan,
    Rule,
    RuleKind,
    RuleSite,
    SkipCondition,
    UnitFlag,
    UnitStatus,
)
from codecoremorph.llm.catalog import ModelInfo
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import RuleScan, scan_rule
from codecoremorph.rules.applicability import StandardOrder

from .conftest import FakeProject

ORDER = StandardOrder("cpp", ("cpp98", "cpp03", "cpp11", "cpp14", "cpp17", "cpp20", "cpp23"))
PRICES = ModelPrice(
    input_usd_per_mtok=4.0,
    output_usd_per_mtok=20.0,
    cache_read_usd_per_mtok=0.2,
    cache_write_usd_per_mtok=5.0,
)
EDITOR = ModelInfo(
    provider="anthropic", model="claude-opus-5-5", context_window=1_000_000, prices=PRICES
)


def _rule(
    rule_id: str,
    kind: RuleKind,
    patterns: list[str | PatternSpec] | None = None,
    *,
    skip: list[SkipCondition] | None = None,
    bump: bool = False,
    codemods: bool = True,
    llm: bool = True,
) -> LoadedRule:
    rule = Rule(
        id=rule_id,
        language="cpp",
        kind=kind,
        description=rule_id,
        applies_to=AppliesTo(standard_bump=bump),
        detector=Detector(type=DetectorType.PATTERN, patterns=patterns) if patterns else None,
        codemods=[CodemodSpec(tool="clang-tidy", checks=["x"])] if codemods else [],
        llm=LlmGuidance(guidance="g.md") if llm else None,
        skip=skip or [],
    )
    return LoadedRule(rule=rule, pack="test", source=Path("rules") / f"{rule_id}.yaml")


TYPEDEFS = _rule(
    "replace_typedefs_with_using",
    RuleKind.API_PRESERVING,
    [r"\btypedef\b"],
    skip=[SkipCondition.C_COMPATIBLE],
)
POINTERS = _rule("convert_raw_pointers", RuleKind.API_CHANGING, [r"\bnew\s+\w"])
BUMP = _rule(
    "bump_standard",
    RuleKind.PROJECT,
    [PatternSpec(pattern=r"\bauto_ptr\b", min_target_standard="cpp17")],
    bump=True,
)


def _project(p: FakeProject) -> ProjectModel:
    p.file("CMakeLists.txt", "set(CMAKE_CXX_STANDARD 11)\n")
    p.file("tests/CMakeLists.txt", "add_test(x x)\n")
    p.file("include/geo/types.h", "typedef int Id;\ntypedef double Scalar;\n", public_api=True)
    p.file("src/types.cpp", '#include "geo/types.h"\ntypedef long Big;\n')
    p.file(
        "include/geo/node.h",
        '#include "geo/types.h"\nstruct Node { Node* make(); };\nstd::auto_ptr<Node> keep;\n',
        public_api=True,
    )
    p.file("src/node.cpp", '#include "geo/node.h"\nNode* Node::make() { return new Node; }\n')
    p.file("src/app.cpp", '#include "geo/node.h"\nint main() { Node n; n.make(); }\n')
    p.file("include/geo/cdefs.h", "typedef struct c_point c_point;\n", c_compatible=True)
    p.file("src/cdefs.c", '#include "geo/cdefs.h"\n')
    p.file("tests/node_test.cpp", '#include "geo/node.h"\nvoid t() { delete new Node; }\n')
    p.dep("src/types.cpp", "include/geo/types.h")
    p.dep("include/geo/node.h", "include/geo/types.h")
    p.dep("src/node.cpp", "include/geo/node.h")
    p.dep("src/app.cpp", "include/geo/node.h")
    p.dep("src/cdefs.c", "include/geo/cdefs.h")
    p.dep("tests/node_test.cpp", "include/geo/node.h")
    p.symbol("c:@S@Node", "include/geo/node.h", (2, 1), (2, 29), kind="struct")
    p.symbol("c:@S@Node@F@make", "include/geo/node.h", (2, 15), (2, 26), definition=False)
    p.symbol("c:@S@Node@F@make", "src/node.cpp", (2, 1), (2, 41), kind="method")
    p.ref("src/app.cpp", "c:@S@Node@F@make", 2, 25)
    p.build()
    return ProjectModel.load(
        p.index_path, p.worktree, source_dirs=["src", "include"], test_dirs=["tests"]
    )


def _scan(
    project: ProjectModel, loaded: LoadedRule, *, source: str = "cpp11", target: str = "cpp20"
) -> RuleScan:
    def no_queries(name: str, files: list[str]) -> list[RuleSite]:
        raise AssertionError(name)

    return scan_rule(
        loaded,
        project,
        order=ORDER,
        source=source,
        target=target,
        query=no_queries,
        automatic=loaded.rule.applies_to.standard_bump,
    )


def _plan(
    project: ProjectModel,
    rules: list[LoadedRule],
    *,
    token_limit: int = 500_000,
    max_files: int = 20,
    editor: ModelInfo = EDITOR,
    target: str = "cpp20",
) -> Plan:
    return build_plan(
        project,
        [_scan(project, r, target=target) for r in rules],
        estimator=Estimator(prices=editor.prices, guidance_chars={}),
        editor=editor,
        settings=PlannerSettings(token_limit=token_limit, max_unit_files=max_files),
        language="cpp",
        source="cpp11",
        target=target,
        base_commit="0" * 40,
    )


def test_bump_first_then_rules_in_manifest_order(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [BUMP, TYPEDEFS, POINTERS])
    assert [u.rules[0] for u in plan.units] == [
        "bump_standard",  # build files
        "bump_standard",  # predicted fallout: auto_ptr in node.h
        "replace_typedefs_with_using",
        "replace_typedefs_with_using",  # skipped: shared with C
        "convert_raw_pointers",
        "convert_raw_pointers",
    ]
    assert [u.id for u in plan.units] == [f"U-{n:04d}" for n in range(1, 7)]
    build, fallout = plan.units[:2]
    assert build.files == ["CMakeLists.txt", "tests/CMakeLists.txt"]
    assert build.note is not None
    assert "cpp20" in build.note
    assert fallout.files == ["include/geo/node.h"]  # only files with sites
    assert fallout.rule_sites == {"include/geo/node.h": 1}
    assert [r.id for r in plan.rules] == ["bump_standard", "replace_typedefs_with_using",
                                          "convert_raw_pointers"]  # fmt: skip
    assert plan.rules[0].automatic


def test_removed_features_count_only_when_the_target_removes_them(
    fake_project: FakeProject,
) -> None:
    plan = _plan(_project(fake_project), [BUMP], target="cpp14")
    assert [u.files for u in plan.units] == [["CMakeLists.txt", "tests/CMakeLists.txt"]]
    assert plan.rules[0].sites == 0


def test_c_compatible_headers_are_skipped_for_cpp_only_rules(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [TYPEDEFS])
    pending, skipped = plan.units
    assert pending.files == ["include/geo/types.h", "src/types.cpp"]  # merged small clusters
    assert pending.rule_sites == {"include/geo/types.h": 2, "src/types.cpp": 1}
    assert pending.status is UnitStatus.PENDING
    assert UnitFlag.PUBLIC_API in pending.flags
    assert skipped.status is UnitStatus.SKIPPED
    assert skipped.files == ["include/geo/cdefs.h"]
    assert skipped.flags == [UnitFlag.C_COMPATIBLE]
    assert skipped.estimate is None
    rule = plan.rules[0]
    assert (rule.sites, rule.files, rule.skipped_files, rule.units) == (4, 2, 1, 2)


def test_api_changing_units_include_the_fan_in(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [POINTERS])
    first, second = plan.units
    # node.cpp's make() is declared in node.h and called from app.cpp
    assert first.files == ["include/geo/node.h", "src/app.cpp", "src/node.cpp"]
    assert first.note == "fan-in adds 2 files"
    # the test's own `new` has no enclosing symbol to change, so it is a unit of its own
    assert second.files == ["tests/node_test.cpp"]
    assert UnitFlag.TOUCHES_TESTS in second.flags


def test_over_the_limit_is_too_large(fake_project: FakeProject) -> None:
    plan = _plan(_project(fake_project), [POINTERS], max_files=2)
    first = plan.units[0]
    assert first.status is UnitStatus.TOO_LARGE
    assert UnitFlag.TOO_LARGE in first.flags
    assert first.note is not None
    assert "too large: 3 files" in first.note
    assert plan.estimate is not None
    # too-large units are skipped, so they don't count towards the estimate
    pending = [u for u in plan.units if u.status is UnitStatus.PENDING]
    assert plan.estimate.tokens == sum(u.estimate.tokens for u in pending if u.estimate)


def test_large_clusters_split_by_pair_and_small_ones_merge(fake_project: FakeProject) -> None:
    project = _project(fake_project)
    # The prompt overhead is 3,000 tokens; each file adds about 17 and the pair 33.
    plan = _plan(project, [TYPEDEFS], token_limit=3_020)
    assert [u.files for u in plan.units if u.status is UnitStatus.PENDING] == [
        ["include/geo/types.h"],
        ["src/types.cpp"],
    ]
    plan = _plan(project, [TYPEDEFS], max_files=1)
    assert [u.files for u in plan.units if u.status is UnitStatus.PENDING] == [
        ["include/geo/types.h"],
        ["src/types.cpp"],
    ]


def test_rules_without_detectors_apply_to_every_file(fake_project: FakeProject) -> None:
    natural = _rule("explain", RuleKind.API_PRESERVING, codemods=False)
    plan = _plan(_project(fake_project), [natural])
    assert plan.rules[0].sites is None
    files = sorted(f for u in plan.units for f in u.files)
    assert files == [
        "include/geo/cdefs.h",
        "include/geo/node.h",
        "include/geo/types.h",
        "src/app.cpp",
        "src/node.cpp",
        "src/types.cpp",
        "tests/node_test.cpp",
    ]  # not the C source
    assert any("only LLM guidance" in w for w in plan.warnings)


def test_a_detector_without_sites_warns(fake_project: FakeProject) -> None:
    nothing = _rule("nothing", RuleKind.API_PRESERVING, [r"\bnever_matches\b"])
    plan = _plan(_project(fake_project), [nothing])
    assert plan.units == []
    assert plan.warnings == ["nothing: the detector found no sites, so the rule has no units"]


def test_local_and_unknown_models(fake_project: FakeProject) -> None:
    project = _project(fake_project)
    local = ModelInfo(provider="ollama", model="coder", context_window=None, local=True)
    plan = _plan(project, [TYPEDEFS], editor=local)
    assert plan.estimate is not None
    assert plan.estimate.cost_usd is None
    assert plan.estimate.unit_token_limit == 500_000  # the test passes its own limit
    assert any("context window of ollama/coder is unknown" in w for w in plan.warnings)
    unpriced = ModelInfo(provider="anthropic", model="new-model", context_window=100_000)
    plan = _plan(project, [TYPEDEFS], editor=unpriced)
    assert any("no prices are known for anthropic/new-model" in w for w in plan.warnings)


def test_the_same_inputs_give_the_same_plan_byte_for_byte(
    fake_project: FakeProject, tmp_path: Path
) -> None:
    first = _plan(_project(fake_project), [BUMP, TYPEDEFS, POINTERS])
    # The same project written with its rows in reverse order.
    again = FakeProject(worktree=tmp_path / "w2", index_path=tmp_path / "c2" / "index.sqlite")
    _project(again)
    again.files.reverse()
    again.symbols.reverse()
    again.refs.reverse()
    again.deps.reverse()
    again.build()
    project = ProjectModel.load(
        again.index_path, again.worktree, source_dirs=["src", "include"], test_dirs=["tests"]
    )
    second = _plan(project, [BUMP, TYPEDEFS, POINTERS])
    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert first.graph is not None
    assert first.graph.files == 8  # the C source too
