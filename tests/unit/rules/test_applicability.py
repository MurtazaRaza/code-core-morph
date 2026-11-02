"""Which rules, patterns and codemod passes apply to a migration, by standard (SPEC §7.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from codecoremorph.adapters.cpp.cmake import SUPPORTED_STANDARDS
from codecoremorph.config import parse_config
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import CodemodSpec, Detector, Rule
from codecoremorph.rules.applicability import (
    MigrationProblem,
    StandardOrder,
    active_patterns,
    check_migration,
    codemod_applies,
    rule_excludes,
    unknown_standards,
)
from codecoremorph.rules.packs.cpp import PACK_DIR
from codecoremorph.rules.registry import RuleRegistry

CPP = StandardOrder("cpp", SUPPORTED_STANDARDS)


def _rule(**fields: object) -> Rule:
    return Rule.model_validate(
        {
            "id": "r",
            "language": "cpp",
            "kind": "api_preserving",
            "description": "d",
            "llm": {"guidance": "g.md"},
            **fields,
        }
    )


def _registry() -> RuleRegistry:
    registry = RuleRegistry()
    registry.add_pack(PACK_DIR)
    return registry


def _problems(source: str, target: str, rules: list[str]) -> list[MigrationProblem]:
    config = parse_config(
        'version: "1.0"\nproject: {name: demo}\nmigration:\n'
        f"  source: {{language: cpp, standard: {source}}}\n"
        f"  target: {{language: cpp, standard: {target}, rules: [{', '.join(rules)}]}}\n"
    ).config
    return check_migration(config, SUPPORTED_STANDARDS, _registry())


def test_standard_order() -> None:
    assert CPP.at_least("cpp20", "cpp17")
    assert CPP.at_least("cpp17", "cpp17")
    assert not CPP.at_least("cpp03", "cpp11")
    assert CPP.newer("cpp03", "cpp98")
    assert not CPP.newer("cpp11", "cpp11")
    with pytest.raises(ConfigError, match="'cpp2a' is not a cpp standard"):
        CPP.rank("cpp2a")


def test_rule_conditions() -> None:
    concepts = _rule(applies_to={"min_target_standard": "cpp20"})
    assert rule_excludes(CPP, concepts, "cpp11", "cpp20") is None
    assert rule_excludes(CPP, concepts, "cpp11", "cpp17") == (
        "needs a target standard of cpp20 or newer"
    )
    old_only = _rule(applies_to={"max_source_standard": "cpp03"})
    assert rule_excludes(CPP, old_only, "cpp98", "cpp11") is None
    assert rule_excludes(CPP, old_only, "cpp11", "cpp14") == "only applies to sources up to cpp03"
    bump = _rule(applies_to={"standard_bump": True})
    assert rule_excludes(CPP, bump, "cpp98", "cpp11") is None
    assert rule_excludes(CPP, bump, "cpp17", "cpp17") == (
        "only applies when the target standard is newer than the source"
    )
    java = _rule(language="java")
    assert rule_excludes(CPP, java, "cpp11", "cpp20") == "is a java rule, not a cpp one"


def test_conditional_patterns() -> None:
    detector = Detector.model_validate(
        {
            "type": "pattern",
            "patterns": [
                r"\balways\b",
                {"pattern": r"\bauto_ptr\b", "min_target_standard": "cpp17"},
                {"pattern": r"\bthrow\s*\(\s*\)", "min_target_standard": "cpp20"},
            ],
        }
    )
    assert active_patterns(CPP, detector, "cpp11", "cpp14") == [r"\balways\b"]
    assert active_patterns(CPP, detector, "cpp11", "cpp17") == [r"\balways\b", r"\bauto_ptr\b"]
    assert len(active_patterns(CPP, detector, "cpp98", "cpp23")) == 3


def test_invalid_patterns_are_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid pattern"):
        Detector.model_validate({"type": "pattern", "patterns": [{"pattern": "(unclosed"}]})


def test_codemod_passes_newer_than_the_target_are_skipped() -> None:
    assert codemod_applies(CPP, CodemodSpec(tool="clang-tidy"), "cpp11")
    assert codemod_applies(CPP, CodemodSpec(tool="clang-tidy", standard="cpp17"), "cpp20")
    assert not codemod_applies(CPP, CodemodSpec(tool="clang-tidy", standard="cpp17"), "cpp14")


def test_unknown_standards_in_a_rule() -> None:
    rule = _rule(
        applies_to={"min_target_standard": "cpp2a"},
        detector={"type": "pattern", "patterns": [{"pattern": "x", "max_source_standard": "c99"}]},
        codemods=[{"tool": "clang-tidy", "standard": "cpp26"}],
    )
    assert unknown_standards(CPP, rule) == ["c99", "cpp26", "cpp2a"]


def test_builtin_bump_rule_covers_every_transition() -> None:
    bump = _registry().get("bump_standard").rule
    assert unknown_standards(CPP, bump) == []
    for source, target in [("cpp98", "cpp11"), ("cpp11", "cpp20"), ("cpp20", "cpp23")]:
        assert rule_excludes(CPP, bump, source, target) is None
    assert bump.detector is not None
    assert active_patterns(CPP, bump.detector, "cpp98", "cpp14") == []
    assert len(active_patterns(CPP, bump.detector, "cpp11", "cpp17")) == 8
    assert len(active_patterns(CPP, bump.detector, "cpp20", "cpp23")) == 13
    passes = [c for c in bump.codemods if c.tool == "clang-tidy"]
    assert [codemod_applies(CPP, c, "cpp14") for c in passes] == [True, False]


@pytest.mark.parametrize(
    ("source", "target"),
    [("cpp98", "cpp11"), ("cpp03", "cpp20"), ("cpp11", "cpp20"), ("cpp14", "cpp17"),
     ("cpp17", "cpp23"), ("cpp20", "cpp23")],
)  # fmt: skip
def test_every_supported_upgrade_validates(source: str, target: str) -> None:
    assert _problems(source, target, ["replace_typedefs_with_using"]) == []


def test_unknown_and_reversed_standards() -> None:
    problems = _problems("cpp2a", "cpp26", [])
    assert [(p.loc, p.severity) for p in problems] == [
        (("migration", "source", "standard"), "error"),
        (("migration", "target", "standard"), "error"),
    ]
    assert "cpp98, cpp03, cpp11, cpp14, cpp17, cpp20, cpp23" in problems[0].message
    [downgrade] = _problems("cpp20", "cpp17", [])
    assert downgrade.loc == ("migration", "target", "standard")
    assert "older than the source cpp20" in downgrade.message


def test_rules_that_dont_apply_or_dont_exist() -> None:
    problems = _problems(
        "cpp11",
        "cpp17",
        ["use_concepts_for_templates", "no_such_rule", "bump_standard", "modernize_basics"],
    )
    by_index = {p.loc[-1]: p for p in problems}
    assert sorted(by_index) == [0, 1, 2]
    assert by_index[0].message == (
        "use_concepts_for_templates doesn't apply to cpp11 -> cpp17: it needs a target standard "
        "of cpp20 or newer"
    )
    assert "unknown rule 'no_such_rule'" in by_index[1].message
    assert "modernize_basics" in by_index[1].message
    assert by_index[2].severity == "warning"
    assert "runs automatically" in by_index[2].message


def test_nothing_to_migrate_is_a_warning() -> None:
    [problem] = _problems("cpp17", "cpp17", [])
    assert problem.severity == "warning"
    assert "nothing to migrate" in problem.message
    assert _problems("cpp17", "cpp17", ["modernize_basics"]) == []


def test_missing_adapter_or_bump_rule(tmp_path: Path) -> None:
    config = parse_config(
        'version: "1.0"\nproject: {name: demo}\nmigration:\n'
        "  source: {language: cpp, standard: cpp11}\n"
        "  target: {language: cpp, standard: cpp20}\n"
    ).config
    [no_adapter] = check_migration(config, None, _registry())
    assert no_adapter.severity == "warning"
    assert "no cpp adapter is installed" in no_adapter.message
    [unlisted] = check_migration(config, [], _registry())
    assert "doesn't list its standards" in unlisted.message
    [no_bump] = check_migration(config, SUPPORTED_STANDARDS, RuleRegistry())
    assert no_bump.severity == "warning"
    assert "no installed cpp rule raises the standard" in no_bump.message
