"""The C adapter on a Make project, with the real toolchain in the codecoremorph-cpp image
(D-68): configure, build, the TAP tests, the index and its build model, and a broken build's
diagnostics. Make builds run in Docker first, so these tests are marked ``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import Boundary, FileRole, LanguageConfig, TargetKind, TestStatus
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "c" / "c-legacy"
LANGUAGE = LanguageConfig(
    language="c",
    standard="c89",
    build_system="make",
    test_dirs=["tests"],
    public_api=["include/**"],
    build_timeout_s=600,
    test_timeout_s=300,
)


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AdapterContext]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "c-legacy"
    shutil.copytree(FIXTURE, work)
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    spec = CAdapter().toolchain_requirements(LANGUAGE)
    try:
        image = resolve_image(spec, None)
    except SandboxError as exc:
        pytest.skip(f"no C toolchain image: {exc}")
    executor = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/cpp.env",
        )
    )
    executor.start()
    try:
        yield AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LANGUAGE,
        )
    finally:
        executor.stop()


def _worktree_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_a_make_project_builds_tests_and_indexes(ctx: AdapterContext) -> None:
    adapter = CAdapter()
    before = _worktree_files(ctx.worktree)
    assert adapter.builds(ctx)
    assert adapter.project_standard(ctx) == "c89"  # -ansi

    configured = adapter.configure(ctx)
    assert configured.success, configured.diagnostics
    built = adapter.build(ctx)
    assert built.success, built.diagnostics
    report = adapter.run_tests(ctx)
    statuses = {c.test_id: c.status for c in report.cases}
    assert statuses["make:test"] is TestStatus.PASSED
    tap = [t for t in statuses if t.startswith("tap:")]
    assert len(tap) == 8 + 12 + 7 + 13
    assert all(statuses[t] is TestStatus.PASSED for t in tap)
    assert "tap:tests/test_htab/the table grows" in statuses
    # Everything was built in the mirror: the worktree has nothing new.
    assert _worktree_files(ctx.worktree) == before

    index = adapter.index(ctx)
    with IndexStore.open(index.path) as store:
        files = {f.path: f for f in store.files()}
        targets = {t.name: t for t in store.build_targets()}
        entries = {e.test_id: e.runs for e in store.test_entries()}
        boundaries = {
            s.name: s.boundary for s in store.symbols(file="src/htab.c") if s.is_definition
        }
    # Every C function has C linkage, but a static one is no interface (D-69).
    assert boundaries["htab_add"] is Boundary.C_ABI
    assert boundaries["htab_find"] is Boundary.NONE
    with IndexStore.open(index.path) as store:
        mains = {s.boundary for s in store.symbols(name="main")}
    assert mains == {Boundary.NONE}  # a program's entry point is no interface either
    assert files["src/htab.c"].language == "c"
    assert files["src/wfint.h"].language == "c"
    assert files["src/wfint.h"].role is FileRole.HEADER
    assert files["tests/test_tok.c"].role is FileRole.TEST
    assert files["Makefile"].role is FileRole.BUILD
    library = targets["wf"]
    assert library.kind is TargetKind.LIBRARY
    assert library.files == [f"src/{m}.c" for m in ("conf", "htab", "sbuf", "tok", "wf")]
    assert targets["wordfreq"].dependencies == ["wf"]
    assert targets["test_htab"].files == ["tests/test_htab.c"]
    assert entries["tap:tests/test_htab/the table grows"] == "test_htab"
    assert entries["make:test"] is None


def test_a_broken_build_names_the_file(ctx: AdapterContext) -> None:
    adapter = CAdapter()
    assert adapter.configure(ctx).success
    source = ctx.worktree / "src" / "htab.c"
    text = source.read_text(encoding="utf-8")
    source.write_text(text.replace("h->size++;", "h->size++ undeclared;"), encoding="utf-8")
    built = adapter.build(ctx)
    assert not built.success
    assert any(d.file_path == "src/htab.c" and d.line for d in built.errors), built.diagnostics


def test_a_clean_build_starts_over(ctx: AdapterContext) -> None:
    adapter = CAdapter()
    assert adapter.configure(ctx).success
    adapter.clean_build(ctx)
    assert not (ctx.cache_dir / "make-mirror").exists()
    assert adapter.configure(ctx).success
    assert adapter.build(ctx).success


CHARACTERIZATION = """\
#include "codecoremorph_tap.h"
#include "wf.h"

static void wf_strerror_names_success(void)
{
    CCM_CHECK_STR(wf_strerror(WF_OK), "no error");
    CCM_CHECK(wf_strerror(WF_ENOMEM) != NULL);
}

static void wf_min_length_is_ninety_nine(void)
{
    CCM_CHECK_UINT(wf_min_length(), 99);
}

int main(void)
{
    CCM_RUN(wf_strerror_names_success);
    CCM_RUN(wf_min_length_is_ninety_nine);
    return ccm_done();
}
"""


def test_a_characterization_test_runs_in_the_make_build(ctx: AdapterContext) -> None:
    """D-74: a TAP program over CodeCoreMorph's helper, built by the Makefile's marked block
    against the library's archive and run before the project's tests, whose IDs are known
    before it runs; a failed check says what it got; a dropped case is gone; unregistering
    gives the Makefile back."""
    adapter = CAdapter()
    makefile = (ctx.worktree / "Makefile").read_bytes()
    assert adapter.configure(ctx).success
    assert adapter.build(ctx).success
    adapter.run_tests(ctx)
    adapter.index(ctx)
    directory = "tests/codecoremorph_characterization"
    test = adapter.characterization_test_file(directory, "wf")
    (ctx.worktree / test).parent.mkdir(parents=True)
    (ctx.worktree / test).write_text(CHARACTERIZATION, encoding="utf-8")
    ids = [a.test_id for a in adapter.extract_assertions(ctx, [test])]
    program = f"tap:{directory}/wf_test"
    assert ids == [
        f"{program}/wf_strerror_names_success",
        f"{program}/wf_min_length_is_ninety_nine",
    ]
    assert adapter.check_determinism(ctx, [test]) == []
    adapter.register_test_files(ctx, directory, [test], ["src/wf.c", "src/conf.c"])
    assert adapter.configure(ctx).success
    built = adapter.build(ctx)
    assert built.success, built.diagnostics
    report = adapter.run_tests(ctx, adapter.characterization_tests(ids))
    cases = {c.test_id: c for c in report.cases}
    assert cases[ids[0]].status is TestStatus.PASSED
    failed = cases[ids[1]]
    assert failed.status is TestStatus.FAILED
    assert "wf_min_length(): got 1, expected 99" in (failed.message or "")
    # The project's own tests still ran, after it, and pass.
    everything = {c.test_id: c.status for c in adapter.run_tests(ctx).cases}
    assert everything["make:test"] is TestStatus.PASSED
    assert everything["tap:tests/test_htab/the table grows"] is TestStatus.PASSED
    assert adapter.drop_tests(ctx, test, [ids[1]]) == 1
    assert adapter.build(ctx).success  # the build brings the worktree's change to the mirror
    again = {c.test_id for c in adapter.run_tests(ctx).cases}
    assert ids[0] in again
    assert ids[1] not in again
    adapter.register_test_files(ctx, directory, [], [])
    assert (ctx.worktree / "Makefile").read_bytes() == makefile
