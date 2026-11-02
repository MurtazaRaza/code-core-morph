"""The C++ adapter against the fixture projects, with real toolchains (SPEC §11.1, Phase 2 exit).

Native runs are marked ``toolchain`` and use the toolchain installed by
``codecoremorph toolchain install cpp``; Docker runs are marked ``docker`` and use the
toolchain image (``scripts/build-image cpp`` or the published one).
"""

from __future__ import annotations

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

    def make(fixture: str, standard: str = "cpp11", target: str = "cpp20") -> AdapterContext:
        work = tmp_path / fixture
        shutil.copytree(FIXTURES_DIR / fixture, work)
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
    assert index.meta.stats["symbols"] > 100
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
