"""The Rust adapter with real toolchains (SPEC §11.5, D-56): Cargo's diagnostics, clippy and
nextest on a crate, and the ``ffi-module`` fixture's port by hand: the scaffold, the mixed
CMake + Cargo build with the C++ tests passing through the C header, and the C interface the
crate exports read back by the C++ adapter.

Native runs are marked ``toolchain`` and use ``codecoremorph toolchain install rust``; Docker
runs are marked ``docker`` and use the Rust image (``scripts/build-image rust``).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, Severity, TestStatus
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.native import NativeExecutor

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "fixtures" / "cpp" / "ffi-module"
EDITS = ROOT / "tests" / "cassettes" / "cpp" / "edits" / "ffi-module.json"


@dataclass
class Contexts:
    cpp: AdapterContext
    rust: AdapterContext

    @property
    def worktree(self) -> Path:
        return self.cpp.worktree


MakeContexts = Callable[..., Contexts]


def _cpp_language(standard: str = "cpp17") -> LanguageConfig:
    return LanguageConfig(
        language="cpp", standard=standard, test_dirs=["tests"], build_timeout_s=900
    )


def _rust_language() -> LanguageConfig:
    return LanguageConfig(
        language="rust",
        standard="rust2021",
        target_standard="rust2021",
        build_timeout_s=900,
        test_timeout_s=600,
    )


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def make_contexts(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MakeContexts]:
    kind: str = request.param
    executors: list[Executor] = []

    def make(source: Path | None, standard: str = "cpp17") -> Contexts:
        """A copy of a fixture (``source``), or an empty project when it is None, with C++ built
        at ``standard``."""
        work = tmp_path / "project"
        if source is None:
            work.mkdir()
        else:
            shutil.copytree(source, work)
        for name in ("build", "cache", "logs"):
            (tmp_path / name).mkdir()
        executor: Executor
        if kind == "native":
            env: Path = request.getfixturevalue("rust_toolchain_env")
            executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
        else:
            from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
            from codecoremorph.sandbox.provisioning import resolve_image

            spec = RustAdapter().toolchain_requirements(_rust_language())
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
        executors.append(executor)
        cpp = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=_cpp_language(standard),
        )
        rust = replace(
            cpp,
            cache_dir=tmp_path / "cache" / "rust",
            log_dir=tmp_path / "logs" / "rust",
            lang=_rust_language(),
        )
        return Contexts(cpp, rust)

    yield make
    for executor in executors:
        executor.stop()


def _crate(root: Path, lib: str) -> None:
    (root / "rust" / "demo" / "src").mkdir(parents=True)
    (root / "rust" / "demo" / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8"
    )
    (root / "rust" / "demo" / "Cargo.lock").write_text(
        'version = 4\n\n[[package]]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "rust" / "demo" / "src" / "lib.rs").write_text(lib, encoding="utf-8")


BROKEN = """\
pub fn add(a: i32, b: i32) -> i64 {
    a + b
}
"""

TESTED = """\
pub fn add(a: i32, b: i32) -> i64 {
    i64::from(a) + i64::from(b)
}

#[cfg(test)]
mod tests {
    #[test]
    fn adds() {
        assert_eq!(super::add(2, 3), 5);
    }

    #[test]
    fn overflows() {
        assert_eq!(super::add(i32::MAX, 1), i64::from(i32::MAX));
    }
}
"""


def test_cargo_diagnostics_clippy_and_nextest(make_contexts: MakeContexts) -> None:
    ctx = make_contexts(None)
    adapter = RustAdapter()
    _crate(ctx.worktree, BROKEN)
    assert adapter.crates(ctx.rust) == ["rust/demo"]

    built = adapter.build(ctx.rust)
    assert not built.success
    [error] = [d for d in built.diagnostics if d.severity is Severity.ERROR]
    assert (error.file_path, error.line, error.code) == ("rust/demo/src/lib.rs", 2, "E0308")

    (ctx.worktree / "rust" / "demo" / "src" / "lib.rs").write_text(TESTED, encoding="utf-8")
    assert adapter.build(ctx.rust).success
    assert adapter.clippy(ctx.rust).success
    report = adapter.run_tests(ctx.rust)
    statuses = {c.test_id: c.status for c in report.cases}
    assert statuses == {
        "nextest:demo::tests::adds": TestStatus.PASSED,
        "nextest:demo::tests::overflows": TestStatus.FAILED,
    }
    # Builds, lints and tests write nothing into the worktree.
    assert sorted(p.name for p in (ctx.worktree / "rust" / "demo").iterdir()) == [
        "Cargo.lock",
        "Cargo.toml",
        "src",
    ]


def _port_lib() -> str:
    data = json.loads(EDITS.read_text(encoding="utf-8"))
    [batch] = data["submissions"]["U-0001"]
    [lib] = [e["replace"] for e in batch["edits"] if e["path"].endswith("lib.rs")]
    return str(lib)


def test_the_ffi_module_ported_by_hand(make_contexts: MakeContexts) -> None:
    ctx = make_contexts(FIXTURE)
    cpp, rust = CppAdapter(), RustAdapter()
    root = ctx.worktree

    scaffold = rust.run_codemods(
        ctx.rust, [CodemodSpec(tool="rust-crate")], ["CMakeLists.txt", "src/ring.cpp"]
    )
    assert sorted(scaffold.changed_files) == [
        "CMakeLists.txt",
        "rust/textkit_ring/Cargo.lock",
        "rust/textkit_ring/Cargo.toml",
        "rust/textkit_ring/src/lib.rs",
    ]
    top = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "target_link_libraries(textkit PUBLIC textkit_ring)" in top
    # Running it again changes nothing.
    assert (
        rust.run_codemods(ctx.rust, [CodemodSpec(tool="rust-crate")], ["src/ring.cpp"]).changes
        == []
    )

    # The port: the crate's code in, the C++ module out.
    (root / "rust" / "textkit_ring" / "src" / "lib.rs").write_text(_port_lib(), encoding="utf-8")
    (root / "src" / "ring.cpp").unlink()
    (root / "CMakeLists.txt").write_text(top.replace("    src/ring.cpp\n", ""), encoding="utf-8")

    assert rust.build(ctx.rust).success
    assert rust.clippy(ctx.rust).success
    configured = cpp.configure(ctx.cpp)
    assert configured.success, configured.diagnostics
    built = cpp.build(ctx.cpp)
    assert built.success, built.diagnostics
    report = cpp.run_tests(ctx.cpp)
    failed = [c.test_id for c in report.cases if c.status is not TestStatus.PASSED]
    assert report.cases
    assert not failed, failed

    [exported] = rust.exported_c_headers(ctx.rust, ["rust/textkit_ring/src/lib.rs"])
    kept = cpp.c_declarations(ctx.cpp, root / "include" / "textkit" / "ring.h")
    assert sorted(kept) == [
        "tk_checksum",
        "tk_ring_capacity",
        "tk_ring_free",
        "tk_ring_len",
        "tk_ring_new",
        "tk_ring_pop",
        "tk_ring_push",
        "tk_ring_sum",
    ]
    assert kept["tk_ring_len"].endswith("(const struct tk_ring *)")
    assert cpp.c_declarations(ctx.cpp, exported) == kept
    # The mixed build left the crate as it was: Corrosion builds it FROZEN, out of the tree.
    crate = root / "rust" / "textkit_ring"
    assert sorted(p.name for p in crate.iterdir()) == ["Cargo.lock", "Cargo.toml", "src"]


PORT_LEGACY = ROOT / "fixtures" / "cpp" / "port-legacy"


def _module(module: str) -> CodemodSpec:
    options = {"crate": "calc_rs", "module": module, "build_target": "calc"}
    return CodemodSpec(tool="rust-module", options=options)


def test_the_module_scaffold(make_contexts: MakeContexts) -> None:
    """The ``rust-module`` codemod on ``port-legacy`` (D-58): a workspace with the library's
    crate, the module and its bridge, the generated bridge header and the Corrosion block. The
    C++98 build links the empty crate and passes its tests; the port's checks are quiet; a
    second module joins the same crate; running it again changes nothing."""
    ctx = make_contexts(PORT_LEGACY, "cpp98")
    rust, cpp = RustAdapter(), CppAdapter()
    unit = ["CMakeLists.txt", "include/calc/strutil.h", "src/strutil.cpp", "tests/CMakeLists.txt"]
    result = rust.run_codemods(ctx.rust, [_module("strutil")], unit)
    assert result.diagnostics == []
    assert sorted(c.file for c in result.changes) == [
        "CMakeLists.txt",
        "rust/Cargo.lock",
        "rust/Cargo.toml",
        "rust/calc_rs/Cargo.toml",
        "rust/calc_rs/cbindgen.toml",
        "rust/calc_rs/include/calc_rs/bridge.h",
        "rust/calc_rs/include/calc_rs/bridge.hpp",
        "rust/calc_rs/src/bridge/mod.rs",
        "rust/calc_rs/src/bridge/strutil.rs",
        "rust/calc_rs/src/lib.rs",
        "rust/calc_rs/src/strutil.rs",
        "rust/symbol-map.toml",
    ]
    root = ctx.worktree
    header = (root / "rust/calc_rs/include/calc_rs/bridge.h").read_text(encoding="utf-8")
    assert "typedef struct CalcRsStr {" in header
    assert "typedef struct CalcRsSink {" in header
    top = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)" in top
    assert "target_include_directories(calc PUBLIC rust/calc_rs/include)" in top
    assert "target_link_libraries(calc PUBLIC calc_rs)" in top
    assert (root / "rust/calc_rs/src/strutil.rs").read_text(encoding="utf-8") == (
        "//! Ported from include/calc/strutil.h, src/strutil.cpp (CodeCoreMorph, D-58).\n"
    )
    assert rust.run_codemods(ctx.rust, [_module("strutil")], unit).changes == []

    lexer = ["CMakeLists.txt", "include/calc/lexer.h", "src/lexer.cpp"]
    second = rust.run_codemods(ctx.rust, [_module("lexer")], lexer)
    assert sorted(c.file for c in second.changes) == [
        "rust/calc_rs/src/bridge/lexer.rs",
        "rust/calc_rs/src/bridge/mod.rs",
        "rust/calc_rs/src/lexer.rs",
        "rust/calc_rs/src/lib.rs",
    ]
    lib = (root / "rust/calc_rs/src/lib.rs").read_text(encoding="utf-8")
    assert lib.endswith("pub mod bridge;\npub mod strutil;\npub mod lexer;\n")

    assert rust.configure(ctx.rust).success
    configured = cpp.configure(ctx.cpp)
    assert configured.success, configured.diagnostics
    built = cpp.build(ctx.cpp)
    assert built.success, [d.message for d in built.errors]
    report = cpp.run_tests(ctx.cpp)
    assert report.cases
    assert [c.test_id for c in report.cases if c.status is not TestStatus.PASSED] == []

    assert rust.unsafe_sites(ctx.rust) == []
    assert [d.message for d in rust.lint(ctx.rust) if d.severity is not Severity.NOTE] == []
    index = rust.index(ctx.rust)
    from codecoremorph.store.index import IndexStore

    with IndexStore.open(index.path) as store:
        items = {s.qualified_name for s in store.symbols()}
    assert {"calc_rs::bridge::CalcRsStr", "calc_rs::bridge::CalcRsStr::bytes"} <= items

    (root / "rust/calc_rs/src/strutil.rs").write_text(
        "#![allow(unsafe_code)]\npub fn f() {}\n", encoding="utf-8"
    )
    assert [(s.file, s.line) for s in rust.unsafe_sites(ctx.rust)] == [
        ("rust/calc_rs/src/strutil.rs", 1)
    ]
