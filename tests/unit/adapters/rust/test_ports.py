"""A module port's Rust side (D-58): names, the scaffold's texts, the Corrosion block's include
lines and the checks on unsafe code."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.rust import ports
from codecoremorph.adapters.rust.cargo import (
    find_crates,
    find_packages,
    with_corrosion_block,
)
from codecoremorph.adapters.rust.symbols import mask


@pytest.mark.parametrize(
    ("target", "stem", "taken", "expected"),
    [
        ("calc", "rational", (), ("calc_rs", "rational")),
        ("Calc-Core", "StrUtil", (), ("calc_core_rs", "str_util")),
        ("calc", "type", (), ("calc_rs", "type_mod")),
        ("calc", "lib", (), ("calc_rs", "lib_mod")),
        ("calc", "bridge", (), ("calc_rs", "bridge_mod")),
        ("calc", "parse", ("parse",), ("calc_rs", "parse_2")),
        ("2d", "3d", (), ("m_2d_rs", "m_3d")),
    ],
)
def test_port_names(
    target: str, stem: str, taken: tuple[str, ...], expected: tuple[str, str]
) -> None:
    assert ports.port_names(target, stem, taken) == expected


def test_workspace_members_and_module_declarations() -> None:
    manifest = ports.workspace_manifest(["calc_rs"])
    assert 'members = ["calc_rs"]' in manifest
    two = ports.with_member(manifest, "aaa_rs")
    assert 'members = ["aaa_rs", "calc_rs"]' in two
    assert ports.with_member(two, "calc_rs") == two
    lib = ports.lib_rs("calc_rs", "calc")
    assert "#![deny(unsafe_code)]" in lib
    assert "#[allow(unsafe_code)]\npub mod bridge;" in lib
    once = ports.with_module(lib, "rational")
    assert once.endswith("pub mod bridge;\npub mod rational;\n")
    assert ports.with_module(once, "rational") == once
    bridge = ports.with_module(ports.bridge_mod_rs("calc_rs"), "rational", public=False)
    assert bridge.endswith("\nmod rational;\n")
    assert "pub struct CalcRsStr" in bridge
    assert "pub struct CalcRsSink" in bridge


def test_the_crates_metadata_and_helpers() -> None:
    manifest = ports.package_manifest("calc_rs", "2021", "calc")
    assert 'crate-type = ["staticlib", "rlib"]' in manifest
    assert ports.cmake_target(manifest) == "calc"
    assert ports.cmake_target("[package]\nname = 'x'\n") is None
    header = ports.bridge_hpp("calc_rs")
    assert '#include "calc_rs/bridge.h"' in header
    assert "inline CalcRsSink into(std::string& out)" in header
    assert "nullptr" not in header  # C++98
    assert "auto " not in header
    config = ports.cbindgen_toml("calc_rs")
    assert "cpp_compat = true" in config
    assert 'include = ["CalcRsStr", "CalcRsSink"]' in config


def test_unsafe_stays_in_the_bridge() -> None:
    lib = ports.lib_rs("calc_rs", "calc") + "pub mod rational;\n"
    assert ports.unsafe_problems("lib.rs", mask(lib), {"src/rational.rs": "fn f() {}"}) == []
    # The root lost its deny, another module allows unsafe code, a comment doesn't count.
    loose = lib.replace("#![deny(unsafe_code)]", "// #![deny(unsafe_code)]")
    loose = loose.replace("pub mod rational;", "#[allow(unsafe_code)]\npub mod rational;")
    others = {
        "src/rational.rs": mask("// #[allow(unsafe_code)]\n#![allow(unsafe_code)]\nfn f() {}"),
    }
    assert ports.unsafe_problems("lib.rs", mask(loose), others) == [
        ("lib.rs", 1, "the crate root must keep #![deny(unsafe_code)]"),
        ("lib.rs", 8, "allows unsafe code"),
        ("src/rational.rs", 2, "allows unsafe code"),
    ]


def test_corrosion_block_with_include_directories() -> None:
    text = "project(TinyCalc CXX)\nadd_library(calc src/a.cpp)\n"
    once = with_corrosion_block(
        text, "rust/Cargo.toml", ("calc", "calc_rs"), ("calc", "rust/calc_rs/include")
    )
    assert once.endswith(
        "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)\n"
        "target_include_directories(calc PUBLIC rust/calc_rs/include)\n"
        "target_link_libraries(calc PUBLIC calc_rs)\n"
        "# <<< CodeCoreMorph: the Rust crates\n"
    )
    again = with_corrosion_block(
        once, "rust/Cargo.toml", ("calc", "calc_rs"), ("calc", "rust/calc_rs/include")
    )
    assert again == once
    # Phase 12's block has no include line, and keeps its text (D-57).
    plain = with_corrosion_block(text, "rust/x/Cargo.toml", ("calc", "x"))
    assert "target_include_directories" not in plain


def test_workspace_members_build_through_the_workspace(tmp_path: Path) -> None:
    def manifest(rel: str, text: str) -> None:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        (tmp_path / rel / "Cargo.toml").write_text(text, encoding="utf-8")

    manifest("rust", ports.workspace_manifest(["calc_rs"]))
    manifest("rust/calc_rs", ports.package_manifest("calc_rs", "2021", "calc"))
    manifest("rust/textkit_ring", '[package]\nname = "textkit_ring"\n')  # inside, not a member
    manifest("tools/gen", '[package]\nname = "gen"\n')
    assert find_crates(tmp_path) == ["rust", "tools/gen"]
    assert find_packages(tmp_path) == ["rust/calc_rs", "rust/textkit_ring", "tools/gen"]


# --------------------------------------------------------------------------- Phase 14 (D-60)


def test_a_programs_package_and_its_cmake_target_going() -> None:
    manifest = ports.bin_manifest("tinycalc_rs", "2021", "tinycalc", "tinycalc", ["calc_rs"])
    assert '[[bin]]\nname = "tinycalc"\npath = "src/main.rs"' in manifest
    assert 'calc_rs = { path = "../calc_rs" }' in manifest
    assert ports.cmake_target(manifest) == "tinycalc"
    assert "#![forbid(unsafe_code)]" in ports.main_rs(["app/main.cpp"])
    cmake = (
        "add_library(calc a.cpp)\n"
        "target_include_directories(calc PUBLIC include)\n"
        "\n"
        "add_executable(tinycalc\n    app/main.cpp)\n"
        "target_link_libraries(tinycalc PRIVATE calc)\n"
        "target_link_libraries(tinycalc2 PRIVATE calc)\n"
        "\n"
        "enable_testing()\n"
    )
    assert ports.without_cmake_target(cmake, "tinycalc") == (
        "add_library(calc a.cpp)\n"
        "target_include_directories(calc PUBLIC include)\n"
        "\n"
        "target_link_libraries(tinycalc2 PRIVATE calc)\n"
        "\n"
        "enable_testing()\n"
    )
    crlf = "x()\r\n\r\nadd_executable(t m.cpp)\r\n\r\ny()\r\n"
    assert ports.without_cmake_target(crlf, "t") == "x()\r\n\r\ny()\r\n"


def test_a_test_files_scaffold() -> None:
    assert "#![forbid(unsafe_code)]" in ports.test_rs("tests/rational_test.cpp")
    text = ports.with_test_entries(ports.TEST_MAP_TEXT, [("ctest:calc_tests", ports.WRAPPER_NOTE)])
    assert text.endswith(f'[[test]]\ncpp = "ctest:calc_tests"\nnote = "{ports.WRAPPER_NOTE}"\n')
    again = ports.with_test_entries(text, [("ctest:calc_tests", "other")])
    assert again == text
    manifest = ports.package_manifest("calc_rs", "2021", "calc")
    deps = ports.with_dev_dependencies(manifest, ["util_rs"])
    assert deps.endswith('\n[dev-dependencies]\nutil_rs = { path = "../util_rs" }\n')
    assert ports.with_dev_dependencies(deps, ["util_rs"]) == deps


def test_retired_crates() -> None:
    lib = ports.lib_rs("calc_rs", "calc") + "pub mod rational;\n"
    retired = ports.retired_lib_rs(lib, "calc_rs", "calc")
    assert retired == (
        "//! The C++ library `calc`, ported to Rust (CodeCoreMorph, D-58, D-60).\n"
        "#![forbid(unsafe_code)]\n\npub mod rational;\n"
    )
    # A root the agent rewrote keeps its doc; the bridge and the deny still go.
    custom = (
        "//! Mine.\n#![deny(unsafe_code)]\n#[allow(unsafe_code)]\npub mod bridge;\npub mod x;\n"
    )
    assert ports.retired_lib_rs(custom, "calc_rs", "calc") == (
        "//! Mine.\n#![forbid(unsafe_code)]\npub mod x;\n"
    )
    manifest = ports.retired_manifest(ports.package_manifest("calc_rs", "2021", "calc"))
    assert manifest == (
        '[package]\nname = "calc_rs"\nversion = "0.1.0"\nedition = "2021"\npublish = false\n'
    )
    assert ports.retired_workspace(ports.workspace_manifest(["calc_rs"])).startswith(
        "# The Rust port of this project (CodeCoreMorph, D-58, D-60).\n[workspace]\n"
    )
    masked = mask("fn f() { unsafe { g() } }\n// unsafe\n#![forbid(unsafe_code)]\n")
    assert ports.unsafe_keywords("x.rs", masked) == [("x.rs", 1, "uses unsafe code")]
