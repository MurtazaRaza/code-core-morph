"""The C++ adapter against the fixture projects, with real toolchains (SPEC §11.1, Phase 2 exit).

Native runs are marked ``toolchain`` and use the toolchain installed by
``codecoremorph toolchain install cpp``; Docker runs are marked ``docker`` and use the
toolchain image (``scripts/build-image cpp`` or the published one).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import (
    BuildResult,
    CodemodSpec,
    LanguageConfig,
    Severity,
    TestStatus,
)
from codecoremorph.rules.detectors import find_pattern_sites
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.native import NativeExecutor

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "cpp"
# Each fixture with the standard it is written in (its migration's source).
FIXTURES = {
    "typedefs": "cpp11",
    "ownership": "cpp11",
    "templates": "cpp11",
    "removed-features": "cpp11",
    "c-compat": "cpp11",
    "multi-module": "cpp11",
    "cpp98-legacy": "cpp98",
    "cpp17-legacy": "cpp17",
    "untested": "cpp11",
}
CODE_SUFFIXES = (".h", ".hpp", ".c", ".cpp")

MakeContext = Callable[..., AdapterContext]


def _language(standard: str, target: str = "cpp20") -> LanguageConfig:
    return LanguageConfig(
        language="cpp",
        standard=standard,
        target_standard=target,
        test_dirs=["tests"],
        public_api=["include/**"],
        build_timeout_s=600,
        test_timeout_s=300,
    )


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def make_context(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MakeContext]:
    kind: str = request.param
    executors: list[Executor] = []

    def make(
        fixture: str, standard: str = "cpp11", target: str = "cpp20", source: Path | None = None
    ) -> AdapterContext:
        """A copy of ``fixtures/cpp/<fixture>``, or of the project at ``source``."""
        work = tmp_path / fixture
        shutil.copytree(source or FIXTURES_DIR / fixture, work)
        for name in ("build", "cache", "logs"):
            (tmp_path / f"{fixture}-{name}").mkdir()
        executor: Executor
        if kind == "native":
            env: Path = request.getfixturevalue("cpp_toolchain_env")
            executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
        else:
            from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
            from codecoremorph.sandbox.provisioning import resolve_image

            spec = CppAdapter().toolchain_requirements(_language(standard))
            try:
                image = resolve_image(spec, None)
            except SandboxError as exc:
                pytest.skip(f"no C++ toolchain image: {exc}")
            executor = DockerExecutor(
                DockerSettings(
                    image=image,
                    run_id=new_run_id(),
                    worktree=work,
                    build_dir=tmp_path / f"{fixture}-build",
                    cache_dir=tmp_path / f"{fixture}-cache",
                    log_dir=tmp_path / "exec",
                    toolchain_env="/opt/codecoremorph/toolchains/cpp.env",
                )
            )
        executor.start()
        executors.append(executor)
        return AdapterContext(
            worktree=work,
            build_dir=tmp_path / f"{fixture}-build",
            cache_dir=tmp_path / f"{fixture}-cache",
            log_dir=tmp_path / f"{fixture}-logs",
            executor=executor,
            lang=_language(standard, target),
        )

    yield make
    for executor in executors:
        executor.stop()


def _code_files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.suffix in CODE_SUFFIXES
    )


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_fixture_configures_builds_tests_and_indexes(
    make_context: MakeContext, fixture: str
) -> None:
    ctx = make_context(fixture, FIXTURES[fixture])
    adapter = CppAdapter()

    configured = adapter.configure(ctx)
    assert configured.success, configured.diagnostics
    built = adapter.build(ctx)
    assert built.success, [d.message for d in built.errors]
    report = adapter.run_tests(ctx)
    failing = [c.test_id for c in report.cases if c.status is not TestStatus.PASSED]
    assert failing == []
    assert any(c.test_id.startswith("gtest:") for c in report.cases)
    assert any(c.test_id.startswith("ctest:") for c in report.cases)

    index = adapter.index(ctx)
    assert index.problems == []
    assert index.meta.stats["symbols"] > (30 if fixture == "untested" else 100)  # it is small
    assert index.meta.stats["parsed"] == index.meta.stats["translation_units"]
    assert adapter.detect_disabled_tests(ctx, _code_files(ctx.worktree)) == []

    again = adapter.index(ctx)  # unchanged translation units come from the cache
    assert again.meta.stats["cached"] == again.meta.stats["translation_units"]
    assert again.meta.stats["symbols"] == index.meta.stats["symbols"]


@pytest.mark.parametrize("target", ["cpp20", "cpp23"])
def test_typedefs_migrates_with_codemods_alone(make_context: MakeContext, target: str) -> None:
    """Phase 2 exit criterion: the typedefs fixture reaches C++20 (and C++23) with codemods only
    and stays green."""
    ctx = make_context("typedefs", target, target)
    adapter = CppAdapter()
    files = _code_files(ctx.worktree)

    bumped = adapter.run_codemods(ctx, [CodemodSpec(tool="cmake-standard")], ["CMakeLists.txt"])
    assert [c.file for c in bumped.changes] == ["CMakeLists.txt"]
    number = target.removeprefix("cpp")
    assert f"set(CMAKE_CXX_STANDARD {number})" in (ctx.worktree / "CMakeLists.txt").read_text()

    assert adapter.configure(ctx).success
    result = adapter.run_codemods(
        ctx, [CodemodSpec(tool="clang-tidy", checks=["modernize-use-using"])], files
    )
    assert result.skipped == []
    assert result.remaining == []
    assert result.diagnostics == []
    remaining_sites = [
        site
        for f in files
        for site in find_pattern_sites(f, (ctx.worktree / f).read_text(), [r"\btypedef\b"])
    ]
    assert remaining_sites == []

    built = adapter.build(ctx)
    assert built.success, [d.message for d in built.errors]
    report = adapter.run_tests(ctx)
    assert report.cases
    assert all(c.status is TestStatus.PASSED for c in report.cases)


def test_parse_check_and_index_queries(make_context: MakeContext) -> None:
    ctx = make_context("typedefs", "cpp11")
    adapter = CppAdapter()
    assert adapter.configure(ctx).success
    adapter.index(ctx)
    sites = adapter.query(ctx, "typedef_declarations", ["include/geo/types.h"])
    assert {s.text.split(" = ")[0] for s in sites} >= {"geo::Scalar", "geo::Id"}

    broken = ctx.worktree / "src" / "vector.cpp"
    broken.write_text(broken.read_text() + "\nint broken() { return undeclared; }\n")
    diagnostics = adapter.parse_check(ctx, ["src/vector.cpp"])
    errors = [d for d in diagnostics if d.severity is Severity.ERROR]
    assert [(d.file_path, "undeclared" in d.message) for d in errors] == [("src/vector.cpp", True)]


def _error_files(result: BuildResult) -> set[str]:
    return {d.file_path for d in result.errors if d.file_path is not None}


@pytest.mark.parametrize(
    ("fixture", "target", "breaks"),
    [
        # C++11 keywords, literal suffixes, narrowing, explicit stream-to-bool, make_pair<A, B>
        (
            "cpp98-legacy",
            "cpp11",
            {
                "include/ledger/compat.h",
                "include/ledger/text.h",
                "src/format.cpp",
                "src/money.cpp",
                "src/parse.cpp",
            },
        ),
        # `requires` became a keyword; u8 literals are char8_t; no aggregate with a constructor
        ("cpp17-legacy", "cpp20", {"include/pkgmeta/manifest.h", "src/render.cpp"}),
        # ... and, in C++23 only, the implicit move in as_lvalue (checked below)
        ("cpp17-legacy", "cpp23", {"include/pkgmeta/manifest.h", "src/render.cpp"}),
    ],
)
def test_fixture_breaks_under_its_target(
    make_context: MakeContext, fixture: str, target: str, breaks: set[str]
) -> None:
    """The version fixtures build under their source standard (above) and break where the
    standard bump has work to do, after the cmake-standard codemod raised the standard."""
    ctx = make_context(fixture, FIXTURES[fixture], target)
    adapter = CppAdapter()
    bumped = adapter.run_codemods(ctx, [CodemodSpec(tool="cmake-standard")], ["CMakeLists.txt"])
    assert [c.file for c in bumped.changes] == ["CMakeLists.txt"]
    assert adapter.configure(ctx).success
    built = adapter.build(ctx)
    assert not built.success
    assert breaks <= _error_files(built), sorted(_error_files(built))
    if fixture == "cpp17-legacy":
        header = "include/pkgmeta/render.h"
        lines = (ctx.worktree / header).read_text(encoding="utf-8").splitlines()
        as_lvalue = lines.index("    return text;") + 1
        errors = {(d.file_path, d.line) for d in built.errors}
        assert ((header, as_lvalue) in errors) == (target == "cpp23")


def test_cpp98_build_uses_the_cpp98_googletest(make_context: MakeContext) -> None:
    ctx = make_context("cpp98-legacy", "cpp98")
    adapter = CppAdapter()
    assert adapter.configure(ctx).success
    # In Docker the build directory is a volume, so read the cache inside the sandbox.
    cache = ctx.executor.run(
        ["cmake", "-E", "cat", ctx.sandbox(ctx.build_dir / "CMakeCache.txt")],
        cwd=ctx.sandbox(ctx.worktree),
        timeout_s=60,
    ).stdout
    gtest_dir = next(line for line in cache.splitlines() if line.startswith("GTest_DIR"))
    assert "1.8.1" in gtest_dir
    commands = (ctx.cache_dir / "compile_commands.json").read_text(encoding="utf-8")
    assert "-std=gnu++98" in commands


def test_bump_codemods_replace_removed_features_in_cpp98_code(make_context: MakeContext) -> None:
    """The bump rule's clang-tidy passes parse a C++98 project as C++11 (and C++17), where
    auto_ptr and its replacement both exist, then the build files are raised."""
    from codecoremorph.rules.packs.cpp import PACK_DIR
    from codecoremorph.rules.registry import RuleRegistry

    registry = RuleRegistry()
    registry.add_pack(PACK_DIR)
    rule = registry.get("bump_standard").rule
    ctx = make_context("cpp98-legacy", "cpp98", "cpp20")
    adapter = CppAdapter()
    assert adapter.configure(ctx).success
    adapter.index(ctx)
    files = [*_code_files(ctx.worktree), "CMakeLists.txt"]
    result = adapter.run_codemods(ctx, rule.codemods, files)

    changed = {c.file for c in result.changes}
    assert {"src/inventory.cpp", "include/ledger/inventory.h", "CMakeLists.txt"} <= changed
    inventory = (ctx.worktree / "src" / "inventory.cpp").read_text(encoding="utf-8")
    assert "auto_ptr" not in inventory
    assert "unique_ptr" in inventory
    assert "set(CMAKE_CXX_STANDARD 20)" in (ctx.worktree / "CMakeLists.txt").read_text()
    transaction = (ctx.worktree / "src" / "transaction.cpp").read_text(encoding="utf-8")
    assert "std::uncaught_exceptions()" in transaction
    assert "std::uncaught_exception()" not in transaction


DOT_INCLUDE = {
    ".clang-format": "BasedOnStyle: Google\nDerivePointerAlignment: false\n",
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.21)\n"
        "project(dotinclude CXX)\n"
        "set(CMAKE_CXX_STANDARD 11)\n"
        'include_directories(".")\n'
        "add_library(core src/core.cpp)\n"
    ),
    "src/core.h": (
        "#pragma once\n#include <map>\n#include <string>\nnamespace core {\n"
        "typedef std::map<std::string, int*> Table;\n"
        "int count(const Table& t);\n}  // namespace core\n"
    ),
    "src/core.cpp": (
        '#include "src/core.h"\nnamespace core {\n'
        "typedef const Table* TablePtr;\n"
        "int count(const Table& t) { return static_cast<int>(t.size()); }\n"
        "}  // namespace core\n"
    ),
}


def test_codemods_reach_headers_found_through_dot_and_keep_the_projects_style(
    make_context: MakeContext, tmp_path: Path
) -> None:
    """Regression (D-42, leveldb): with include_directories("."), clang names a header
    `<root>/./src/core.h`, which the anchored header filter never matched, so clang-tidy
    skipped every header; and its fixes printed pointers as `int *` against the project's
    .clang-format."""
    source = tmp_path / "dot-include-source"
    for rel, text in DOT_INCLUDE.items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_text(text, encoding="utf-8", newline="\n")
    ctx = make_context("dot-include", source=source)
    adapter = CppAdapter()
    assert adapter.configure(ctx).success
    adapter.index(ctx)
    spec = CodemodSpec(tool="clang-tidy", checks=["modernize-use-using"])
    result = adapter.run_codemods(ctx, [spec], ["src/core.h", "src/core.cpp"])
    assert {c.file for c in result.changes} == {"src/core.h", "src/core.cpp"}
    header = (ctx.worktree / "src" / "core.h").read_text(encoding="utf-8")
    assert "using Table = std::map<std::string, int*>;" in header
    assert "int count(const Table& t);" in header  # an unchanged line stays as it was
    source_text = (ctx.worktree / "src" / "core.cpp").read_text(encoding="utf-8")
    assert "using TablePtr = const Table*;" in source_text
    assert adapter.build(ctx).success


# --------------------------------------------------------------------------- characterization

CHARACTERIZATION_DIR = "tests/codecoremorph_characterization"
UNTESTED_EDITS = (
    Path(__file__).resolve().parents[2] / "cassettes" / "cpp" / "edits" / "untested.json"
)
# Behavior changes the untested fixture's characterization tests must notice (SPEC §7.11 step 7):
# (file, text, mutated text, what changes). None is an equivalent mutant: `units < 100` would
# be one, since both tiers price 100 units at 1000.
MUTATIONS = [
    ("src/tariff.cpp", "if (units <= 100) {", "if (units <= 101) {", "off by one: first tier"),
    ("src/tariff.cpp", "(units - 100) * 8", "(units - 100) * 9", "constant: second tier rate"),
    ("src/tariff.cpp", "if (years >= 5) {", "if (years > 5) {", "off by one: discount step"),
    ("src/tariff.cpp", "        return 15;\n", "        return 10;\n", "branches: top discount"),
    ("src/tariff.cpp", "+ 50) / 100", "+ 49) / 100", "constant: rounding"),
    ("src/tariff.cpp", "            return cap;\n", "            return sum;\n", "branch: cap"),
    ("src/meter.cpp", "if (value < last_) {", "if (value <= last_) {", "off by one: rollover"),
    ("src/meter.cpp", "return last_ - start_;", "return last_ - start_ + 1;", "constant: usage"),
]
MUTATION_THRESHOLD = 0.6  # the Phase 6 exit criterion; the rate reached is asserted below


def _recorded_tests() -> tuple[str, str]:
    """The untested fixture's characterization test file as its host-agent run left it: the
    job's create edit, then its regeneration's replace edits."""
    data = json.loads(UNTESTED_EDITS.read_text(encoding="utf-8"))
    path, text = "", ""
    for batch in data["submissions"]["C-0001"]:
        for edit in batch["edits"]:
            if edit["op"] == "create":
                path, text = edit["path"], edit["content"]
            elif edit["op"] == "replace":
                assert text.count(edit["search"]) == 1
                text = text.replace(edit["search"], edit["replace"])
    return path, text


def test_characterization_tests_build_pass_and_catch_mutations(make_context: MakeContext) -> None:
    """The mutation sanity check (Phase 6 exit): the untested fixture's characterization tests,
    registered by the adapter, build and pass on the base code three times, pass the
    determinism lint, and fail on each deliberate behavior change."""
    ctx = make_context("untested")
    adapter = CppAdapter()
    path, text = _recorded_tests()
    (ctx.worktree / path).parent.mkdir(parents=True, exist_ok=True)
    (ctx.worktree / path).write_text(text, encoding="utf-8", newline="\n")
    written = adapter.register_test_files(
        ctx, CHARACTERIZATION_DIR, [path], ["src/meter.cpp", "src/tariff.cpp"]
    )
    assert written == [f"{CHARACTERIZATION_DIR}/CMakeLists.txt", "CMakeLists.txt"]
    assert adapter.check_determinism(ctx, [path]) == []
    cases = adapter.extract_assertions(ctx, [path])
    ids = [c.test_id for c in cases]
    assert len(ids) == 10
    assert all(c.assertions for c in cases)
    selection = adapter.characterization_tests(ids)

    def failing() -> list[str]:
        built = adapter.build(ctx)
        assert built.success, [d.message for d in built.errors]
        report = adapter.run_tests(ctx, selection)
        statuses = {c.test_id: c.status for c in report.cases}
        assert set(ids) <= set(statuses), sorted(statuses)
        assert "gtest:Billing.FormatsWholeAmounts" not in statuses  # only the selection ran
        return [i for i in ids if statuses[i] is not TestStatus.PASSED]

    assert adapter.configure(ctx).success
    for _ in range(3):
        assert failing() == []
    caught: list[str] = []
    for file, before, after, what in MUTATIONS:
        source = ctx.worktree / file
        original = source.read_text(encoding="utf-8")
        assert original.count(before) == 1, what
        source.write_text(original.replace(before, after), encoding="utf-8", newline="\n")
        try:
            if failing():
                caught.append(what)
        finally:
            source.write_text(original, encoding="utf-8", newline="\n")
    rate = len(caught) / len(MUTATIONS)
    assert rate >= MUTATION_THRESHOLD, caught
    assert caught == [m[3] for m in MUTATIONS]  # the rate reached: 8 of 8 (docs/SPEC D-46)
    # The registration comes out again, back to the fixture's own build file.
    adapter.register_test_files(ctx, CHARACTERIZATION_DIR, [], [])
    assert (ctx.worktree / "CMakeLists.txt").read_text(encoding="utf-8") == (
        FIXTURES_DIR / "untested" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
