"""A C port's Rust side with the real toolchain (D-68), on the ``c-legacy`` fixture: the
``rust-module`` scaffold of the ``sbuf.c`` unit (crate, bridge, ``sys`` module, Make block,
archive fold), then a hand port of the file: the Make build folds the crate into ``libwf.a``,
every C test passes against it, bindgen's ``sys`` drops what the crate exports, and the
interface it exports matches the kept header's. Make builds run in Docker first, so the test is
marked ``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, TestStatus
from codecoremorph.invariants.abi import c_abi_differences

pytestmark = pytest.mark.docker

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "c" / "c-legacy"
EXPORTS = ("sbuf_init", "sbuf_append", "sbuf_putc", "sbuf_reset", "sbuf_free")

MODULE = """\
//! Ported from src/sbuf.c (CodeCoreMorph, D-68).

/// The smallest capacity a buffer grows to.
pub const MIN_CAPACITY: usize = 16;

/// Why a buffer can't hold more bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TooLarge;

/// The capacity a buffer of `len` bytes in `cap` needs for `extra` more and a NUL: `None` when
/// it has room, doubling from `MIN_CAPACITY` otherwise.
pub fn capacity_for(len: usize, cap: usize, extra: usize) -> Result<Option<usize>, TooLarge> {
    let need = len.checked_add(extra).ok_or(TooLarge)?;
    let wanted = need.checked_add(1).ok_or(TooLarge)?;
    if wanted <= cap {
        return Ok(None);
    }
    let mut grown = if cap == 0 { MIN_CAPACITY } else { cap };
    while grown < wanted {
        grown = grown.checked_mul(2).ok_or(TooLarge)?;
    }
    Ok(Some(grown))
}
"""

BRIDGE = """\
//! The C functions of src/sbuf.c, exported under their names over `crate::sbuf`.

use std::ffi::{c_char, c_int, c_void};

use super::sys;
use crate::sbuf::capacity_for;

extern "C" {
    fn realloc(ptr: *mut c_void, size: usize) -> *mut c_void;
    fn free(ptr: *mut c_void);
}

#[no_mangle]
pub unsafe extern "C" fn sbuf_init(b: *mut sys::sbuf) {
    (*b).data = std::ptr::null_mut();
    (*b).len = 0;
    (*b).cap = 0;
}

unsafe fn grow(b: &mut sys::sbuf, extra: usize) -> sys::wf_status {
    match capacity_for(b.len, b.cap, extra) {
        Err(_) => sys::wf_status_WF_ERANGE,
        Ok(None) => sys::wf_status_WF_OK,
        Ok(Some(cap)) => {
            let data = realloc(b.data.cast(), cap);
            if data.is_null() {
                return sys::wf_status_WF_ENOMEM;
            }
            b.data = data.cast();
            b.cap = cap;
            sys::wf_status_WF_OK
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn sbuf_append(
    b: *mut sys::sbuf,
    s: *const c_char,
    n: usize,
) -> sys::wf_status {
    let b = &mut *b;
    let status = grow(b, n);
    if status != sys::wf_status_WF_OK {
        return status;
    }
    std::ptr::copy(s, b.data.add(b.len), n);
    b.len += n;
    *b.data.add(b.len) = 0;
    sys::wf_status_WF_OK
}

#[no_mangle]
pub unsafe extern "C" fn sbuf_putc(b: *mut sys::sbuf, c: c_int) -> sys::wf_status {
    let ch = c as c_char;
    sbuf_append(b, &ch, 1)
}

#[no_mangle]
pub unsafe extern "C" fn sbuf_reset(b: *mut sys::sbuf) {
    (*b).len = 0;
    if !(*b).data.is_null() {
        *(*b).data = 0;
    }
}

#[no_mangle]
pub unsafe extern "C" fn sbuf_free(b: *mut sys::sbuf) {
    free((*b).data.cast());
    sbuf_init(b);
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

    work = tmp_path / "c-legacy"
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
                standard="c89",
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


def test_a_c_file_ported_by_hand(ctx: Contexts) -> None:
    c, rust = CAdapter(), RustAdapter()
    work = ctx.c.worktree
    assert c.configure(ctx.c).success
    assert (ctx.c.build_dir / "compile_commands.json").is_file()  # for bindgen

    # The scaffold of the unit that ports src/sbuf.c.
    spec = CodemodSpec(
        tool="rust-module",
        options={
            "language": "c",
            "crate": "wf_rs",
            "module": "sbuf",
            "build_target": "wf",
            "archive": "libwf.a",
            "headers": "include/wf.h,src/wfint.h",
        },
    )
    scaffold = rust.run_codemods(ctx.rust, [spec], ["Makefile", "src/sbuf.c"])
    assert not scaffold.diagnostics, scaffold.diagnostics
    changed = set(scaffold.changed_files)
    assert {
        "Makefile",
        "rust/Cargo.toml",
        "rust/Cargo.lock",
        "rust/wf_rs/Cargo.toml",
        "rust/wf_rs/src/lib.rs",
        "rust/wf_rs/src/sbuf.rs",
        "rust/wf_rs/src/bridge/mod.rs",
        "rust/wf_rs/src/bridge/sbuf.rs",
        "rust/wf_rs/src/bridge/sys.rs",
        "rust/symbol-map.toml",
    } <= changed
    sys_rs = (work / "rust/wf_rs/src/bridge/sys.rs").read_text(encoding="utf-8")
    assert "pub struct sbuf" in sys_rs
    assert "pub fn sbuf_append" in sys_rs  # still C
    makefile = (work / "Makefile").read_text(encoding="utf-8")
    assert "ADDLIB $(CODECOREMORPH_RUST_wf_rs)" in makefile
    assert "-L. -lwf $(CODECOREMORPH_RUST_LIBS)" in makefile
    again = rust.run_codemods(ctx.rust, [spec], ["Makefile", "src/sbuf.c"])
    assert not again.changed_files  # idempotent
    # The unit's first build, as a run makes one: the archive takes the still empty crate.
    scaffolded = c.build(ctx.c)
    assert scaffolded.success, scaffolded.diagnostics

    # The port: Rust takes over, the C file and its object go.
    (work / "rust/wf_rs/src/sbuf.rs").write_text(MODULE, encoding="utf-8")
    (work / "rust/wf_rs/src/bridge/sbuf.rs").write_text(BRIDGE, encoding="utf-8")
    (work / "src/sbuf.c").unlink()
    makefile = makefile.replace("OBJS = src/sbuf.o ", "OBJS = ").replace(
        "src/sbuf.o src/htab.o", "src/htab.o"
    )
    (work / "Makefile").write_text(makefile, encoding="utf-8")

    configured = rust.configure(ctx.rust)  # sys again, without what the crate exports
    assert configured.success, configured.diagnostics
    sys_rs = (work / "rust/wf_rs/src/bridge/sys.rs").read_text(encoding="utf-8")
    assert "pub fn sbuf_append" not in sys_rs
    assert "pub fn htab_add" in sys_rs
    assert c.configure(ctx.c).success
    built = c.build(ctx.c)
    assert built.success, built.diagnostics
    # The build folded the rebuilt crate in, so the archive was made again, without sbuf.o.
    assert b"sbuf.o/" not in (ctx.c.cache_dir / "make-mirror" / "libwf.a").read_bytes()
    report = c.run_tests(ctx.c)
    statuses = {t.test_id: t.status for t in report.cases}
    assert statuses["make:test"] is TestStatus.PASSED
    tap = [t for t in statuses if t.startswith("tap:")]
    assert len(tap) == 40
    assert all(statuses[t] is TestStatus.PASSED for t in tap)
    own = rust.build(ctx.rust)
    assert own.success, own.diagnostics
    # lint_clean: clippy is quiet about the scaffold, bindgen's code and this port, and the
    # crate copies no header type.
    assert [d for d in rust.lint(ctx.rust) if d.severity.value != "note"] == []

    # c_abi_kept: the exports against the kept header's declarations of them.
    [exported] = rust.exported_c_headers(ctx.rust, ["rust/wf_rs/src/bridge/sbuf.rs"])
    declared = c.c_declarations(ctx.c, work / "src" / "wfint.h")
    expected = {name: declared[name] for name in EXPORTS}
    assert c_abi_differences(expected, c.c_declarations(ctx.c, exported)) == []
    assert not (work / "rust" / "target").exists()  # the Make build ran in the mirror

    # The program's scaffold: its Make rule now copies the Rust program to its path.
    program = CodemodSpec(
        tool="rust-bin",
        options={
            "language": "c",
            "crate": "wordfreq_rs",
            "bin": "wordfreq",
            "build_target": "wordfreq",
            "artifact": "wordfreq",
            "dependencies": "wf_rs",
        },
    )
    made = rust.run_codemods(ctx.rust, [program], ["Makefile", "tools/wordfreq.c"])
    assert not made.diagnostics, made.diagnostics
    makefile = (work / "Makefile").read_text(encoding="utf-8")
    assert "wordfreq:\n\tcp $(CODECOREMORPH_RUST_BIN_wordfreq) $@\n" in makefile
    assert "wordfreq: $(CODECOREMORPH_RUST_BIN_wordfreq)\n" in makefile  # in the block
    assert (work / "rust/wordfreq_rs/src/main.rs").is_file()
    assert (work / "rust/test-map.toml").is_file()
    assert c.configure(ctx.c).success
    assert c.build(ctx.c).success
    mirror = ctx.c.cache_dir / "make-mirror"
    built = (mirror / "rust/target/release/wordfreq").read_bytes()
    assert (mirror / "wordfreq").read_bytes() == built  # the C program's path has Rust's
