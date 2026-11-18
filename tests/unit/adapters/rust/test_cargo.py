"""The Rust adapter's pure helpers: Cargo's JSON diagnostics, nextest's JUnit report, crates and
the crate scaffold with its Corrosion block (SPEC §11.5, D-56)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.rust.cargo import (
    CORROSION_BEGIN,
    CORROSION_END,
    cbindgen_config,
    crate_files,
    edition,
    feature_runs,
    fetched_packages,
    find_crates,
    library_compiling,
    nextest_config,
    parse_cargo_messages,
    parse_nextest_junit,
    valid_crate_name,
    with_corrosion_block,
    without_corrosion_block,
)
from codecoremorph.core.models import DiagnosticOrigin, Severity, TestStatus

ROOTS = ["/workspace", "E:\\wt"]


def _message(
    level: str,
    text: str,
    *,
    file: str | None = "src/lib.rs",
    line: int = 3,
    code: str | None = None,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spans = (
        [{"file_name": file, "line_start": line, "column_start": 5, "is_primary": True}]
        if file is not None
        else []
    )
    return {
        "level": level,
        "message": text,
        "code": {"code": code} if code else None,
        "spans": spans,
        "children": children or [],
    }


def _record(message: dict[str, Any]) -> str:
    return json.dumps({"reason": "compiler-message", "message": message})


def test_editions() -> None:
    assert edition("rust2021") == "2021"
    with pytest.raises(ValueError, match="not a Rust edition"):
        edition("cpp17")
    assert valid_crate_name("textkit_ring")
    assert not valid_crate_name("Textkit-ring")


def test_cargo_errors_name_worktree_files() -> None:
    out = "\n".join(
        [
            "   Compiling textkit_ring v0.1.0",
            _record(
                _message(
                    "error",
                    "mismatched types",
                    code="E0308",
                    children=[_message("help", "try `as i64`", file=None)],
                )
            ),
            _record(_message("error", "aborting due to 1 previous error", file=None)),
            json.dumps({"reason": "build-finished", "success": False}),
            "not json {",
        ]
    )
    [diagnostic] = parse_cargo_messages(out, "rust/textkit_ring", ROOTS)
    assert diagnostic.file_path == "rust/textkit_ring/src/lib.rs"
    assert (diagnostic.line, diagnostic.column) == (3, 5)
    assert diagnostic.severity is Severity.ERROR
    assert diagnostic.code == "E0308"
    assert diagnostic.origin is DiagnosticOrigin.COMPILER
    assert [n.message for n in diagnostic.notes] == ["try `as i64`"]


def test_absolute_spans_and_duplicates() -> None:
    inside = _record(_message("warning", "unused", file="/workspace/rust/a/src/x.rs"))
    windows = _record(_message("warning", "unused", file="e:\\wt\\rust\\a\\src\\y.rs"))
    outside = _record(_message("warning", "std", file="/rustc/abc/library/core/src/x.rs"))
    found = parse_cargo_messages("\n".join([inside, inside, windows, outside]), "rust/a", ROOTS)
    assert [d.file_path for d in found] == [
        "rust/a/src/x.rs",
        "rust/a/src/y.rs",
        "/rustc/abc/library/core/src/x.rs",
    ]


def test_clippy_lints_are_analyzer_diagnostics() -> None:
    out = "\n".join(
        [
            _record(_message("warning", "needless return", code="clippy::needless_return")),
            _record(_message("warning", "unused variable", code="unused_variables", line=9)),
        ]
    )
    lint, compiler = parse_cargo_messages(out, ".", ROOTS, analyzer=True)
    assert lint.origin is DiagnosticOrigin.ANALYZER
    assert compiler.origin is DiagnosticOrigin.COMPILER
    assert lint.file_path == "src/lib.rs"


JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="nextest-run" tests="3" failures="1" errors="0">
  <testsuite name="textkit_ring" tests="3">
    <testcase name="tests::wraps" classname="textkit_ring" time="0.002"/>
    <testcase name="tests::sums" classname="textkit_ring" time="0.010">
      <failure message="assertion failed: left == right">panicked at src/lib.rs:90</failure>
    </testcase>
    <testcase name="tests::slow" classname="textkit_ring" time="0"><skipped/></testcase>
  </testsuite>
</testsuites>
"""


def test_nextest_junit() -> None:
    cases = {c.test_id: c for c in parse_nextest_junit(JUNIT)}
    assert cases["nextest:textkit_ring::tests::wraps"].status is TestStatus.PASSED
    failed = cases["nextest:textkit_ring::tests::sums"]
    assert failed.status is TestStatus.FAILED
    assert failed.message == "assertion failed: left == right"
    assert failed.duration_s == pytest.approx(0.01)
    assert cases["nextest:textkit_ring::tests::slow"].status is TestStatus.SKIPPED
    config = nextest_config("C:\\cache\\nextest")
    assert "[store]\ndir = 'C:\\cache\\nextest'" in config  # never the crate's own target/
    assert 'path = "junit.xml"' in config
    with pytest.raises(ValueError, match="TOML"):
        nextest_config("it's")


def test_nextest_junit_refuses_a_dtd() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuites/>'
    with pytest.raises(ValueError, match="DTD"):
        parse_nextest_junit(evil)


def test_find_crates(tmp_path: Path) -> None:
    for crate in ("rust/a", "rust/b", "third_party/c", "rust/a/target/debug/build/x"):
        (tmp_path / crate).mkdir(parents=True)
        (tmp_path / crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert find_crates(tmp_path, ["third_party/**"]) == ["rust/a", "rust/b"]


def test_crate_files() -> None:
    files = crate_files("textkit_ring", "2021", doc="The port.")
    assert sorted(files) == ["Cargo.lock", "Cargo.toml", "src/lib.rs"]
    assert 'name = "textkit_ring"' in files["Cargo.toml"]
    assert 'edition = "2021"' in files["Cargo.toml"]
    assert 'crate-type = ["staticlib"]' in files["Cargo.toml"]
    assert "version = 4" in files["Cargo.lock"]
    assert files["src/lib.rs"] == "//! The port.\n"


def test_library_compiling() -> None:
    top = "add_library(textkit\n    src/ring.cpp\n    src/report.cpp\n)\n"
    nested = 'add_library(util STATIC "x.cpp")\nadd_executable(tool main.cpp)\n'
    files = [("CMakeLists.txt", top), ("lib/CMakeLists.txt", nested)]
    assert library_compiling(files, "src/ring.cpp") == "textkit"
    assert library_compiling(files, "lib/x.cpp") == "util"
    assert library_compiling(files, "lib/main.cpp") is None


def test_corrosion_block_is_appended_once_and_grows() -> None:
    text = "project(Textkit CXX)\nadd_subdirectory(tests)\n"
    once = with_corrosion_block(text, "rust/a/Cargo.toml", ("textkit", "textkit_a"))
    assert once.startswith(text)
    assert once.endswith(
        "\n".join(
            [
                CORROSION_BEGIN,
                "find_package(Corrosion REQUIRED)",
                "corrosion_import_crate(MANIFEST_PATH rust/a/Cargo.toml FROZEN)",
                "set_property(TARGET textkit APPEND PROPERTY LINK_LIBRARIES textkit_a)",
                "set_property(TARGET textkit APPEND PROPERTY INTERFACE_LINK_LIBRARIES "
                "$<BUILD_INTERFACE:textkit_a>)",
                CORROSION_END,
            ]
        )
        + "\n"
    )
    assert with_corrosion_block(once, "rust/a/Cargo.toml", ("textkit", "textkit_a")) == once
    twice = with_corrosion_block(once, "rust/b/Cargo.toml", None)
    assert twice.count(CORROSION_BEGIN) == 1
    assert "corrosion_import_crate(MANIFEST_PATH rust/b/Cargo.toml FROZEN)" in twice
    assert twice.count("rust/a/Cargo.toml") == 1
    assert "set_property(TARGET textkit APPEND PROPERTY LINK_LIBRARIES textkit_a)" in twice
    base, inner = without_corrosion_block(twice)
    assert base == text
    assert len(inner) == 5


def test_corrosion_block_keeps_crlf() -> None:
    text = "project(T CXX)\r\nadd_library(t a.cpp)\r\n"
    updated = with_corrosion_block(text, "rust/t/Cargo.toml", ("t", "t_a"))
    assert "\n" not in updated.replace("\r\n", "")
    assert without_corrosion_block(updated)[0] == text


def test_fetched_packages() -> None:
    lock = """\
version = 4

[[package]]
name = "textkit_ring"
version = "0.1.0"
dependencies = [
 "crc32fast",
]

[[package]]
name = "crc32fast"
version = "1.4.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "a97769d94ddab943e4510d138150169a2758b5ef3eb191a9ee688de3e23ef7b3"
"""
    assert fetched_packages(lock) == ["crc32fast 1.4.2"]
    assert fetched_packages(crate_files("x", "2021", doc="d")["Cargo.lock"]) == []


def test_cbindgen_config() -> None:
    config = cbindgen_config()
    assert 'language = "C"' in config
    assert "usize_is_size_t = true" in config


def test_a_ports_corrosion_block_links_through_the_targets_properties() -> None:
    # cJSON links its library with the plain signature, which CMake won't mix with PUBLIC,
    # and installs it with install(EXPORT), which refuses a crate in no export set (D-89).
    text = 'add_library(cjson cJSON.h)\ntarget_link_libraries(cjson "${MATH}")\n'
    once = with_corrosion_block(text, "rust/Cargo.toml", ("cjson", "cjson_rs"))
    assert once.endswith(
        "\n".join(
            [
                "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)",
                "set_property(TARGET cjson APPEND PROPERTY LINK_LIBRARIES cjson_rs)",
                "set_property(TARGET cjson APPEND PROPERTY INTERFACE_LINK_LIBRARIES "
                "$<BUILD_INTERFACE:cjson_rs>)",
                CORROSION_END,
            ]
        )
        + "\n"
    )
    again = with_corrosion_block(once, "rust/Cargo.toml", ("cjson", "cjson_rs"))
    assert again == once
    both = with_corrosion_block(once, "rust/Cargo.toml", ("cjson_utils", "cjson_utils_rs"))
    assert both.count("corrosion_import_crate(") == 1
    assert both.count("PROPERTY LINK_LIBRARIES") == 2
    assert "target_link_libraries(cjson PUBLIC" not in both


def test_a_block_an_earlier_version_wrote_is_rewritten_with_its_crates() -> None:
    # A C++ port's block from before D-89 links with PUBLIC and names a source directory in
    # the target's interface, which a project's install(EXPORT) refuses.
    old = (
        "project(T CXX)\n"
        f"{CORROSION_BEGIN}\nfind_package(Corrosion REQUIRED)\n"
        "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)\n"
        "target_include_directories(calc PUBLIC rust/calc_rs/include)\n"
        f"target_link_libraries(calc PUBLIC calc_rs)\n{CORROSION_END}\n"
    )
    updated = with_corrosion_block(old, "rust/Cargo.toml", ("calc", "calc_rs"))
    _, inner = without_corrosion_block(updated)
    assert inner == [
        "find_package(Corrosion REQUIRED)",
        "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)",
        "target_include_directories(calc PUBLIC "
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/rust/calc_rs/include>)",
        "set_property(TARGET calc APPEND PROPERTY LINK_LIBRARIES calc_rs)",
        "set_property(TARGET calc APPEND PROPERTY INTERFACE_LINK_LIBRARIES "
        "$<BUILD_INTERFACE:calc_rs>)",
    ]
    assert with_corrosion_block(updated, "rust/Cargo.toml", ("calc", "calc_rs")) == updated


def test_each_feature_of_a_package_is_a_run_of_its_own() -> None:
    # http-parser's port: the C built with and without its strict checks (D-89).
    manifests = {
        "rust/http_parser_rs": (
            '[package]\nname = "http_parser_rs"\n\n[features]\ndefault = []\nstrict = []\n'
        ),
        "rust/aes_rs": '[package]\nname = "aes_rs"\n\n[features]\naes256 = []\naes192 = []\n',
        "rust/plain_rs": '[package]\nname = "plain_rs"\n',
        "rust/broken": "[package\n",
    }
    assert feature_runs(manifests) == [
        ("aes_rs", "aes192"),
        ("aes_rs", "aes256"),
        ("http_parser_rs", "strict"),
    ]


def test_a_feature_the_defaults_turn_on_is_no_run_of_its_own() -> None:
    # shlex's `default = ["std"]`, and a feature the defaults reach through another.
    manifest = (
        '[package]\nname = "shlex"\n\n[features]\ndefault = ["std"]\nstd = ["alloc"]\n'
        'alloc = []\nserde = ["dep:serde", "alloc"]\n'
    )
    assert feature_runs({".": manifest}) == [("shlex", "serde")]
