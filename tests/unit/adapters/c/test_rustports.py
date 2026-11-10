"""The C side of a Rust → C port (D-75): the CMake files, the façades' metadata, the translated
tests' names and the scaffolds, without a toolchain (the Docker test builds them:
``tests/integration/adapters/test_rust_to_c_port.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.c import CAdapter, rustports
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LanguageConfig

MANIFEST = '[package]\nname = "rpn"\nversion = "0.2.0"\n'


def test_the_library_lists_each_ported_module_once() -> None:
    text = rustports.c_cmake("rpn", ["src/lexer.c"])
    assert rustports.library_sources(text) == ["src/lexer.c"]
    more = rustports.with_source(text, "src/eval.c")
    assert more is not None
    assert rustports.library_sources(more) == ["src/eval.c", "src/lexer.c"]
    assert rustports.with_source(more, "src/eval.c") == more
    assert rustports.with_source("add_library(x STATIC a.c)\n", "src/eval.c") is None
    top = rustports.top_cmake("rpn", "c99")
    assert "set(CMAKE_C_STANDARD 99)" in top
    assert rustports.SANITIZE in top
    assert "add_subdirectory(c)" in top


def test_translated_tests_are_ctest_tests_named_as_the_rust_tests() -> None:
    text = rustports.tests_cmake(
        "rpn",
        {"test_eval": ("test_eval.c", "rpn::eval::tests"), "test_cli": ("test_cli.c", "rpn::cli")},
    )
    assert rustports.test_programs(text) == {
        "test_cli": ("test_cli.c", "rpn::cli"),
        "test_eval": ("test_eval.c", "rpn::eval::tests"),
    }
    assert "target_link_libraries(${program} PRIVATE rpn_c)" in text
    files = {
        "test_eval.c": "int main(void) {\n    CCM_RUN(evaluates_in_order);\n"
        "  CCM_RUN( empty );\n}\n"
    }
    assert rustports.test_ids(text, files) == {
        "ctest:rpn::eval::tests::evaluates_in_order": ("test_eval.c", "evaluates_in_order"),
        "ctest:rpn::eval::tests::empty": ("test_eval.c", "empty"),
    }


@pytest.mark.parametrize(
    ("rust_file", "ids", "names"),
    [
        ("src/eval.rs", ["nextest:rpn::eval::tests::a", "nextest:rpn::eval::tests::b"],
         ("test_eval", "test_eval.c", "rpn::eval::tests")),
        ("tests/cli.rs", ["nextest:rpn::cli::runs"], ("test_cli", "test_cli.c", "rpn::cli")),
        ("src/geo/mod.rs", ["nextest:g::geo::tests::x", "nextest:g::geo::tests::inner::y"],
         ("test_geo", "test_geo.c", "g::geo::tests")),
        ("src/lib.rs", ["nextest:rpn::tests::t"], ("test_rpn", "test_rpn.c", "rpn::tests")),
    ],
)  # fmt: skip
def test_test_names(rust_file: str, ids: list[str], names: tuple[str, str, str]) -> None:
    assert rustports.test_names(rust_file, "rpn", ids) == names


def test_the_manifest_names_each_facades_header() -> None:
    once = rustports.with_c_module(MANIFEST, "src/lexer.rs", "c/include/rpn/lexer.h")
    twice = rustports.with_c_module(once, "src/eval.rs", "c/include/rpn/eval.h")
    assert rustports.c_modules(twice) == {
        "src/lexer.rs": "c/include/rpn/lexer.h",
        "src/eval.rs": "c/include/rpn/eval.h",
    }
    assert rustports.with_c_module(twice, "src/eval.rs", "c/include/rpn/eval.h") == twice
    assert rustports.c_modules(rustports.without_c_modules(twice)) == {}
    crlf = rustports.with_c_module(MANIFEST.replace("\n", "\r\n"), "src/a.rs", "c/include/rpn/a.h")
    assert "\n" not in crlf.replace("\r\n", "")  # the manifest keeps its line endings
    assert rustports.c_modules("not toml [") == {}
    assert rustports.sys_path("src/stats.rs") == "src/stats/sys.rs"
    assert rustports.sys_path("src/geo/mod.rs") == "src/geo/sys.rs"


def test_the_module_scaffold_and_the_retirement(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("version = 3\n", encoding="utf-8")
    (tmp_path / "src" / "lexer.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    options = {"crate": "rpn", "module": "lexer", "build_target": "rpn"}
    changed = rustports.scaffold(tmp_path, "c-module", options, ["src/lexer.rs"], "c99")
    assert set(changed) == {
        "CMakeLists.txt",
        "Cargo.toml",
        "build.rs",
        "c/CMakeLists.txt",
        "c/cargo-link",
        "c/include/rpn/lexer.h",
        "c/src/lexer.c",
        "c/symbol-map.toml",
        "src/lexer/sys.rs",
    }
    assert (tmp_path / "build.rs").read_text(encoding="utf-8") == rustports.RUST_C_LINK_SCRIPT
    assert rustports.scaffold(tmp_path, "c-module", options, ["src/lexer.rs"], "c99") == []
    with pytest.raises(ConfigError):
        rustports.scaffold(tmp_path, "c-module", {"crate": "Bad-Name"}, ["src/lexer.rs"], "c99")
    retired = rustports.scaffold(tmp_path, "c-retire", {}, [], "c99")
    assert {"Cargo.toml", "Cargo.lock", "build.rs", "c/cargo-link", "src/lexer.rs"} <= set(retired)
    assert not (tmp_path / "src").exists()
    assert (tmp_path / "CMakeLists.txt").is_file()
    assert (tmp_path / "c" / "symbol-map.toml").is_file()


def test_a_rust_ports_c_target_runs_in_the_rust_image() -> None:
    c = CAdapter()
    lang = LanguageConfig(language="c", standard="c99", build_system="cmake")
    assert c.toolchain_requirements(lang).image == "codecoremorph-cpp"
    port = c.toolchain_requirements(lang.model_copy(update={"port_source": "rust"}))
    assert (port.language, port.image) == ("rust", "codecoremorph-rust")
    assert c.symbol_map_path() == "c/symbol-map.toml"
    assert c.test_map_path() == "c/test-map.toml"


def _workspace(root: Path) -> None:
    """The layout a C → Rust port leaves (the round trip): a workspace under rust/ with the
    library's package and a program's package that uses it."""
    files = {
        "rust/Cargo.toml": '[workspace]\nmembers = ["wf_rs", "wordfreq_rs"]\nresolver = "2"\n',
        "rust/Cargo.lock": "version = 3\n",
        "rust/.gitignore": "/target/\n",
        "rust/test-map.toml": "# the C → Rust port's map\n",
        "rust/wf_rs/Cargo.toml": '[package]\nname = "wf_rs"\nversion = "0.1.0"\n',
        "rust/wf_rs/src/lib.rs": "pub mod htab;\n",
        "rust/wf_rs/src/htab.rs": "pub fn hash() {}\n",
        "rust/wf_rs/tests/test_htab.rs": "#[test]\nfn a() {}\n",
        "rust/wordfreq_rs/Cargo.toml": '[package]\nname = "wordfreq_rs"\nversion = "0.1.0"\n\n'
        '[dependencies]\nwf_rs = { path = "../wf_rs" }\n',
        "rust/wordfreq_rs/src/main.rs": "fn main() {}\n",
        "tests/sample.txt": "the data the program's tests read\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_a_workspace_members_crate(tmp_path: Path) -> None:
    """The C, its build and the maps go at the repository's root; the build script, the
    façades' sys modules and their metadata go in the library's package."""
    _workspace(tmp_path)
    assert rustports.package_of(tmp_path, "rust/wf_rs/src/htab.rs") == "rust/wf_rs"
    assert rustports.package_of(tmp_path, "tests/sample.txt") == "."
    assert rustports.library_package(tmp_path, "wf_rs") == "rust/wf_rs"
    options = {"crate": "wf_rs", "module": "htab", "build_target": "wf_rs"}
    changed = rustports.scaffold(tmp_path, "c-module", options, ["rust/wf_rs/src/htab.rs"], "c99")
    assert set(changed) == {
        "CMakeLists.txt",
        "c/CMakeLists.txt",
        "c/cargo-link",
        "c/include/wf_rs/htab.h",
        "c/src/htab.c",
        "c/symbol-map.toml",
        "rust/wf_rs/Cargo.toml",
        "rust/wf_rs/build.rs",
        "rust/wf_rs/src/htab/sys.rs",
    }
    manifest = (tmp_path / "rust/wf_rs/Cargo.toml").read_text(encoding="utf-8")
    assert rustports.c_modules(manifest) == {"rust/wf_rs/src/htab.rs": "c/include/wf_rs/htab.h"}
    assert rustports.linked_library((tmp_path / "c/cargo-link").read_text("utf-8")) == "wf_rs_c"
    program = {"crate": "wf_rs", "module": "wordfreq", "bin": "wordfreq", "build_target": "wf_rs"}
    changed = rustports.scaffold(
        tmp_path, "c-program", program, ["rust/wordfreq_rs/src/main.rs"], "c99"
    )
    assert "rust/wordfreq_rs/build.rs" not in changed  # the library's package links the C
    assert "int wf_rs_wordfreq_main(int argc, char **argv);" in (
        tmp_path / "c/include/wf_rs/wordfreq.h"
    ).read_text(encoding="utf-8")
    tests = {"crate": "wf_rs", "module": "test_htab", "tests": "nextest:wf_rs::test_htab::a"}
    rustports.scaffold(tmp_path, "c-tests", tests, ["rust/wf_rs/tests/test_htab.rs"], "c99")
    cmake = (tmp_path / rustports.TESTS_CMAKE).read_text(encoding="utf-8")
    assert rustports.test_programs(cmake) == {"test_htab": ("test_htab.c", "wf_rs::test_htab")}
    assert 'CCM_SOURCE_DIR=\\"${CMAKE_SOURCE_DIR}\\"' in cmake
    retired = rustports.scaffold(tmp_path, "c-retire", {}, [], "c99")
    assert {
        "rust/Cargo.toml",
        "rust/Cargo.lock",
        "rust/wf_rs/Cargo.toml",
        "rust/wf_rs/build.rs",
        "rust/wf_rs/src/htab.rs",
        "rust/wf_rs/tests/test_htab.rs",
        "rust/wordfreq_rs/Cargo.toml",
        "rust/wordfreq_rs/src/main.rs",
        "c/cargo-link",
    } <= set(retired)
    # What Cargo didn't own stays: the workspace's .gitignore, the earlier port's map, the data.
    left = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert [p for p in left if not p.startswith("c/")] == [
        "CMakeLists.txt",
        "rust/.gitignore",
        "rust/test-map.toml",
        "tests/sample.txt",
    ]
    assert not (tmp_path / "rust/wf_rs").exists()


def test_a_test_file_named_test_keeps_its_name() -> None:
    assert rustports.test_names("tests/test_htab.rs", "wf_rs", ["nextest:wf_rs::test_htab::a"]) == (
        "test_htab",
        "test_htab.c",
        "wf_rs::test_htab",
    )
    assert rustports.linked_library("# comment\nm\n") is None


def test_a_modules_unit_with_the_crate_root_scaffolds_the_module(tmp_path: Path) -> None:
    """A module's unit also has the crate root (for its attributes): the scaffold makes the
    module's façade, not the root's, though src/lib.rs sorts before src/sbuf.rs. The round
    trip's host run found it, the third module's metadata naming lib.rs."""
    _workspace(tmp_path)
    (tmp_path / "rust/wf_rs/src/sbuf.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    options = {"crate": "wf_rs", "module": "sbuf", "build_target": "wf_rs"}
    files = ["rust/Cargo.toml", "rust/wf_rs/Cargo.toml", "rust/wf_rs/src/lib.rs",
             "rust/wf_rs/src/sbuf.rs"]  # fmt: skip
    changed = rustports.scaffold(tmp_path, "c-module", options, files, "c99")
    assert "rust/wf_rs/src/sbuf/sys.rs" in changed
    assert "rust/wf_rs/src/lib/sys.rs" not in changed
    manifest = (tmp_path / "rust/wf_rs/Cargo.toml").read_text(encoding="utf-8")
    assert rustports.c_modules(manifest) == {"rust/wf_rs/src/sbuf.rs": "c/include/wf_rs/sbuf.h"}
    # The crate root's own unit (a root with public items) has no other module file.
    root = rustports.scaffold(
        tmp_path, "c-module", {**options, "module": "wf_rs"}, ["rust/wf_rs/src/lib.rs"], "c99"
    )
    assert "rust/wf_rs/src/lib/sys.rs" in root


def test_the_retirement_deletes_the_packages_its_unit_names(tmp_path: Path) -> None:
    """shlex's fuzz/ is a cargo-fuzz workspace the manifest excludes: the retirement, whose
    unit names the root package's manifest alone, leaves it, as a unit naming no manifest would
    not."""
    files = {
        "Cargo.toml": MANIFEST,
        "Cargo.lock": "version = 3\n",
        "src/lib.rs": "pub mod bytes;\n",
        "src/bytes.rs": "pub fn f() {}\n",
        "build.rs": rustports.RUST_C_LINK_SCRIPT,
        "src/bytes/sys.rs": "//! bindgen's\n",
        "c/cargo-link": "static=rpn_c\n",
        "fuzz/Cargo.toml": '[package]\nname = "fuzz"\n\n[workspace]\nmembers = ["."]\n',
        "fuzz/Cargo.lock": "version = 3\n",
        "fuzz/fuzz_targets/fuzz_next.rs": "fn main() {}\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    unit = ["Cargo.lock", "Cargo.toml", "src/bytes.rs", "src/lib.rs"]
    retired = rustports.scaffold(tmp_path, "c-retire", {}, unit, "c99")
    assert set(retired) == {*unit, "build.rs", "src/bytes/sys.rs", "c/cargo-link"}
    left = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert left == ["fuzz/Cargo.lock", "fuzz/Cargo.toml", "fuzz/fuzz_targets/fuzz_next.rs"]


def test_clang_tidy_checks_bugs_and_warnings_not_cs_idioms() -> None:
    """The compiler's warnings count (``-*`` alone would turn them off), and what the
    bug-finding checks say of C's idioms doesn't (D-80)."""
    argv = rustports.clang_tidy_argv("/build", ["/workspace/c/src/stats.c"])
    checks = next(a for a in argv if a.startswith("--checks="))
    assert "clang-diagnostic-*" in checks
    assert "bugprone-*" in checks
    assert "-bugprone-multi-level-implicit-pointer-conversion" in checks
    config = next(a for a in argv if a.startswith("--config="))
    assert "WarnOnSizeOfPointerToAggregate: false" in config
    assert "_POSIX_C_SOURCE" in config
    assert argv[-1] == "/workspace/c/src/stats.c"
