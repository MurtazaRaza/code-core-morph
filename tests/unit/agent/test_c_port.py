"""A C translation unit's context (D-68): what it ports, exports and keeps."""

from __future__ import annotations

from codecoremorph.agent.context import module_port_note, translation_unit_note
from codecoremorph.core.models import PortUnit, WorkUnit


def test_the_note_of_a_c_unit() -> None:
    port = PortUnit(
        build_target="wf",
        crate="wf",
        module="htab",
        callers=["src/wf.c", "tests/test_htab.c"],
        c_headers=["src/wfint.h"],
        exports=["htab_init", "htab_add"],
    )
    unit = WorkUnit(
        id="U-0002",
        seq=2,
        rules=["port_c_module"],
        files=["Makefile", "include/wf.h", "src/htab.c", "src/htab_priv.h", "src/wfint.h"],
        reference_files=["include/wf.h", "src/wfint.h"],
        port=port,
    )
    note = translation_unit_note(unit, port)
    assert note.startswith(
        "Port src/htab.c, src/htab_priv.h to the Rust module `wf::htab` (rust/wf/src/htab.rs, "
        "safe Rust), and delete them once Rust does the job"
    )
    assert "rust/wf/src/bridge/htab.rs" in note
    assert ": htab_init, htab_add." in note
    assert "(read-only): include/wf.h, src/wfint.h." in note
    assert "`wf::bridge::sys` (rust/wf/src/bridge/sys.rs" in note
    assert "don't change: src/wf.c, tests/test_htab.c." in note


def test_the_note_of_a_c_program() -> None:
    from codecoremorph.agent.context import c_executable_note

    port = PortUnit(
        build_target="wordfreq",
        crate="wordfreq_rs",
        module="main",
        bin="wordfreq",
        dependencies=["wf_rs"],
        artifact="wordfreq",
    )
    note = c_executable_note(port)
    assert note.startswith(
        "Port the C program `wordfreq` to Rust: rust/wordfreq_rs/src/main.rs, the `bin` named "
        "`wordfreq`, which the Make build copies to wordfreq."
    )
    assert "library crates (wf_rs), never their `bridge` or `sys` modules" in note
    assert "These tests run it" not in note
    assert note.endswith("\n\n")


def test_an_automake_unit_and_what_stays_c() -> None:
    """With automake, the source leaves its library's `_SOURCES`; a function taking `...` or a
    `va_list` stays in the C file, over Rust (D-70)."""
    port = PortUnit(
        build_target="kvconf", crate="kvconf_rs", module="log", exports=["kv_log_write"]
    )
    unit = WorkUnit(
        id="U-0002",
        seq=2,
        rules=["port_c_module"],
        files=["Makefile.am", "include/kvconf.h", "src/Makefile.am", "src/log.c"],
        reference_files=["include/kvconf.h"],
        port=port,
    )
    plain = translation_unit_note(unit, port)
    assert (
        "delete it once Rust does the job, with its file in its library's `_SOURCES` in the "
        "Makefile.am." in plain
    )
    note = translation_unit_note(unit, port, [("src/log.c", "kv_log"), ("src/log.c", "kv_vlog")])
    assert "safe Rust). `kv_log` and `kv_vlog` stay in src/log.c: stable Rust can't define" in note
    assert "delete it once" not in note
    assert "the file stays in the build until the retirement deletes it. Export" in note


def test_a_whole_automake_test_program_maps_to_several_rust_tests() -> None:
    from codecoremorph.agent.context import c_tests_note

    port = PortUnit(
        build_target="test_value", crate="kvconf_rs", module="test_value",
        tests=["make:tests/test_value"],
    )  # fmt: skip
    unit = WorkUnit(
        id="U-0008", seq=8, rules=["port_c_tests"], files=["tests/test_value.c"],
        reference_files=["tests/test_value.c"], port=port,
    )  # fmt: skip
    note = c_tests_note(unit, port)
    assert (
        ": make:tests/test_value. make:tests/test_value is a whole program (automake's "
        "test, by its exit status): give its checks Rust tests and map it to all of them" in note
    )
    tap = port.model_copy(update={"tests": ["tap:tests/test_sbuf/grows"]})
    assert "whole program" not in c_tests_note(unit, tap)


def test_the_note_of_a_c_retirement() -> None:
    from codecoremorph.agent.context import c_retirement_note

    files = ["include/wf.h", "src/wfint.h", "tests/test_htab.c"]
    whole = WorkUnit(id="U-0011", seq=11, rules=["retire_c"], files=["Makefile", *files])
    assert c_retirement_note(whole).startswith(
        "The codemod retired the C: it deleted 4 C and Make files and the crates' C bridges"
    )
    # Keeping the public API (D-72): the header stays, read-only, and so do the build and the
    # bridge, which must go on exporting the header's functions.
    kept = WorkUnit(
        id="U-0011",
        seq=11,
        rules=["retire_c"],
        files=files,
        reference_files=["include/wf.h"],
        kept_api=["include/wf.h"],
    )
    note = c_retirement_note(kept)
    assert note.startswith(
        "The codemod deleted the 2 C files the kept C interface doesn't need (keep_source_api). "
        "include/wf.h stays exactly as it is, with the headers it includes, the Make build"
    )
    assert "the C tests that use only it." in note
    assert "Keep exporting every function the kept headers declare (c_abi_kept" in note
    assert "take what the deleted files needed out of the Makefile" in note


def test_a_unit_with_white_box_tests_gets_a_stand_in() -> None:
    """Tests that #include the C file itself (D-89) keep testing the Rust through a stand-in
    under rust/c-whitebox/; a unit without them gets no such paragraph."""
    port = PortUnit(
        build_target="cjson",
        crate="cjson_rs",
        module="c_json",
        exports=["cJSON_Parse"],
        white_box=["tests/common.h"],
        white_box_tests=["tests/parse_number.c", "tests/print_number.c"],
        white_box_uses=["parse_number", "global_hooks"],
    )
    unit = WorkUnit(
        id="U-0001", seq=1, rules=["port_c_module"], files=["CMakeLists.txt", "cJSON.c"],
        port=port,
    )  # fmt: skip
    note = translation_unit_note(unit, port)
    assert "**White-box tests.** tests/common.h `#include` cJSON.c itself" in note
    assert ": `parse_number`, `global_hooks`." in note
    assert "don't change: tests/parse_number.c, tests/print_number.c." in note
    assert "`rust/c-whitebox/cJSON.c`" in note
    assert '`#[no_mangle] pub extern "C" fn cjson_rs_test_<name>`' in note
    plain = port.model_copy(update={"white_box": [], "white_box_tests": [], "white_box_uses": []})
    assert "White-box" not in translation_unit_note(unit, plain)


def test_a_cpp_module_with_white_box_tests_keeps_what_they_use_in_its_facade() -> None:
    """A C++ test that includes the module's source (D-89): the source stays as the façade
    until the retirement, so it keeps what the test uses, over the bridge's test hooks."""
    port = PortUnit(
        build_target="lib",
        crate="lib_rs",
        module="b",
        white_box=["tests/b_internals_test.cpp"],
        white_box_tests=["tests/b_internals_test.cpp"],
        white_box_uses=["b_helper"],
    )
    note = module_port_note(port)
    assert "**White-box tests.** tests/b_internals_test.cpp `#include` one of the module's" in note
    assert "(a `static` function, an anonymous namespace's): `b_helper`." in note
    assert '`#[no_mangle] pub extern "C" fn lib_rs_test_<name>`' in note
    assert "c-whitebox" not in note  # the façade is the stand-in
    plain = port.model_copy(update={"white_box": [], "white_box_tests": [], "white_box_uses": []})
    assert "White-box" not in module_port_note(plain)


def test_a_cmake_units_file_leaves_its_target_and_corrosion_links_the_crate() -> None:
    port = PortUnit(
        build_target="cjson", crate="cjson_rs", module="c_json", exports=["cJSON_Parse"]
    )
    unit = WorkUnit(
        id="U-0001", seq=1, rules=["port_c_module"],
        files=["CMakeLists.txt", "cJSON.c", "cJSON.h", "tests/CMakeLists.txt"], port=port,
    )  # fmt: skip
    note = translation_unit_note(unit, port)
    assert "with the file out of its library target's sources in the CMake files" in note
    assert "marked Corrosion block at the end of the top-level CMakeLists.txt" in note
    assert "`LINKER_LANGUAGE C`" in note
    make = unit.model_copy(update={"files": ["CMakeLists.txt", "Makefile", "cJSON.c"]})
    assert "with its objects in the build files" in translation_unit_note(make, port)
