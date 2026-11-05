"""The Java adapter with real toolchains (SPEC §11.2, D-63, D-64): the dependency step, Maven
builds and Surefire tests on the JDK of the build's standard, the Spoon index, the bump's
codemods (OpenRewrite and the POMs' level), and the guard on build extensions.

Native runs are marked ``toolchain`` and use ``codecoremorph toolchain install java``; Docker
runs are marked ``docker`` and use the Java image (``scripts/build-image java``).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.java import JavaAdapter
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
FIXTURES = ROOT / "fixtures" / "java"
BUMP = [
    CodemodSpec(
        tool="openrewrite",
        options={"recipe_for_java21": "org.openrewrite.java.migrate.UpgradeToJava21"},
    ),
    CodemodSpec(tool="maven-java-version"),
]

MakeContext = Callable[[str], AdapterContext]


def _language(standard: str = "java8") -> LanguageConfig:
    return LanguageConfig(
        language="java",
        standard=standard,
        target_standard="java21",
        build_system="maven",
        test_dirs=["**/src/test"],
        build_timeout_s=900,
        test_timeout_s=600,
    )


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def make_context(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MakeContext]:
    kind: str = request.param
    executors: list[Executor] = []

    def make(fixture: str) -> AdapterContext:
        work = tmp_path / fixture
        shutil.copytree(FIXTURES / fixture, work)
        for name in ("build", "cache", "logs"):
            (tmp_path / name).mkdir(exist_ok=True)
        executor: Executor
        if kind == "native":
            env: Path = request.getfixturevalue("java_toolchain_env")
            executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
        else:
            from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
            from codecoremorph.sandbox.provisioning import resolve_image

            spec = JavaAdapter().toolchain_requirements(_language())
            try:
                image = resolve_image(spec, None)
            except SandboxError as exc:
                pytest.skip(f"no Java toolchain image: {exc}")
            executor = DockerExecutor(
                DockerSettings(
                    image=image,
                    run_id=new_run_id(),
                    worktree=work,
                    build_dir=tmp_path / "build",
                    cache_dir=tmp_path / "cache",
                    log_dir=tmp_path / "exec",
                    toolchain_env="/opt/codecoremorph/toolchains/java.env",
                )
            )
        executor.start()
        executors.append(executor)
        return AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=_language(),
        )

    yield make
    for executor in executors:
        executor.stop()


def test_the_ledger_builds_tests_indexes_and_takes_the_bump(make_context: MakeContext) -> None:
    ctx = make_context("legacy-ledger")
    adapter = JavaAdapter()
    assert adapter.configure(ctx).success
    assert adapter.configure(ctx).duration_s == 0  # the POMs didn't change: no fetch
    assert adapter.project_standard(ctx) == "java8"
    assert adapter.build(ctx).success
    report = adapter.run_tests(ctx)
    assert len(report.cases) == 23
    assert {c.status for c in report.cases} == {TestStatus.PASSED}
    assert not (ctx.worktree / "target").exists()  # Maven built in the mirror

    index = adapter.index(ctx)
    with IndexStore.open(index.path) as store:
        roles = {f.path: f.role for f in store.files()}
        deps = {(d.from_file, d.to_file) for d in store.dependencies()}
    assert roles["pom.xml"] is FileRole.BUILD
    assert roles["src/test/java/org/example/ledger/LedgerTest.java"] is FileRole.TEST
    assert (
        "src/test/java/org/example/ledger/LedgerTest.java",
        "src/main/java/org/example/ledger/Ledger.java",
    ) in deps
    candidates = adapter.query(ctx, "record_candidates", sorted(roles))
    assert [s.file for s in candidates] == ["src/main/java/org/example/ledger/Money.java"]

    # The bump: OpenRewrite's recipe for 21 on JDK 8, then the POM's level.
    files = sorted(f for f, role in roles.items() if role is not FileRole.THIRD_PARTY)
    result = adapter.run_codemods(ctx, BUMP, files)
    assert result.diagnostics == []
    assert "src/main/java/org/example/ledger/Codec.java" in result.changed_files
    pom = (ctx.worktree / "pom.xml").read_text(encoding="utf-8")
    assert "<maven.compiler.source>21</maven.compiler.source>" in pom
    assert "jakarta.xml.bind-api" in pom  # the recipe resolved the dependency it added
    at21 = replace(ctx, lang=_language("java21"))
    assert adapter.project_standard(at21) == "java21"
    assert adapter.configure(at21).success
    assert adapter.build(at21).success
    failing = [
        c.test_id for c in adapter.run_tests(at21).cases if c.status is not TestStatus.PASSED
    ]
    # Base64.getEncoder() doesn't wrap lines as sun.misc.BASE64Encoder did: the agent's part.
    assert failing == ["junit:org.example.ledger.CodecTest#wrapsLongBase64Lines"]


def test_the_multi_module_build(make_context: MakeContext) -> None:
    ctx = make_context("task-planner")
    adapter = JavaAdapter()
    assert adapter.configure(ctx).success
    assert adapter.build(ctx).success
    cases = {c.test_id: c for c in adapter.run_tests(ctx).cases}
    assert len(cases) == 21
    assert "junit:org.example.tasks.TaskQueueTest$WhenEmpty#hasNoNext" in cases
    assert "junit:org.example.tasks.EffortTest#describesEstimates(int, String)[1]" in cases
    chosen = adapter.run_tests(
        ctx,
        TestSelection(
            test_ids=[
                "junit:org.example.tasks.TaskQueueTest$WhenEmpty#hasNoNext",
                "junit:org.example.app.PlannerTest#namesPriorities(int, String)[1]",
            ]
        ),
    )
    assert sorted({c.test_id.split("#")[0] for c in chosen.cases}) == [
        "junit:org.example.app.PlannerTest",
        "junit:org.example.tasks.TaskQueueTest$WhenEmpty",
    ]

    # A compile error names the module's file, as the worktree has it.
    planner = ctx.worktree / "app" / "src" / "main" / "java" / "org" / "example" / "app"
    path = planner / "Planner.java"
    path.write_text(
        path.read_text(encoding="utf-8").replace("queue.size()", "queue.count()"), encoding="utf-8"
    )
    broken = adapter.build(ctx)
    assert not broken.success
    errors = [d for d in broken.diagnostics if d.severity is Severity.ERROR]
    assert errors[0].file_path == "app/src/main/java/org/example/app/Planner.java"
    assert "cannot find symbol" in errors[0].message


def test_a_new_build_extension_is_refused_before_the_network(make_context: MakeContext) -> None:
    ctx = make_context("legacy-ledger")
    adapter = JavaAdapter()
    assert adapter.configure(ctx).success
    pom = ctx.worktree / "pom.xml"
    pom.write_text(
        pom.read_text(encoding="utf-8").replace(
            "</dependencies>",
            "</dependencies>\n  <build><extensions><extension><groupId>x</groupId>"
            "<artifactId>spy</artifactId><version>1</version></extension></extensions></build>",
        ),
        encoding="utf-8",
    )
    refused = adapter.configure(ctx)
    assert not refused.success
    assert "build extensions" in refused.diagnostics[0].message
    assert "spy" in refused.diagnostics[0].message
