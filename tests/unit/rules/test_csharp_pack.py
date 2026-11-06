"""The csharp rule pack's pattern detectors (SPEC §11.3, D-65, D-66): what each finds, and
what the fixtures' runs showed it must leave alone."""

from __future__ import annotations

import pytest

from codecoremorph.core.models import PatternSpec
from codecoremorph.rules.detectors import find_pattern_sites
from codecoremorph.rules.registry import RuleRegistry


def _patterns(rule_id: str) -> list[str]:
    rule = RuleRegistry.discover().get(rule_id).rule
    assert rule.detector is not None
    return [p.pattern if isinstance(p, PatternSpec) else p for p in rule.detector.patterns]


def _lines(rule_id: str, code: str) -> list[int]:
    return sorted({s.line for s in find_pattern_sites("x.cs", code, _patterns(rule_id))})


@pytest.mark.parametrize(
    ("rule", "found", "left"),
    [
        ("use_file_scoped_namespaces", "namespace Shop.Core\n{\n}", "namespace Shop.Core;"),
        ("use_pattern_matching", "Item a = value as Item;", "if (value is Item item) {}"),
        ("use_pattern_matching", "if (!(obj is Money)) {}", "if (obj is not Money other) {}"),
        ("use_pattern_matching", "if (obj is not Money) {}", 'var s = $"is Money)";'),
        ("convert_switch_statements", '  case 0:\n    return "zero";', "  case 0:\n    count++;"),
        (
            "use_using_declarations",
            "using (var r = new StringReader(s))",
            "using var r = new StringReader(s);",
        ),
        ("use_target_typed_new", "List<int> xs = new List<int>();", "List<int> xs = new();"),
        (
            "use_target_typed_new",
            "Order o = new Order { Id = 1 };",
            'Money m = new Money(1, "EUR").Plus(x);',
        ),
        ("use_collection_expressions", "xs = new List<int>();", "var xs = new List<int>(100);"),
        ("use_collection_expressions", "return new string[0];", "return [];"),
        ("use_collection_expressions", "int[] a = new int[] { 1 };", "int[] a = [1];"),
        ("bump_csharp_version", "var f = new BinaryFormatter();", "// BinaryFormatter, once"),
    ],
)
def test_a_detector_finds_its_sites_and_leaves_the_rest(rule: str, found: str, left: str) -> None:
    assert _lines(rule, found) == [1]
    assert _lines(rule, left) == []


def test_the_bump_s_site_needs_a_target_where_it_breaks() -> None:
    rule = RuleRegistry.discover().get("bump_csharp_version").rule
    assert rule.detector is not None
    specs = [p for p in rule.detector.patterns if isinstance(p, PatternSpec)]
    assert [(s.pattern, s.min_target_standard) for s in specs] == [
        (r"\bBinaryFormatter\b", "csharp12")
    ]


def test_every_feature_rule_runs_dotnet_format_or_asks_the_adapter() -> None:
    registry = RuleRegistry.discover()
    for loaded in registry.for_language("csharp"):
        rule = loaded.rule
        tools = {c.tool for c in rule.codemods}
        if rule.id == "bump_csharp_version":
            assert tools == {"msbuild-lang-version"}
        elif rule.id in ("use_record_types", "enable_nullable"):
            assert rule.detector is not None
            assert rule.detector.type.value == "adapter_query"
        else:
            assert tools == {"dotnet-format"}, rule.id
            assert all(c.checks for c in rule.codemods), rule.id
