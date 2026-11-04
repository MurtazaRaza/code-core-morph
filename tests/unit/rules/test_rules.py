"""Rule model, loader, registry and pattern detectors (SPEC §7.3, FR-9)."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from pydantic import ValidationError

from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import Detector, DetectorType, Rule, RuleKind
from codecoremorph.rules.detectors import find_pattern_sites, mask_c_family
from codecoremorph.rules.loader import RuleLoadError, load_pack
from codecoremorph.rules.packs.cpp import PACK_DIR
from codecoremorph.rules.registry import RuleRegistry

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "cpp"

RULE = """\
id: {id}
language: cpp
kind: api_preserving
description: A project rule.
detector: {{type: pattern, patterns: ['\\bfoo\\b']}}
llm: {{guidance: guidance/{id}.md}}
"""


def _write_rule(directory: Path, rule_id: str, *, guidance: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{rule_id}.yaml").write_text(RULE.format(id=rule_id), encoding="utf-8")
    if guidance:
        (directory / "guidance").mkdir(exist_ok=True)
        (directory / "guidance" / f"{rule_id}.md").write_text("Do it.\n", encoding="utf-8")


def test_builtin_cpp_pack() -> None:
    pack, rules = load_pack(PACK_DIR)
    assert pack.name == "cpp"
    assert pack.min_tool_versions == {"clang-tidy": "18"}
    by_id = {r.rule.id: r for r in rules}
    assert sorted(by_id) == [
        "bump_standard",
        "convert_raw_pointers_to_smart_pointers",
        "modernize_basics",
        "replace_typedefs_with_using",
        "use_concepts_for_templates",
    ]
    assert by_id["convert_raw_pointers_to_smart_pointers"].rule.kind is RuleKind.API_CHANGING
    assert by_id["bump_standard"].rule.kind is RuleKind.PROJECT
    for loaded in rules:
        guidance = loaded.guidance_path()
        assert guidance is not None
        assert guidance.is_file()
        llm = loaded.rule.llm
        assert llm is not None
        assert llm.examples is not None
        assert (loaded.base_dir / llm.examples / "before.cpp").is_file()


@pytest.mark.parametrize(
    ("rule_id", "fixture", "minimum"),
    [
        ("replace_typedefs_with_using", "typedefs", 30),
        ("convert_raw_pointers_to_smart_pointers", "ownership", 15),
        ("use_concepts_for_templates", "templates", 10),
        ("bump_standard", "removed-features", 15),
        ("bump_standard", "cpp98-legacy", 8),
        ("bump_standard", "cpp17-legacy", 3),
    ],
)
def test_detectors_find_sites_in_their_fixture(rule_id: str, fixture: str, minimum: int) -> None:
    _, rules = load_pack(PACK_DIR)
    rule = next(r.rule for r in rules if r.rule.id == rule_id)
    assert rule.detector is not None
    sites = []
    for path in sorted((FIXTURES / fixture).rglob("*")):
        if path.suffix in (".h", ".cpp"):
            text = path.read_text(encoding="utf-8")
            patterns = [spec.pattern for spec in rule.detector.pattern_specs()]
            sites += find_pattern_sites(path.name, text, patterns)
    assert len(sites) >= minimum


def test_masking_ignores_comments_and_strings() -> None:
    text = (
        "typedef int A; // typedef in a comment\n"
        '/* typedef\n   still a comment */ const char* s = "typedef \\" typedef";\n'
        'char c = \'t\'; auto r = R"x(typedef )" still raw)x"; typedef long B;\n'
    )
    masked = mask_c_family(text)
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    sites = find_pattern_sites("a.h", text, [r"\btypedef\b"])
    assert [(s.line, s.column) for s in sites] == [(1, 1), (4, 53)]
    assert sites[1].text.startswith("char c")


def test_the_ownership_detector_finds_new_and_delete_expressions_only() -> None:
    # The M1 benchmark planned a unit on leveldb's ``deleted_files_`` (57 such sites).
    _, rules = load_pack(PACK_DIR)
    rule = next(r.rule for r in rules if r.rule.id == "convert_raw_pointers_to_smart_pointers")
    assert rule.detector is not None
    patterns = [spec.pattern for spec in rule.detector.pattern_specs()]
    text = (
        "Item* a = new Item(1);\n"
        "char* b = new char[8];\n"
        "delete a;\n"
        "delete[] b;\n"
        "delete (a);\n"
        "Owner(const Owner&) = delete;\n"
        "std::set<int> deleted_files_;\n"
        "deleter_(key, value);\n"
        "bool newer = renew(x);\n"
        'const char* s = "new-file entry";\n'
    )
    sites = find_pattern_sites("a.cc", text, patterns)
    assert [s.line for s in sites] == [1, 2, 3, 4, 5]


def test_detector_fields_must_match_its_type() -> None:
    with pytest.raises(ValidationError, match="needs `patterns`"):
        Detector(type=DetectorType.PATTERN)
    with pytest.raises(ValidationError, match="needs `query`"):
        Detector(type=DetectorType.ADAPTER_QUERY)
    assert Detector(type=DetectorType.HOOK, hook="pkg.mod:check").hook == "pkg.mod:check"


def test_natural_language_only_rules() -> None:
    rule = Rule(
        id="explain",
        language="cpp",
        kind=RuleKind.API_PRESERVING,
        description="x",
        llm={"guidance": "g.md"},  # type: ignore[arg-type]
    )
    assert rule.natural_language_only


def test_project_rules_and_errors(tmp_path: Path) -> None:
    project = tmp_path / "rules"
    _write_rule(project, "my_rule")
    registry = RuleRegistry.discover([project], eps=[])
    assert registry.get("my_rule").pack == "project:rules"

    _write_rule(tmp_path / "missing", "no_guidance", guidance=False)
    with pytest.raises(RuleLoadError, match="doesn't exist"):
        RuleRegistry.discover([tmp_path / "missing"], eps=[])

    wrong = tmp_path / "wrong"
    _write_rule(wrong, "named_right")
    (wrong / "named_right.yaml").rename(wrong / "other_name.yaml")
    with pytest.raises(RuleLoadError, match="must be named after the rule"):
        RuleRegistry.discover([wrong], eps=[])

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bad.yaml").write_text("id: bad\nlanguage: cpp\nkind: sideways\n", encoding="utf-8")
    with pytest.raises(RuleLoadError, match="kind"):
        RuleRegistry.discover([bad], eps=[])

    with pytest.raises(RuleLoadError, match="doesn't exist"):
        RuleRegistry.discover([tmp_path / "nowhere"], eps=[])

    with pytest.raises(ConfigError, match="unknown rule 'nope'"):
        registry.get("nope")


def test_duplicate_rule_ids_are_errors(tmp_path: Path) -> None:
    _write_rule(tmp_path / "one", "same")
    _write_rule(tmp_path / "two", "same")
    with pytest.raises(RuleLoadError, match="defined twice"):
        RuleRegistry.discover([tmp_path / "one", tmp_path / "two"], eps=[])


def test_entry_point_packs() -> None:
    registry = RuleRegistry.discover()
    assert "cpp" in registry.packs
    assert registry.get("modernize_basics").pack == "cpp"
    assert [r.rule.id for r in registry.resolve(["bump_standard"])] == ["bump_standard"]
    by_pack = {
        pack: sorted(r.rule.id for r in registry.for_language("cpp") if r.pack == pack)
        for pack in ("cpp", "cpp_to_rust")
    }
    assert len(by_pack["cpp"]) == 5
    assert by_pack["cpp_to_rust"] == ["port_c_abi_module"]
    port = registry.get("port_c_abi_module").rule
    assert (port.kind, port.target_language, port.extra_scope) == ("port", "rust", ["rust/**"])

    broken = RuleRegistry.discover(
        eps=[
            EntryPoint("x", "codecoremorph.core.ids:new_run_id", "codecoremorph.rules"),
            EntryPoint("y", "no_such_module_here:PACK", "codecoremorph.rules"),
        ]
    )
    assert broken.rules == {}
    assert len(broken.problems) == 2


PACK_YAML = "name: {name}\nlanguage: cpp\n{api}"


def _plugin_pack(root: Path, name: str, api: str) -> Path:
    directory = root / name
    _write_rule(directory, f"{name}_rule")
    (directory / "pack.yaml").write_text(PACK_YAML.format(name=name, api=api), encoding="utf-8")
    return directory


def test_plugin_packs_declare_the_rule_pack_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    module = types.ModuleType("ccm_test_rule_plugins")
    module.good = _plugin_pack(tmp_path, "good", 'api_version: "1"\n')  # type: ignore[attr-defined]
    module.old = _plugin_pack(tmp_path, "old", 'api_version: "2"\n')  # type: ignore[attr-defined]
    module.silent = _plugin_pack(tmp_path, "silent", "")  # type: ignore[attr-defined]
    loose = tmp_path / "loose"
    _write_rule(loose, "loose_rule")
    module.loose = loose  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ccm_test_rule_plugins", module)

    group = "codecoremorph.rules"
    registry = RuleRegistry.discover(
        eps=[
            EntryPoint(n, f"ccm_test_rule_plugins:{n}", group)
            for n in ("good", "old", "silent", "loose")
        ]
    )
    assert sorted(registry.packs) == ["good"]
    problems = "\n".join(registry.problems)
    assert "rule pack old: implements rule pack API 2, but this CodeCoreMorph supports" in problems
    assert "rule pack silent: doesn't declare the rule pack API version" in problems
    assert "has no pack.yaml" in problems

    # A rule from a pack that didn't load says why it is unknown.
    with pytest.raises(ConfigError) as caught:
        registry.get("old_rule")
    reason = caught.value.reason or ""
    assert "rule packs that didn't load: " in reason
    assert "old = ccm_test_rule_plugins:old: rule pack old: implements rule pack API 2" in reason


def test_project_rule_directories_may_skip_the_api_version(tmp_path: Path) -> None:
    project = _plugin_pack(tmp_path, "mine", "")
    assert RuleRegistry.discover([project], eps=[]).get("mine_rule").pack == "mine"

    newer = _plugin_pack(tmp_path, "newer", 'api_version: "2"\n')
    with pytest.raises(RuleLoadError, match="implements rule pack API 2"):
        RuleRegistry.discover([newer], eps=[])


def test_the_builtin_pack_declares_its_api() -> None:
    pack, _ = load_pack(PACK_DIR, plugin=True)
    assert pack.api_version == "1"
