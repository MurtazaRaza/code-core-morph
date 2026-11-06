"""The C# adapter with real toolchains (SPEC §11.3, D-65): the restore step, dotnet builds and
VSTest tests on each test project's runtime, the Roslyn index, the bump's codemod, a
dotnet format pass, and the restore's guard on code-running MSBuild constructs.

Native runs are marked ``toolchain`` and use ``codecoremorph toolchain install csharp``; Docker
runs are marked ``docker`` and use the C# image (``scripts/build-image csharp``).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import (
    CodemodSpec,
    FileRole,
    LanguageConfig,
    Severity,
    TestSelection,
    TestStatus,
)
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.store.index import IndexStore

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "csharp"
BUMP = [CodemodSpec(tool="msbuild-lang-version")]

MakeContext = Callable[[str, str], AdapterContext]


def _language(standard: str = "csharp7.3", target: str = "csharp14") -> LanguageConfig:
    return LanguageConfig(
        language="csharp",
        standard=standard,
        target_standard=target,
        build_system="dotnet",
        test_dirs=["tests"],
        build_timeout_s=900,
        test_timeout_s=600,
    )


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def make_context(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MakeContext]:
    kind: str = request.param
    executors: list[Executor] = []
    # NuGet's packages and the mirror go in the cache, whose deepest files pass Windows'
    # 260-character limit under pytest's temporary directory: keep its path short.
    short = Path(tempfile.mkdtemp(prefix="ccm"))

    def make(fixture: str, target: str) -> AdapterContext:
        work = tmp_path / fixture
        shutil.copytree(FIXTURES / fixture, work)
        cache = short / "cache"
        for path in (tmp_path / "build", cache, tmp_path / "logs"):
            path.mkdir(exist_ok=True)
        executor: Executor
        if kind == "native":
            env: Path = request.getfixturevalue("csharp_toolchain_env")
            executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
        else:
            from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
            from codecoremorph.sandbox.provisioning import resolve_image

            spec = CSharpAdapter().toolchain_requirements(_language())
            try:
                image = resolve_image(spec, None)
            except SandboxError as exc:
                pytest.skip(f"no C# toolchain image: {exc}")
            executor = DockerExecutor(
                DockerSettings(
                    image=image,
                    run_id=new_run_id(),
                    worktree=work,
                    build_dir=tmp_path / "build",
                    cache_dir=cache,
                    log_dir=tmp_path / "exec",
                    toolchain_env="/opt/codecoremorph/toolchains/csharp.env",
                )
            )
        executor.start()
        executors.append(executor)
        return AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=cache,
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=_language(target=target),
        )

    yield make
    for executor in executors:
        executor.stop()
    # NuGet writes some files past 260 characters, which only the long-path form deletes.
    shutil.rmtree(
        f"\\\\?\\{short.resolve()}" if sys.platform == "win32" else short, ignore_errors=True
    )


def test_the_order_book_builds_tests_indexes_and_takes_the_bump(
    make_context: MakeContext,
) -> None:
    ctx = make_context("order-book", "csharp14")
    adapter = CSharpAdapter()
    assert adapter.configure(ctx).success
    assert adapter.configure(ctx).duration_s == 0  # the projects didn't change: no restore
    assert adapter.project_standard(ctx) == "csharp7.3"
    built = adapter.build(ctx)
    assert built.success
    assert {d.code for d in built.diagnostics} == {"SYSLIB0011"}  # a warning on .NET 6
    report = adapter.run_tests(ctx)
    assert len(report.cases) == 30
    assert {c.status for c in report.cases} == {TestStatus.PASSED}
    assert not list(ctx.worktree.rglob("bin"))
    assert not list(ctx.worktree.rglob("obj"))

    index = adapter.index(ctx)
    with IndexStore.open(index.path) as store:
        roles = {f.path: f.role for f in store.files()}
        deps = {(d.from_file, d.to_file) for d in store.dependencies()}
        pinvoke = [s.qualified_name for s in store.symbols() if s.boundary.value == "pinvoke"]
    assert roles["src/OrderBook/OrderBook.csproj"] is FileRole.BUILD
    assert roles["OrderBook.sln"] is FileRole.BUILD
    assert roles["tests/OrderBook.Tests/BookTests.cs"] is FileRole.TEST
    assert ("tests/OrderBook.Tests/BookTests.cs", "src/OrderBook/Book.cs") in deps
    assert ("src/OrderBook/Order.cs", "src/OrderBook/OrderLine.cs") in deps
    assert pinvoke == ["OrderBook.Platform.GetTickCount"]
    candidates = adapter.query(ctx, "record_candidates", sorted(roles))
    assert [s.file for s in candidates] == ["src/OrderBook/Money.cs", "src/OrderBook/OrderLine.cs"]
    nullable = adapter.query(ctx, "nullable_disabled", sorted(roles))
    assert {s.file for s in nullable} == {f for f, r in roles.items() if r is FileRole.SOURCE}

    # The bump: net10.0 and C# 14, where BinaryFormatter is an error.
    projects = [f for f in roles if f.endswith(".csproj")]
    result = adapter.run_codemods(ctx, BUMP, projects)
    assert sorted(result.changed_files) == sorted(projects)
    at14 = replace(ctx, lang=_language("csharp14"))
    assert adapter.project_standard(at14) == "csharp14"
    assert adapter.configure(at14).success
    broken = adapter.build(at14)
    assert not broken.success
    errors = {(d.file_path, d.code) for d in broken.diagnostics if d.severity is Severity.ERROR}
    assert errors == {("src/OrderBook/Book.cs", "SYSLIB0011")}

    # dotnet format changes the unit's files only.
    before = (ctx.worktree / "src/OrderBook/Order.cs").read_bytes()
    formatted = adapter.run_codemods(
        at14,
        [
            CodemodSpec(
                tool="dotnet-format",
                checks=["IDE0161"],
                options={"csharp_style_namespace_declarations": "file_scoped"},
            )
        ],
        ["src/OrderBook/Money.cs"],
    )
    assert formatted.diagnostics == []
    assert formatted.changed_files == ["src/OrderBook/Money.cs"]
    assert b"namespace OrderBook;" in (ctx.worktree / "src/OrderBook/Money.cs").read_bytes()
    assert (ctx.worktree / "src/OrderBook/Order.cs").read_bytes() == before
    assert not (ctx.worktree / ".editorconfig").exists()


def test_a_multi_project_build_on_older_frameworks(make_context: MakeContext) -> None:
    ctx = make_context("shipping-rates", "csharp12")
    adapter = CSharpAdapter()
    assert adapter.configure(ctx).success
    assert adapter.build(ctx).success
    cases = {c.test_id: c for c in adapter.run_tests(ctx).cases}
    assert len(cases) == 22  # netcoreapp3.1 tests, rolled forward to .NET 6
    assert {c.status for c in cases.values()} == {TestStatus.PASSED}
    assert "dotnet:Rates.Tests.RateTableTests+Parcels#AParcelWeighsSomething" in cases
    chosen = adapter.run_tests(
        ctx,
        TestSelection(
            test_ids=[
                "dotnet:Rates.Tests.RateTableTests+Parcels#AParcelWeighsSomething",
                'dotnet:Rates.Tests.RateTableTests#ZoneCodesGoBothWays(zone: Europe, code: "EU")',
            ]
        ),
    )
    assert sorted(c.test_id.split("(")[0] for c in chosen.cases) == [
        "dotnet:Rates.Tests.RateTableTests#ZoneCodesGoBothWays",
        "dotnet:Rates.Tests.RateTableTests#ZoneCodesGoBothWays",
        "dotnet:Rates.Tests.RateTableTests#ZoneCodesGoBothWays",
        "dotnet:Rates.Tests.RateTableTests+Parcels#AParcelWeighsSomething",
    ]

    # The bump keeps netstandard2.0, which takes its language version from the props.
    files = [
        "Directory.Build.props",
        *sorted(
            str(p.relative_to(ctx.worktree).as_posix()) for p in ctx.worktree.rglob("*.csproj")
        ),
    ]
    adapter.run_codemods(ctx, BUMP, files)
    core = (ctx.worktree / "src/Rates.Core/Rates.Core.csproj").read_text(encoding="utf-8")
    assert "<TargetFramework>netstandard2.0</TargetFramework>" in core
    assert "LangVersion" not in core
    assert "<LangVersion>12.0</LangVersion>" in (ctx.worktree / "Directory.Build.props").read_text(
        encoding="utf-8"
    )
    at12 = replace(ctx, lang=_language("csharp12", "csharp12"))
    assert adapter.project_standard(at12) == "csharp12"
    assert adapter.configure(at12).success
    assert adapter.build(at12).success

    # A compile error names the project's file, as the worktree has it.
    path = ctx.worktree / "src" / "Rates.App" / "Quoter.cs"
    path.write_text(
        path.read_text(encoding="utf-8").replace("table.Price(", "table.Cost("), encoding="utf-8"
    )
    broken = adapter.build(at12)
    assert not broken.success
    errors = [d for d in broken.diagnostics if d.severity is Severity.ERROR]
    assert errors[0].file_path == "src/Rates.App/Quoter.cs"
    assert errors[0].code == "CS1061"


def test_a_new_exec_is_refused_before_the_network(make_context: MakeContext) -> None:
    ctx = make_context("order-book", "csharp14")
    adapter = CSharpAdapter()
    assert adapter.configure(ctx).success
    project = ctx.worktree / "src" / "OrderBook" / "OrderBook.csproj"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            "</Project>",
            '  <Target Name="Fetch" BeforeTargets="Restore">\n'
            '    <Exec Command="curl https://example.invalid/x" />\n  </Target>\n</Project>',
        ),
        encoding="utf-8",
    )
    refused = adapter.configure(ctx)
    assert not refused.success
    message = refused.diagnostics[0].message
    assert "an Exec task in src/OrderBook/OrderBook.csproj" in message
    assert "a target in src/OrderBook/OrderBook.csproj" in message
