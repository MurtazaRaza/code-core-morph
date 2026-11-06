"""A C port's Rust side in an automake and libtool build (D-70), on the ``c-autotools``
fixture: the ``rust-module`` scaffold of the ``strutil.c`` unit writes its marked block in
``src/Makefile.am``, a hand port of the file passes every automake test against the crate,
libtool's static archive holds the crate as a member, and the program's scaffold makes
``tools/kvdump`` the Rust program. Make builds run in Docker first, so the test is marked
``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.cpp.make import MakeBuild
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, TestStatus

pytestmark = pytest.mark.docker

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "c" / "c-autotools"
TESTS = ("dump", "test_doc", "test_log", "test_parse", "test_value")

MODULE = """\
//! Ported from src/strutil.c (CodeCoreMorph, D-68).

/// Spaces and tabs.
pub fn is_space(c: u8) -> bool {
    c == b' ' || c == b'\\t'
}

/// Where the text is once spaces and tabs are trimmed off both ends: `(start, len)`.
pub fn trim(s: &[u8]) -> (usize, usize) {
    let start = s.iter().position(|&c| !is_space(c)).unwrap_or(s.len());
    let end = s.iter().rposition(|&c| !is_space(c)).map_or(start, |e| e + 1);
    (start, end - start)
}

/// ASCII order without case: negative, zero or positive.
pub fn casecmp(a: &[u8], b: &[u8]) -> i32 {
    for i in 0.. {
        let ca = i32::from(a.get(i).map_or(0, u8::to_ascii_lowercase));
        let cb = i32::from(b.get(i).map_or(0, u8::to_ascii_lowercase));
        if ca != cb || ca == 0 {
            return ca - cb;
        }
    }
    0
}

/// Letters, digits, `_`, `-` and `.`, and not empty.
pub fn valid_name(s: &[u8]) -> bool {
    !s.is_empty()
        && s.iter().all(|&c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-' || c == b'.')
}
"""

BRIDGE = """\
//! The C functions of src/strutil.c, exported under their names over `crate::strutil`.

use std::ffi::{c_char, c_int, c_uint, c_void, CStr};

use super::sys;
use crate::strutil;

extern "C" {
    fn malloc(size: usize) -> *mut c_void;
}

#[no_mangle]
pub unsafe extern "C" fn kvi_strndup(s: *const c_char, len: usize) -> *mut c_char {
    let copy = malloc(len + 1).cast::<c_char>();
    if !copy.is_null() {
        std::ptr::copy_nonoverlapping(s, copy, len);
        *copy.add(len) = 0;
    }
    copy
}

#[no_mangle]
pub unsafe extern "C" fn kvi_casecmp(a: *const c_char, b: *const c_char) -> c_int {
    strutil::casecmp(CStr::from_ptr(a).to_bytes(), CStr::from_ptr(b).to_bytes())
}

#[no_mangle]
pub unsafe extern "C" fn kvi_casencmp(a: *const c_char, b: *const c_char, len: usize) -> c_int {
    for i in 0..len {
        let ca = i32::from((*a.add(i) as u8).to_ascii_lowercase());
        let cb = i32::from((*b.add(i) as u8).to_ascii_lowercase());
        if ca != cb {
            return ca - cb;
        }
        if cb == 0 {
            return 0;
        }
    }
    if *b.add(len) == 0 {
        0
    } else {
        -1
    }
}

#[no_mangle]
pub unsafe extern "C" fn kvi_trim(s: *mut *const c_char, len: usize) -> usize {
    let text = std::slice::from_raw_parts((*s).cast::<u8>(), len);
    let (start, n) = strutil::trim(text);
    *s = (*s).add(start);
    n
}

#[no_mangle]
pub unsafe extern "C" fn kvi_lower(s: *mut c_char) {
    let mut p = s;
    while *p != 0 {
        *p = (*p as u8).to_ascii_lowercase() as c_char;
        p = p.add(1);
    }
}

#[no_mangle]
pub unsafe extern "C" fn kvi_valid_name(s: *const c_char, len: usize) -> c_int {
    c_int::from(strutil::valid_name(std::slice::from_raw_parts(s.cast::<u8>(), len)))
}

#[no_mangle]
pub unsafe extern "C" fn kvi_copy_name(
    dst: *mut c_char,
    src: *const c_char,
    len: usize,
    flags: c_uint,
) -> c_int {
    if len >= sys::KV_NAME_MAX as usize {
        return sys::kv_status_KV_ERANGE;
    }
    std::ptr::copy_nonoverlapping(src, dst, len);
    *dst.add(len) = 0;
    if flags & sys::KV_OPT_CASE_FOLD != 0 {
        kvi_lower(dst);
    }
    sys::kv_status_KV_OK
}
"""


@dataclass
class Contexts:
    c: AdapterContext
    rust: AdapterContext


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[Contexts]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "c-autotools"
    shutil.copytree(FIXTURE, work)
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    rust_lang = LanguageConfig(
        language="rust", standard="rust2021", target_standard="rust2021", build_timeout_s=900
    )
    try:
        image = resolve_image(RustAdapter().toolchain_requirements(rust_lang), None)
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
        c = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LanguageConfig(
                language="c",
                standard="c99",
                build_system="make",
                test_dirs=["tests"],
                public_api=["include/**"],
                build_timeout_s=900,
                test_timeout_s=300,
            ),
        )
        rust = replace(
            c, cache_dir=tmp_path / "cache" / "rust", log_dir=tmp_path / "logs" / "rust",
            lang=rust_lang,
        )  # fmt: skip
        yield Contexts(c, rust)
    finally:
        executor.stop()


def test_an_automake_file_ported_by_hand(ctx: Contexts) -> None:
    c, rust = CAdapter(), RustAdapter()
    work = ctx.c.worktree
    mirror = ctx.c.cache_dir / "make-mirror"
    assert c.configure(ctx.c).success  # autoreconf, ./configure, a first build
    baseline = c.run_tests(ctx.c)
    assert sorted(t.test_id for t in baseline.cases) == [f"make:tests/{t}" for t in TESTS]
    assert all(t.status is TestStatus.PASSED for t in baseline.cases)
    targets = {t.name: t for t in MakeBuild(ctx.c).targets()}
    assert targets["kvconf"].artifacts == [
        "src/.libs/libkvconf.a",
        "src/.libs/libkvconf.so.3.0.1",
    ]
    assert targets["kvdump"].artifacts == ["tools/kvdump"]  # libtool's wrapper, not .libs/

    spec = CodemodSpec(
        tool="rust-module",
        options={
            "language": "c",
            "crate": "kvconf_rs",
            "module": "strutil",
            "build_target": "kvconf",
            "archive": "src/.libs/libkvconf.a",
            "headers": "include/kvconf.h,src/kvint.h",
        },
    )
    files = ["Makefile.am", "src/Makefile.am", "src/strutil.c"]
    scaffold = rust.run_codemods(ctx.rust, [spec], files)
    assert not scaffold.diagnostics, scaffold.diagnostics
    assert "src/Makefile.am" in scaffold.changed_files
    am = (work / "src/Makefile.am").read_text(encoding="utf-8")
    assert "libkvconf_la_LIBADD = codecoremorph-kvconf_rs.lo $(CODECOREMORPH_RUST_LIBS)" in am
    assert (work / "Makefile.am").read_text(encoding="utf-8") == (
        FIXTURE / "Makefile.am"
    ).read_text(encoding="utf-8")
    sys_rs = (work / "rust/kvconf_rs/src/bridge/sys.rs").read_text(encoding="utf-8")
    assert "pub struct kv_value" in sys_rs
    assert "pub union kv_value__bindgen_ty_1" in sys_rs  # the union stays C's
    assert "pub fn kvi_trim" in sys_rs  # still C
    assert not rust.run_codemods(ctx.rust, [spec], files).changed_files  # idempotent
    # The unit's first build, as a run makes one: the crate is still empty.
    scaffolded = c.build(ctx.c)
    assert scaffolded.success, scaffolded.diagnostics

    # The port: Rust takes over, and the file leaves the library's sources.
    (work / "rust/kvconf_rs/src/strutil.rs").write_text(MODULE, encoding="utf-8")
    (work / "rust/kvconf_rs/src/bridge/strutil.rs").write_text(BRIDGE, encoding="utf-8")
    (work / "src/strutil.c").unlink()
    am = am.replace("libkvconf_la_SOURCES = strutil.c ", "libkvconf_la_SOURCES = ")
    (work / "src/Makefile.am").write_text(am, encoding="utf-8")
    configured = rust.configure(ctx.rust)
    assert configured.success, configured.diagnostics
    assert "pub fn kvi_trim" not in (work / "rust/kvconf_rs/src/bridge/sys.rs").read_text(
        encoding="utf-8"
    )
    # Makefile.am changed: autoreconf and ./configure first. The crate cargo rebuilds reaches
    # the library in this make, not the next (D-70).
    built = c.build(ctx.c)
    assert built.success, built.diagnostics
    report = c.run_tests(ctx.c)
    assert {t.test_id: t.status for t in report.cases} == {
        f"make:tests/{t}": TestStatus.PASSED for t in TESTS
    }
    own = rust.build(ctx.rust)
    assert own.success, own.diagnostics
    assert [d for d in rust.lint(ctx.rust) if d.severity.value != "note"] == []
    # libtool's static archive holds the crate as a member, beside the C objects.
    archive = (mirror / "src/.libs/libkvconf.a").read_bytes()
    assert b"codecoremorph-kvconf_rs.o/" in archive
    assert b"strutil.o/" not in archive
    assert not (work / "rust" / "target").exists()  # the Make build ran in the mirror

    # The program's scaffold: no C sources, and a link that copies the Rust program.
    program = CodemodSpec(
        tool="rust-bin",
        options={
            "language": "c",
            "crate": "kvdump_rs",
            "bin": "kvdump",
            "build_target": "kvdump",
            "artifact": "tools/kvdump",
            "dependencies": "kvconf_rs",
        },
    )
    made = rust.run_codemods(ctx.rust, [program], ["tools/Makefile.am", "tools/kvdump.c"])
    assert not made.diagnostics, made.diagnostics
    tools = (work / "tools/Makefile.am").read_text(encoding="utf-8")
    assert "kvdump_SOURCES =\n" in tools
    assert "kvdump_LINK = cp $(CODECOREMORPH_RUST_BIN_kvdump) $@ && :" in tools
    assert c.build(ctx.c).success
    rust_bin = (mirror / "rust/target/release/kvdump").read_bytes()
    assert (mirror / "tools/kvdump").read_bytes() == rust_bin
