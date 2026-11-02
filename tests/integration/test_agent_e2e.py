"""Single-unit migrations end to end with real toolchains and replayed model calls (Phase 4).

Each scenario's model turns are scripted below. ``pytest --record-cassettes`` runs them and
records every request and reply; a normal run replays the cassette, so it also fails loudly
when a prompt drifts (a changed template, a path or run ID leaking into a prompt). Cassettes
are per sandbox, because compiler messages differ between toolchains.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    StopReason,
    TextBlock,
    ToolCall,
    ToolResult,
    Usage,
)
from codecoremorph.llm.replay import (
    Cassette,
    RecordingProvider,
    ReplayProvider,
    Responder,
    ScriptedProvider,
)
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.session import open_session
from codecoremorph.orchestrator.unit import SingleUnitResult, run_single_unit

GitRunner = Callable[..., str]
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "cpp"
CASSETTES = ROOT / "tests" / "cassettes" / "cpp"

pytestmark = pytest.mark.usefixtures("restore_logging")


def reply(*calls: ToolCall) -> CompletionResult:
    return CompletionResult(
        provider="anthropic",
        model="claude-opus-5-5",
        content=list(calls) or [TextBlock(text="")],
        stop_reason=StopReason.TOOL_USE if calls else StopReason.END,
        usage=Usage(input_tokens=1000, output_tokens=200),
        cost_usd=0.008,
    )


def replace(path: str, search: str, new: str) -> dict[str, str]:
    return {"op": "replace", "path": path, "search": search, "replace": new}


def submit(call_id: str, edits: list[dict[str, str]], summary: str, notes: list[str]) -> ToolCall:
    arguments: dict[str, Any] = {"edits": edits, "summary": summary, "review_notes": notes}
    return ToolCall(id=call_id, name="submit_edits", arguments=arguments)


@dataclass(frozen=True)
class Scenario:
    fixture: str
    source: str
    target: str
    rules: list[str]
    files: list[str]
    script: list[Responder]


CACHE_H = "include/inventory/cache.h"
CACHE_CPP = "src/cache.cpp"
OWNERSHIP = Scenario(
    fixture="ownership",
    source="cpp11",
    target="cpp20",
    rules=["convert_raw_pointers_to_smart_pointers"],
    files=[CACHE_H, CACHE_CPP],
    script=[
        reply(ToolCall(id="toolu_read", name="read_file",
                       arguments={"path": "tests/cache_test.cpp", "start_line": None,
                                  "end_line": None})),
        reply(submit("toolu_submit", [
            replace(CACHE_H, "#include <map>\n#include <string>",
                    "#include <map>\n#include <memory>\n#include <string>"),
            replace(CACHE_H, "    std::map<std::string, Node*> index_;",
                    "    std::map<std::string, std::unique_ptr<Node>> index_;  // owns the nodes"),
            replace(CACHE_CPP, '#include "inventory/cache.h"\n',
                    '#include "inventory/cache.h"\n\n#include <utility>\n'),
            replace(CACHE_CPP,
                    "PriceCache::~PriceCache() {\n    Node* node = head_;\n"
                    "    while (node != NULL) {\n        Node* next = node->next;\n"
                    "        delete node;\n        node = next;\n    }\n}",
                    "PriceCache::~PriceCache() {}  // index_ owns the nodes"),
            replace(CACHE_CPP,
                    "void PriceCache::put(const std::string& key, double price) {\n"
                    "    std::map<std::string, Node*>::iterator it = index_.find(key);\n"
                    "    if (it != index_.end()) {\n        it->second->price = price;\n"
                    "        unlink(it->second);\n        pushFront(it->second);",
                    "void PriceCache::put(const std::string& key, double price) {\n"
                    "    std::map<std::string, std::unique_ptr<Node>>::iterator it = "
                    "index_.find(key);\n"
                    "    if (it != index_.end()) {\n        it->second->price = price;\n"
                    "        unlink(it->second.get());\n        pushFront(it->second.get());"),
            replace(CACHE_CPP, "        index_.erase(victim->key);\n        delete victim;",
                    "        std::string evicted = victim->key;\n"
                    "        index_.erase(evicted);  // destroys the node"),
            replace(CACHE_CPP, "    Node* node = new Node;",
                    "    std::unique_ptr<Node> owned(new Node());\n    Node* node = owned.get();"),
            replace(CACHE_CPP, "    index_[key] = node;", "    index_[key] = std::move(owned);"),
            replace(CACHE_CPP,
                    "    std::map<std::string, Node*>::iterator it = index_.find(key);\n"
                    "    if (it == index_.end()) {\n        return false;\n    }\n"
                    "    unlink(it->second);\n    pushFront(it->second);",
                    "    std::map<std::string, std::unique_ptr<Node>>::iterator it = "
                    "index_.find(key);\n"
                    "    if (it == index_.end()) {\n        return false;\n    }\n"
                    "    unlink(it->second.get());\n    pushFront(it->second.get());"),
        ], "PriceCache's index_ owns its nodes through std::unique_ptr; manual deletes are "
           "gone. The list links (prev, next, head_, tail_) stay raw, non-owning pointers.",
           ["new Node() stays: std::make_unique needs C++14, and this unit builds as C++11."])),
    ],
)  # fmt: skip

FUNCTIONAL_H = "include/toolkit/functional.h"
FUNCTIONAL_CPP = "src/functional.cpp"
REMOVED_FEATURES = Scenario(
    fixture="removed-features",
    source="cpp11",
    target="cpp17",
    rules=["bump_standard"],
    files=["CMakeLists.txt", "tests/CMakeLists.txt", FUNCTIONAL_H, FUNCTIONAL_CPP],
    script=[
        reply(submit("toolu_1", [
            replace(FUNCTIONAL_H, "struct IsEven : public std::unary_function<int, bool> {",
                    "struct IsEven {"),
            replace(FUNCTIONAL_H, "struct InRange : public std::binary_function<int, int, bool> {",
                    "struct InRange {"),
            replace(FUNCTIONAL_CPP, "std::bind2nd(std::multiplies<int>(), factor)",
                    "[factor](int value) { return value * factor; }"),
            replace(FUNCTIONAL_CPP, "std::bind1st(std::plus<int>(), amount)",
                    "[amount](int value) { return amount + value; }"),
            replace(FUNCTIONAL_CPP, "std::not1(IsEven())",
                    "[](int value) { return !IsEven()(value); }"),
            replace(FUNCTIONAL_CPP, "std::bind2nd(InRange(), limit)",
                    "[limit](int value) { return InRange()(value, limit); }"),
            replace(FUNCTIONAL_CPP, "std::not1(std::ptr_fun(isVowel))",
                    "[](char c) { return !isVowel(c); }"),
            replace(FUNCTIONAL_CPP, "std::mem_fun_ref(&Entry::name)",
                    "[](const Entry& entry) { return entry.name(); }"),
        ], "Replaced the adaptors C++17 removed with lambdas.", [])),
        reply(ToolCall(id="toolu_2", name="read_file",
                       arguments={"path": "src/shuffle.cpp", "start_line": None,
                                  "end_line": None})),
        lambda request: fix_shuffle(request),
    ],
)  # fmt: skip


def fix_shuffle(request: CompletionRequest) -> CompletionResult:
    """The fixer's second attempt, written against shuffle.cpp as read_file returned it: drop
    `register` (an error in C++17), and replace the codemod's std::shuffle with a
    random_device, which made shuffled() nondeterministic, by a seeded Fisher-Yates loop.

    (Left to the tests, the random_device would fail a determinism test with random values
    in its output, and no cassette could replay that prompt.)"""
    read = next(b for b in request.messages[-1].content if isinstance(b, ToolResult))
    line = next(text for text in read.content.splitlines() if "std::shuffle(" in text)
    indent = line[: len(line) - len(line.lstrip())]
    loop = (
        f"{indent}for (std::ptrdiff_t i = static_cast<std::ptrdiff_t>(out.size()) - 1; "
        f"i > 0; --i) {{\n{indent}    std::swap(out[i], out[rng(i + 1)]);\n{indent}}}"
    )
    register = "    register long total = 0;\n    for (register int i = 0; i < count; ++i) {"
    plain = "    long total = 0;\n    for (int i = 0; i < count; ++i) {"
    return reply(
        submit(
            "toolu_3",
            [replace("src/shuffle.cpp", register, plain), replace("src/shuffle.cpp", line, loop)],
            "Removed the register storage class, which C++17 removed, and made shuffled() "
            "shuffle with the seeded Lcg again: the codemod's std::shuffle with "
            "std::random_device was nondeterministic.",
            [
                "The shuffle order differs from libc++'s old random_shuffle, but it is "
                "deterministic per seed, which is what the tests require."
            ],
        )
    )


TYPEDEFS = Scenario(
    fixture="typedefs",
    source="cpp11",
    target="cpp20",
    rules=["replace_typedefs_with_using"],
    files=["include/geo/types.h", "include/geo/vector.h", "src/vector.cpp"],
    script=[],
)


def _manifest(name: str, s: Scenario, mode: str) -> str:
    return f"""\
version: "1.0"
project:
  name: {name}
  source_dirs: [src, include, app]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {{language: cpp, standard: {s.source}, build_system: cmake}}
  target: {{language: cpp, standard: {s.target}, rules: []}}
parity: {{baseline_runs: 1}}
sandbox: {{mode: {mode}}}
"""


Run = Callable[[Scenario, str], tuple[SingleUnitResult, ScriptedProvider | ReplayProvider]]


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def run(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    git: GitRunner,
    isolated_home: Path,
    record_cassettes: bool,
) -> Run:
    mode: str = request.param
    if mode == "native":
        env: Path = request.getfixturevalue("cpp_toolchain_env")
        (isolated_home / "toolchains").mkdir(parents=True)
        shutil.copyfile(env, isolated_home / "toolchains" / "cpp.env")
    flavor = "docker" if mode == "docker" else f"native-{sys.platform}"

    def go(
        scenario: Scenario, name: str
    ) -> tuple[SingleUnitResult, ScriptedProvider | ReplayProvider]:
        root = tmp_path / scenario.fixture
        shutil.copytree(FIXTURES / scenario.fixture, root)
        (root / "migration_config.yaml").write_text(
            _manifest(scenario.fixture, scenario, mode), encoding="utf-8"
        )
        git(root.parent, "init", "--quiet", str(root))
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "-m", "legacy")
        path = CASSETTES / f"{name}-{flavor}.json"
        provider: ScriptedProvider | ReplayProvider
        if record_cassettes or not scenario.script:
            scripted = ScriptedProvider(list(scenario.script))
            provider = scripted
            llm_provider: Any = (
                RecordingProvider(scripted, Cassette(path)) if scenario.script else scripted
            )
        else:
            if not path.is_file():
                pytest.skip(f"no cassette {path.name}; record it with --record-cassettes")
            provider = ReplayProvider(Cassette.load(path), "anthropic")
            llm_provider = provider
        with open_session(root / "migration_config.yaml") as session:
            service = LLMService(session.config.llm, providers={"anthropic": llm_provider})
            result = run_single_unit(
                session, rule_ids=scenario.rules, files=scenario.files, llm=service
            )
        return result, provider

    return go


def test_typedefs_need_no_model(run: Run) -> None:
    result, provider = run(TYPEDEFS, "typedefs")
    outcome = result.outcome
    assert outcome.succeeded, outcome.reason
    assert outcome.agent_skipped
    assert isinstance(provider, ScriptedProvider)
    assert provider.requests == []
    assert "+using Scalar = double;" in outcome.diff


def test_ownership_unit_with_the_agent(run: Run) -> None:
    result, provider = run(OWNERSHIP, "ownership-cache")
    outcome = result.outcome
    assert outcome.succeeded, outcome.reason
    assert not outcome.agent_skipped
    assert [a.outcome.value for a in outcome.attempts if a.outcome] == ["succeeded"]
    assert "std::unique_ptr<Node>" in outcome.diff
    assert "-        delete victim;" in outcome.diff
    assert outcome.warnings == [
        "convert_raw_pointers_to_smart_pointers: 1 site(s) left (a warning)"
    ]
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0


def test_removed_features_bump_with_a_fixer_attempt(run: Run) -> None:
    result, provider = run(REMOVED_FEATURES, "removed-features-cpp17")
    outcome = result.outcome
    assert outcome.succeeded, outcome.reason
    # 1: the build breaks in shuffle.cpp, outside the unit (register); 2: the fixer reads it
    # and fixes it, and the nondeterministic shuffle the codemod introduced.
    assert [a.outcome.value for a in outcome.attempts if a.outcome] == [
        "build_failed",
        "succeeded",
    ]
    assert [a.role.value for a in outcome.attempts] == ["editor", "fixer"]
    assert outcome.attempts[0].diagnostics[0].file_path == "src/shuffle.cpp"
    assert "set(CMAKE_CXX_STANDARD 17)" in outcome.diff
    assert "src/shuffle.cpp" in outcome.changed_files  # a failing file a project rule may fix
    assert "std::unique_ptr" in outcome.diff  # the bump's own codemods, project-wide
    if isinstance(provider, ReplayProvider):
        assert provider.unused == 0
