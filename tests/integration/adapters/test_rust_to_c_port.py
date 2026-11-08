"""A Rust → C port with the real toolchain (D-75), on the Phase 16 spike's ``stats`` crate: the
Rust index from rust-analyzer's SCIP output, the ``c-module`` scaffold of the ``stats`` module,
a hand port (the C, and the module as a façade over it), the crate's own tests through the
façade with the C under AddressSanitizer and UndefinedBehaviorSanitizer, the module's tests
translated to a TAP program whose cases are CTest tests named as the Rust tests, and the
retirement, after which CMake builds and tests the C alone. Marked ``docker``: the run uses the
Rust image, which holds the C toolchain."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter, rustports
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, TestStatus
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
CRATE = ROOT / "tests" / "unit" / "adapters" / "rust" / "data" / "stats"
PORT = Path(__file__).with_name("data") / "rust_to_c"
RUST_TESTS = {
    "nextest:stats::stats::tests::mean_of_three",
    "nextest:stats::stats::tests::median_even_and_odd",
    "nextest:stats::stats::tests::histogram_bins",
    "nextest:stats::text::tests::words",
    "nextest:stats::api::the_public_api",
}
MODULE_TESTS = sorted(t for t in RUST_TESTS if t.startswith("nextest:stats::stats::tests::"))


@dataclass
class Contexts:
    rust: AdapterContext
    c: AdapterContext


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[Contexts]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "stats"
    shutil.copytree(CRATE, work)
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    rust_lang = LanguageConfig(
        language="rust", standard="rust2021", port_target="c", build_timeout_s=900
    )
    c_lang = LanguageConfig(
        language="c",
        standard="c99",
        target_standard="c99",
        port_source="rust",
        build_system="cmake",
        build_timeout_s=900,
        test_timeout_s=300,
    )
    spec = CAdapter().toolchain_requirements(c_lang)
    assert spec.image == "codecoremorph-rust"  # the C target of a Rust port needs Rust too
    try:
        image = resolve_image(spec, None)
    except SandboxError as exc:
        pytest.skip(f"no Rust toolchain image: {exc}")
    executor = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/rust.env",
        )
    )
    executor.start()
    try:
        rust = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=rust_lang,
        )
        c = replace(
            rust, cache_dir=tmp_path / "cache" / "c", log_dir=tmp_path / "logs" / "c", lang=c_lang
        )
        yield Contexts(rust, c)
    finally:
        executor.stop()


def _verify(rust: RustAdapter, c: CAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    """One verification as a port run makes it: the target configures, the source configures,
    builds and tests, then the target builds and tests (agent/loop.py)."""
    configured = c.configure(ctx.c)
    assert configured.success, configured.diagnostics
    configured = rust.configure(ctx.rust)
    assert configured.success, configured.diagnostics
    built = rust.build(ctx.rust)
    assert built.success, built.diagnostics
    statuses = {t.test_id: t.status for t in rust.run_tests(ctx.rust).cases}
    if c.builds(ctx.c):
        own = c.build(ctx.c)
        assert own.success, own.diagnostics
        statuses |= {t.test_id: t.status for t in c.run_tests(ctx.c).cases}
    return statuses


def test_a_rust_module_ported_to_c_by_hand(ctx: Contexts) -> None:
    rust, c = RustAdapter(), CAdapter()
    work = ctx.rust.worktree
    baseline = _verify(rust, c, ctx)
    assert baseline == dict.fromkeys(RUST_TESTS, TestStatus.PASSED)

    # The index a port from Rust plans with: references between the files, targets, tests.
    index = rust.index(ctx.rust)
    assert not index.problems, index.problems
    with IndexStore.open(index.path) as store:
        calls = {(a, b) for a, b, _ in store.reference_counts()}
        assert ("src/text.rs", "src/stats.rs") in calls  # text uses stats: stats goes first
        assert {t.name for t in store.build_targets()} == {"stats", "stats::api"}
        assert {t.test_id for t in store.test_entries()} == RUST_TESTS
        assert store.file("tests/api.rs").role.value == "test"  # type: ignore[union-attr]

    # The scaffold of the unit that ports src/stats.rs.
    options = {"crate": "stats", "module": "stats", "build_target": "stats"}
    spec = CodemodSpec(tool="c-module", options=options)
    scaffold = c.run_codemods(ctx.c, [spec], ["src/stats.rs", "Cargo.toml"])
    assert set(scaffold.changed_files) == {
        "CMakeLists.txt",
        "Cargo.toml",
        "build.rs",
        "c/CMakeLists.txt",
        "c/cargo-link",
        "c/include/stats/stats.h",
        "c/src/stats.c",
        "c/symbol-map.toml",
        "src/stats/sys.rs",
    }
    assert (work / "build.rs").read_text(encoding="utf-8") == rustports.RUST_C_LINK_SCRIPT
    manifest = (work / "Cargo.toml").read_text(encoding="utf-8")
    assert rustports.c_modules(manifest) == {"src/stats.rs": "c/include/stats/stats.h"}
    assert not c.run_codemods(ctx.c, [spec], ["src/stats.rs", "Cargo.toml"]).changed_files
    # The unit's first verification: the empty C library links, and nothing changed yet.
    assert _verify(rust, c, ctx) == baseline

    # The port: the C, and the module a façade over it.
    shutil.copyfile(PORT / "stats.h", work / "c/include/stats/stats.h")
    shutil.copyfile(PORT / "stats.c", work / "c/src/stats.c")
    shutil.copyfile(PORT / "stats.rs", work / "src/stats.rs")
    assert _verify(rust, c, ctx) == baseline  # the crate's tests, through the façade
    sys_rs = (work / "src/stats/sys.rs").read_text(encoding="utf-8")
    assert "pub fn stats_histogram_new" in sys_rs  # bindgen, from the module's header
    assert c.lint(ctx.c) == []  # -Wall -Wextra and clang-tidy's bug-finding checks are quiet
    c_index = c.index(ctx.c)
    with IndexStore.open(c_index.path) as store:
        names = {s.qualified_name for s in store.symbols()}
    assert {"stats_mean", "stats_histogram_new", "stats_histogram"} <= names  # the symbol map's

    # A planted overflow in the C fails a Rust test with the sanitizer's report.
    good = (work / "c/src/stats.c").read_text(encoding="utf-8")
    (work / "c/src/stats.c").write_text(
        good.replace("h->counts[i]++;", "h->counts[i + h->bins]++;"), encoding="utf-8"
    )
    broken = _verify(rust, c, ctx)
    assert broken["nextest:stats::stats::tests::histogram_bins"] is not TestStatus.PASSED
    (work / "c/src/stats.c").write_text(good, encoding="utf-8")

    # The module's tests, translated: one CTest test per case, named as the Rust test.
    spec = CodemodSpec(tool="c-tests", options={**options, "tests": ",".join(MODULE_TESTS)})
    scaffold = c.run_codemods(ctx.c, [spec], ["src/stats.rs"])
    assert {
        "c/tests/CMakeLists.txt",
        "c/tests/codecoremorph_tap.h",
        "c/tests/test_stats.c",
        "c/test-map.toml",
    } <= set(scaffold.changed_files)
    shutil.copyfile(PORT / "test_stats.c", work / "c/tests/test_stats.c")
    facade = (work / "src/stats.rs").read_text(encoding="utf-8")
    (work / "src/stats.rs").write_text(facade.split("#[cfg(test)]")[0], encoding="utf-8")
    translated = _verify(rust, c, ctx)
    module = {f"ctest:{t.removeprefix('nextest:')}" for t in MODULE_TESTS}
    assert translated == dict.fromkeys(RUST_TESTS - set(MODULE_TESTS) | module, TestStatus.PASSED)

    # A use-after-free in a translated test fails that test alone, under ASan.
    test_c = (work / "c/tests/test_stats.c").read_text(encoding="utf-8")
    (work / "c/tests/test_stats.c").write_text(
        test_c.replace(
            "    stats_histogram_free(h);\n",
            "    stats_histogram_free(h);\n    (void)stats_histogram_bins(h);\n",
        ),
        encoding="utf-8",
    )
    leaked = _verify(rust, c, ctx)
    assert leaked["ctest:stats::stats::tests::histogram_bins"] is TestStatus.FAILED
    assert leaked["ctest:stats::stats::tests::mean_of_three"] is TestStatus.PASSED
    (work / "c/tests/test_stats.c").write_text(test_c, encoding="utf-8")

    # The retirement: the Rust goes, CMake builds and tests the C alone.
    retired = c.run_codemods(ctx.c, [CodemodSpec(tool="c-retire")], [])
    assert {"Cargo.toml", "Cargo.lock", "build.rs", "src/stats.rs", "tests/api.rs"} <= set(
        retired.changed_files
    )
    assert not (work / "src").exists()
    assert not (work / "tests").exists()
    assert not rust.builds(ctx.rust)
    assert c.configure(ctx.c).success
    assert c.build(ctx.c).success
    final = {t.test_id: t.status for t in c.run_tests(ctx.c).cases}
    assert final == dict.fromkeys(module, TestStatus.PASSED)
