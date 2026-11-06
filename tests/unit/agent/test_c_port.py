"""A C translation unit's context (D-68): what it ports, exports and keeps."""

from __future__ import annotations

from codecoremorph.agent.context import translation_unit_note
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
