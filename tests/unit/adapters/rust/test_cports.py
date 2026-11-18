"""A C port's Rust side (D-68): the crate manifest's kept headers, the bindgen command and its
flags, and the Make edits (the marked block, the archive fold, the native libraries)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters import leftovers
from codecoremorph.adapters.rust import cports

C_LEGACY = Path(__file__).resolve().parents[4] / "fixtures" / "c" / "c-legacy" / "Makefile"
WF = cports.Crate(
    name="wf_rs",
    library="wf",
    archive="libwf.a",
    staticlib="rust/target/release/libwf_rs.a",
    target="libwf.a",
)


def test_the_manifest_keeps_more_headers() -> None:
    manifest = cports.package_manifest("wf_rs", "2021", "wf", "libwf.a", ["src/wfint.h"])
    assert cports.manifest_headers(manifest) == ["src/wfint.h"]
    assert cports.manifest_archive(manifest) == "libwf.a"
    assert cports.manifest_library(manifest) == "wf"
    wider = cports.with_headers(manifest, ["include/wf.h", "src/wfint.h"])
    assert cports.manifest_headers(wider) == ["include/wf.h", "src/wfint.h"]
    assert cports.with_headers(wider, ["src/wfint.h"]) == wider
    # A retirement that keeps the public API deletes the other headers (D-72).
    narrower = cports.without_headers(wider, ["src/wfint.h", "tests/tap.h"])
    assert cports.manifest_headers(narrower) == ["include/wf.h"]
    assert cports.without_headers(narrower, ["src/wfint.h"]) == narrower


def test_the_workspace_has_the_release_profile() -> None:
    manifest = cports.workspace_manifest(["wf_rs"])
    assert manifest.count("[profile.release]") == 1
    assert cports.with_release_profile(manifest) == manifest
    plain = '[workspace]\nmembers = ["wf_rs"]\n'
    assert 'strip = "debuginfo"' in cports.with_release_profile(plain)


def test_bindgen_reads_the_project_headers_without_the_exports() -> None:
    argv = cports.bindgen_argv(
        "/c/w.h", "/w/rust/wf_rs/src/bridge/sys.rs", "/w", ["sbuf_init", "wf.x"], ["-I/w/src"]
    )
    assert argv[:4] == ["bindgen", "/c/w.h", "--output", "/w/rust/wf_rs/src/bridge/sys.rs"]
    assert argv[argv.index("--allowlist-file") + 1] == "/w/.*"
    assert argv[argv.index("--blocklist-function") + 1] == r"^(sbuf_init|wf\.x)$"
    assert argv[argv.index("--blocklist-var") + 1] == r"^(sbuf_init|wf\.x)$"
    assert argv[argv.index("--") :] == ["--", "-x", "c", "-std=c11", "-I/w/src"]
    assert "--blocklist-function" not in cports.bindgen_argv("w.h", "o.rs", "/w", [], [])


def test_clang_args_from_a_compile_database() -> None:
    entries = [
        {
            "directory": "/w",
            "arguments": ["cc", "-O2", "-Iinclude", "-I", "src", "-DBIG=1", "-c", "src/a.c"],
            "file": "src/a.c",
        },
        {
            "directory": "/w/tools",
            "command": "cc -I../include -iquote /m/gen -include cfg.h -m64 -o t t.c",
            "file": "tools/t.c",
        },
        {"directory": "/w", "arguments": ["cc", "-Iinclude", "-UBAD", "-c", "src/b.c"]},
    ]
    assert cports.clang_args(entries) == [
        "-I", "/w/include", "-I", "/w/src", "-DBIG=1", "-iquote", "/m/gen",
        "-include", "/w/tools/cfg.h", "-m64", "-UBAD",
    ]  # fmt: skip


def test_native_libs_from_rustc() -> None:
    note = "note: native-static-libs: -lgcc_s -lutil -lm -lc\n"
    assert cports.native_libs(note) == ["-lgcc_s", "-lutil", "-lm", "-lc"]
    assert cports.native_libs("error: no") is None


def test_the_c_legacy_makefile() -> None:
    text = C_LEGACY.read_text(encoding="utf-8")
    assert cports.rule_making(text, "libwf.a") == "libwf.a"
    folded = cports.fold_archive(text, WF)
    # The recipe already removes the archive, so it only gains the fold, at its end.
    assert "\trm -f libwf.a\n\t$(AR) cq libwf.a $(OBJS)\n\tprintf 'OPEN $@\\nADDLIB " in folded
    assert "$(CODECOREMORPH_RUST_wf_rs)\\nSAVE\\nEND\\n' | $(AR) -M\n" in folded
    assert cports.fold_archive(folded, WF) == folded
    linked = cports.with_native_libs(folded, [WF])
    assert "-o wordfreq tools/wordfreq.o -L. -lwf $(CODECOREMORPH_RUST_LIBS)\n" in linked
    assert "-o tests/test_sbuf tests/test_sbuf.o -L. -lwf $(CODECOREMORPH_RUST_LIBS)\n" in linked
    assert linked.count("$(CODECOREMORPH_RUST_LIBS)") == 5  # the program and four tests
    assert cports.with_native_libs(linked, [WF]) == linked
    block = cports.make_block([WF], "rust/Cargo.toml", ["-lgcc_s", "-lm"])
    once = cports.with_make_block(linked, block)
    assert once.endswith(block)
    assert "CODECOREMORPH_RUST_LIBS = -lgcc_s -lm\n" in block
    assert "libwf.a: $(CODECOREMORPH_RUST_wf_rs)\n" in block
    # A recipe, so make reads the staticlib's time again once cargo ran: else the archive
    # waits for the next make to take the rebuilt crate (D-70).
    assert "$(CODECOREMORPH_RUST_wf_rs): codecoremorph-rust\n\t@:\n" in block
    assert "\t$(CODECOREMORPH_CARGO) build --release --locked --offline --manifest-path " in block
    again = cports.with_make_block(once, cports.make_block([WF], "rust/Cargo.toml", ["-lm"]))
    assert again.count(cports.MAKE_BEGIN) == 1
    assert "CODECOREMORPH_RUST_LIBS = -lm\n" in again


def test_an_archive_rule_that_keeps_old_members_starts_over() -> None:
    """zlib's rule (`ar rc`, a variable for the archive): a deleted source's object would stay
    in the archive, so the recipe starts by removing it."""
    text = (
        "STATICLIB=libz.a\nAR=ar\nARFLAGS=rc\nTEST_LDFLAGS=-L. libz.a\n\n"
        "$(STATICLIB): $(OBJS)\n"
        "\t$(AR) $(ARFLAGS) $@ $(OBJS)\n"
        "\t-@ ($(RANLIB) $@ || true) >/dev/null 2>&1\n\n"
        "example$(EXE): example.o $(STATICLIB)\n"
        "\t$(CC) $(CFLAGS) -o $@ example.o $(TEST_LDFLAGS)\n\n"
        "install-libs: $(LIBS)\n"
        "\tcp $(STATICLIB) $(DESTDIR)$(libdir)\n"
    )
    z = cports.Crate("z_rs", "z", "libz.a", "rust/target/release/libz_rs.a", "$(STATICLIB)")
    assert cports.rule_making(text, "libz.a") == "$(STATICLIB)"
    folded = cports.fold_archive(text, z)
    assert "$(STATICLIB): $(OBJS)\n\trm -f $@\n\t$(AR) $(ARFLAGS) $@ $(OBJS)\n" in folded
    assert "2>&1\n\tprintf 'OPEN $@\\nADDLIB $(CODECOREMORPH_RUST_z_rs)" in folded
    linked = cports.with_native_libs(folded, [z])
    assert "example.o $(TEST_LDFLAGS) $(CODECOREMORPH_RUST_LIBS)\n" in linked
    assert "\tcp $(STATICLIB) $(DESTDIR)$(libdir)\n" in linked  # not a link


def test_a_continued_link_line_gets_the_libraries_at_its_end() -> None:
    text = "prog: prog.o libx.a\n\t$(CC) -o prog prog.o \\\n\t  -L. -lx\n"
    x = cports.Crate("x_rs", "x", "libx.a", "rust/target/release/libx_rs.a", "libx.a")
    assert cports.with_native_libs(text, [x]) == (
        "prog: prog.o libx.a\n\t$(CC) -o prog prog.o \\\n\t  -L. -lx $(CODECOREMORPH_RUST_LIBS)\n"
    )
    assert cports.rule_making(text, "libx.a") is None  # no rule makes it here


def test_a_crate_defines_no_copy_of_a_header_type() -> None:
    sys_text = (
        "pub struct sbuf {\n    pub data: *mut c_char,\n}\n"
        "pub type wf_status = c_uint;\n"
        "pub struct wf_table {\n    _unused: [u8; 0],\n}\n"
    )
    files = {
        "rust/wf_rs/src/bridge/sbuf.rs": "#[repr(C)]\npub struct Sbuf {\n    data: *mut u8,\n}\n",
        "rust/wf_rs/src/sbuf.rs": "pub struct Sbuf {\n    data: Vec<u8>,\n}\n",  # not repr(C)
        "rust/wf_rs/src/wf.rs": "#[repr(C)]\n#[derive(Debug)]\npub(crate) struct wf_table {}\n",
        "rust/wf_rs/src/x.rs": "#[repr(C, u32)]\nenum WfStatus { Ok }\n",
    }
    assert cports.copied_c_types(sys_text, files) == [
        ("rust/wf_rs/src/bridge/sbuf.rs", 2, "Sbuf", "sbuf"),
        ("rust/wf_rs/src/x.rs", 2, "WfStatus", "wf_status"),
    ]


def test_a_program_rule_copies_the_rust_program() -> None:
    text = C_LEGACY.read_text(encoding="utf-8")
    wordfreq = cports.Program(
        name="wordfreq", binary="rust/target/release/wordfreq", target="wordfreq"
    )
    assert cports.rule_making(text, "wordfreq") == "wordfreq"
    replaced = cports.with_rust_program(text, wordfreq)
    assert "wordfreq:\n\tcp $(CODECOREMORPH_RUST_BIN_wordfreq) $@\n" in replaced
    assert "-o wordfreq tools/wordfreq.o" not in replaced
    assert "tests/test_sbuf: tests/test_sbuf.o libwf.a\n" in replaced  # the others stay
    assert cports.with_rust_program(replaced, wordfreq) == replaced
    block = cports.make_block([WF], "rust/Cargo.toml", ["-lm"], [wordfreq])
    variable = block.index("CODECOREMORPH_RUST_BIN_wordfreq = rust/target/release/wordfreq\n")
    # make expands a rule's prerequisites as it reads it: the variable comes first.
    assert block.index("wordfreq: $(CODECOREMORPH_RUST_BIN_wordfreq)\n") > variable
    assert "$(CODECOREMORPH_RUST_BIN_wordfreq): codecoremorph-rust\n" in block
    # A rule whose prerequisites go on over several lines is one rule.
    long = "prog: a.o \\\n\tb.o libx.a\n\t$(CC) -o prog a.o b.o -L. -lx\n\nother:\n\ttrue\n"
    prog = cports.Program(name="prog", binary="rust/target/release/prog", target="prog")
    assert cports.with_rust_program(long, prog) == (
        "prog:\n\tcp $(CODECOREMORPH_RUST_BIN_prog) $@\n\nother:\n\ttrue\n"
    )


def test_a_program_package_names_the_c_program() -> None:
    manifest = cports.bin_manifest("wordfreq_rs", "2021", "wordfreq", "wordfreq", ["wf_rs"])
    assert cports.manifest_program(manifest) == ("wordfreq", "wordfreq")
    assert 'wf_rs = { path = "../wf_rs" }' in manifest
    assert cports.manifest_archive(manifest) is None


def test_the_c_retirement(tmp_path: Path) -> None:
    """``rust-retire`` for C (D-68): the C files and the Make build go, and each crate loses its
    bridge (with ``sys``), its staticlib and its metadata."""
    from codecoremorph.adapters.base import AdapterContext
    from codecoremorph.adapters.rust import RustAdapter
    from codecoremorph.core.models import CodemodSpec, LanguageConfig
    from codecoremorph.sandbox.native import NativeExecutor

    work = tmp_path / "work"
    files = {
        "Makefile": "all:\n",
        "src/wf.c": "int x;\n",
        "include/wf.h": "int x;\n",
        "rust/Cargo.toml": cports.workspace_manifest(["wf_rs", "wordfreq_rs"]),
        "rust/wf_rs/Cargo.toml": cports.package_manifest(
            "wf_rs", "2021", "wf", "libwf.a", ["include/wf.h"]
        ),
        "rust/wf_rs/src/lib.rs": cports.lib_rs("wf") + "pub mod htab;\n",
        "rust/wf_rs/src/htab.rs": "pub fn add() {}\n",
        "rust/wf_rs/src/bridge/mod.rs": cports.bridge_mod_rs(),
        "rust/wf_rs/src/bridge/sys.rs": cports.placeholder_sys(),
        "rust/wordfreq_rs/Cargo.toml": cports.bin_manifest(
            "wordfreq_rs", "2021", "wordfreq", "wordfreq", ["wf_rs"]
        ),
        "rust/wordfreq_rs/src/main.rs": "fn main() {}\n",
        # A white-box test's stand-in for src/wf.c (D-89): it goes with the C tests.
        "rust/c-whitebox/wf.c": "int wf_test_hook(void);\n",
        # Builds the run didn't use (D-89): another platform's makefile goes, an excluded one
        # and the CI are for review, a Makefile that builds no C stays.
        "makefile.msc": "wf.lib: src\\wf.obj\n\tlib /out:wf.lib src\\wf.obj\n",
        "contrib/Makefile": "tool: ../src/wf.c\n\tcc -o tool ../src/wf.c\n",
        ".github/workflows/ci.yml": "jobs:\n  build:\n    steps:\n      - run: make check\n",
        "docs/Makefile": "html:\n\tsphinx-build . _build\n",
    }
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode("utf-8"))  # LF, as the codemods write them
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(
            language="rust",
            standard="rust2021",
            target_standard="rust2021",
            exclude=["contrib/**"],
        ),
    )
    spec = CodemodSpec(tool="rust-retire", options={"language": "c"})
    result = RustAdapter().run_codemods(ctx, [spec], ["Makefile", "src/wf.c", "include/wf.h"])
    assert not (work / "Makefile").exists()
    assert not (work / "src/wf.c").exists()
    assert not (work / "rust/wf_rs/src/bridge").exists()
    assert not (work / "rust/c-whitebox").exists()
    assert "rust/c-whitebox/wf.c" in {c.file for c in result.changes}
    assert not (work / "makefile.msc").exists()
    assert "makefile.msc" in {c.file for c in result.changes}
    assert (work / "contrib/Makefile").exists()
    assert (work / "docs/Makefile").exists()
    left = {s.file: s.reason for s in result.skipped}
    assert sorted(left) == [".github/workflows/ci.yml", "contrib/Makefile"]
    assert "still builds src/wf.c" in left["contrib/Makefile"]
    assert "rewrite it for Cargo" in left[".github/workflows/ci.yml"]
    lib = (work / "rust/wf_rs/src/lib.rs").read_text(encoding="utf-8")
    assert lib == (
        "//! The C library `wf`, ported to Rust (CodeCoreMorph, D-68).\n"
        "#![forbid(unsafe_code)]\n\npub mod htab;\n"
    )
    manifest = (work / "rust/wf_rs/Cargo.toml").read_text(encoding="utf-8")
    assert "staticlib" not in manifest
    assert "codecoremorph" not in manifest
    program = (work / "rust/wordfreq_rs/Cargo.toml").read_text(encoding="utf-8")
    assert "c-artifact" not in program
    assert 'name = "wordfreq"' in program
    workspace = (work / "rust/Cargo.toml").read_text(encoding="utf-8")
    assert workspace.startswith("# The Rust port of this project (CodeCoreMorph, D-68).\n")


def test_the_c_retirement_that_keeps_the_api(tmp_path: Path) -> None:
    """With ``keep`` (D-72): only the unit's files go (the planner leaves the public headers,
    the build and the tests of the kept API out of it); the bridge stays, and the crate stops
    naming the headers that went, so ``sys`` is written from the kept ones."""
    from codecoremorph.adapters.base import AdapterContext
    from codecoremorph.adapters.rust import RustAdapter
    from codecoremorph.core.models import CodemodSpec, LanguageConfig
    from codecoremorph.sandbox.native import NativeExecutor

    work = tmp_path / "work"
    manifest = cports.package_manifest(
        "wf_rs", "2021", "wf", "libwf.a", ["include/wf.h", "src/wfint.h"]
    )
    files = {
        "Makefile": "all:\n",
        "src/wfint.h": "int y;\n",
        "include/wf.h": "int x;\n",
        "tests/test_htab.c": "int main(void) { return 0; }\n",
        "rust/Cargo.toml": cports.workspace_manifest(["wf_rs"]),
        "rust/wf_rs/Cargo.toml": manifest,
        "rust/wf_rs/src/lib.rs": cports.lib_rs("wf"),
        "rust/wf_rs/src/bridge/mod.rs": cports.bridge_mod_rs(),
        "rust/wf_rs/src/bridge/sys.rs": cports.placeholder_sys(),
        "rust/c-whitebox/wf.c": "int wf_test_hook(void);\n",  # the kept tests still need it
    }
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode("utf-8"))
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(language="rust", standard="rust2021", target_standard="rust2021"),
    )
    spec = CodemodSpec(tool="rust-retire", options={"language": "c", "keep": "true"})
    result = RustAdapter().run_codemods(ctx, [spec], ["src/wfint.h", "tests/test_htab.c"])
    assert sorted(c.file for c in result.changes) == [
        "rust/wf_rs/Cargo.toml",
        "src/wfint.h",
        "tests/test_htab.c",
    ]
    assert (work / "Makefile").exists()
    assert (work / "include/wf.h").exists()
    assert (work / "rust/wf_rs/src/bridge/sys.rs").exists()
    assert (work / "rust/c-whitebox/wf.c").exists()
    assert (work / "rust/wf_rs/src/lib.rs").read_text(encoding="utf-8") == cports.lib_rs("wf")
    kept = (work / "rust/wf_rs/Cargo.toml").read_text(encoding="utf-8")
    assert cports.manifest_headers(kept) == ["include/wf.h"]
    assert 'crate-type = ["staticlib", "rlib"]' in kept


# --------------------------------------------------------------------------- automake (D-70)

C_AUTOTOOLS = Path(__file__).resolve().parents[4] / "fixtures" / "c" / "c-autotools"
KVCONF = cports.AmLibrary(
    name="kvconf_rs",
    canonical="libkvconf_la",
    libtool=True,
    staticlib="rust/target/release/libkvconf_rs.a",
    archive="libkvconf.a",
)


def test_automake_names_and_lists() -> None:
    src = (C_AUTOTOOLS / "src" / "Makefile.am").read_text(encoding="utf-8")
    assert cports.am_canonical("libkvconf.la") == "libkvconf_la"
    assert cports.am_canonical("src/kv-dump") == "src_kv_dump"
    assert cports.am_target(src, "libkvconf.la", ["LTLIBRARIES"]) == "libkvconf_la"
    assert cports.am_target(src, "libkvconf.la", ["LIBRARIES"]) is None
    tools = (C_AUTOTOOLS / "tools" / "Makefile.am").read_text(encoding="utf-8")
    assert cports.am_target(tools, "kvdump", ["PROGRAMS"]) == "kvdump"
    # A non-recursive build lists the library by its path; variables expand.
    flat = "NAME = libx\nnoinst_LTLIBRARIES = \\\n\tsub/$(NAME).la\n"
    assert cports.am_target(flat, "sub/libx.la", ["LTLIBRARIES"]) == "sub_libx_la"
    assert cports.am_defined(src) >= {"AM_CFLAGS", "libkvconf_la_SOURCES", "lib_LTLIBRARIES"}


def test_a_libtool_library_takes_the_crate_as_a_libtool_object() -> None:
    """libtool puts a plain object of LIBADD in the shared library only, and a static archive
    in the static one as a member nothing can link: the crate goes in as one object, behind a
    .lo, which reaches both (D-70)."""
    block = cports.am_block([KVCONF], [], ["-lgcc_s", "-lm"], "rust/Cargo.toml", set())
    lines = block.splitlines()
    assert lines[0] == cports.MAKE_BEGIN
    assert lines[-1] == cports.MAKE_END
    assert "CODECOREMORPH_RUST_LIBS = -lgcc_s -lm" in lines
    assert (
        "CODECOREMORPH_RUST_kvconf_rs = $(top_srcdir)/rust/target/release/libkvconf_rs.a" in lines
    )
    assert "libkvconf_la_LIBADD = codecoremorph-kvconf_rs.lo $(CODECOREMORPH_RUST_LIBS)" in lines
    assert "EXTRA_libkvconf_la_DEPENDENCIES = codecoremorph-kvconf_rs.lo" in lines
    assert "CLEANFILES = codecoremorph-kvconf_rs.o codecoremorph-kvconf_rs.lo" in lines
    rule = lines.index("codecoremorph-kvconf_rs.lo: $(CODECOREMORPH_RUST_kvconf_rs)")
    assert lines[rule + 1] == (
        "\t$(CC) -r -nostdlib -o codecoremorph-kvconf_rs.o -Wl,--whole-archive "
        "$(CODECOREMORPH_RUST_kvconf_rs) -Wl,--no-whole-archive"
    )
    # libtool reads a .lo only if one of its first lines says libtool generated it.
    assert lines[rule + 2].startswith('\tprintf "# %s - a libtool object file\\n# Generated by ')
    assert "pic_object='%s'\\nnon_pic_object='%s'" in lines[rule + 2]
    assert lines[rule + 2].endswith("$@ codecoremorph-kvconf_rs.o codecoremorph-kvconf_rs.o > $@")
    staticlib = lines.index("$(CODECOREMORPH_RUST_kvconf_rs): codecoremorph-rust")
    assert lines[staticlib + 1] == "\t@:"
    assert (
        "\t$(CODECOREMORPH_CARGO) build --release --locked --offline --manifest-path "
        "$(top_srcdir)/rust/Cargo.toml" in lines
    )
    # What the file sets already, the block appends to: automake refuses a second `=`.
    again = cports.am_block(
        [KVCONF], [], ["-lm"], "rust/Cargo.toml", {"libkvconf_la_LIBADD", "CLEANFILES"}
    )
    assert "libkvconf_la_LIBADD += codecoremorph-kvconf_rs.lo $(CODECOREMORPH_RUST_LIBS)" in again
    assert "CLEANFILES += codecoremorph-kvconf_rs.o codecoremorph-kvconf_rs.lo" in again


def test_a_static_automake_library_takes_the_crate_as_an_object() -> None:
    library = cports.AmLibrary(
        name="x_rs", canonical="libx_a", libtool=False,
        staticlib="rust/target/release/libx_rs.a", archive="libx.a",
    )  # fmt: skip
    block = cports.am_block([library], [], ["-lm"], "rust/Cargo.toml", set()).splitlines()
    assert "libx_a_LIBADD = codecoremorph-x_rs.o" in block
    assert "codecoremorph-x_rs.o: $(CODECOREMORPH_RUST_x_rs)" in block
    assert not any("printf" in line for line in block)
    # It hands on no libraries, so the links that take it get Rust's.
    text = "bin_PROGRAMS = t u\nt_LDADD = libx.a\nu_LDADD = \\\n\t../other.a libx.a -lz\n"
    linked = cports.with_am_native_libs(text, {"libx.a"})
    assert linked == (
        "bin_PROGRAMS = t u\nt_LDADD = libx.a $(CODECOREMORPH_RUST_LIBS)\nu_LDADD = \\\n"
        "\t../other.a libx.a -lz $(CODECOREMORPH_RUST_LIBS)\n"
    )
    assert cports.with_am_native_libs(linked, {"libx.a"}) == linked


def test_a_program_of_a_makefile_am_copies_its_rust_bin() -> None:
    tools = (C_AUTOTOOLS / "tools" / "Makefile.am").read_text(encoding="utf-8")
    program = cports.AmProgram(
        name="kvdump", canonical="kvdump", binary="rust/target/release/kvdump"
    )
    text = cports.with_am_program(tools, "kvdump")
    assert "kvdump_SOURCES =\n" in text
    assert "kvdump.c" not in text
    assert "kvdump_LDADD = $(top_builddir)/src/libkvconf.la" in text  # the rest stays
    block = cports.am_block([], [program], [], "rust/Cargo.toml", cports.am_defined(text))
    lines = block.splitlines()
    assert "CODECOREMORPH_RUST_LIBS" not in block  # no library here
    assert "CODECOREMORPH_RUST_BIN_kvdump = $(top_srcdir)/rust/target/release/kvdump" in lines
    assert "kvdump_LINK = cp $(CODECOREMORPH_RUST_BIN_kvdump) $@ && :" in lines
    assert "EXTRA_kvdump_DEPENDENCIES = $(CODECOREMORPH_RUST_BIN_kvdump)" in lines
    binary = lines.index("$(CODECOREMORPH_RUST_BIN_kvdump): codecoremorph-rust")
    assert lines[binary + 1] == "\t@:"
    assert not any(line.startswith("kvdump_SOURCES") for line in lines)
    # A program that lists no sources would build its default one, kvdump.c.
    bare = cports.am_block([], [program], [], "rust/Cargo.toml", set()).splitlines()
    assert "kvdump_SOURCES =" in bare
    # Conditional additions go too, and the block's own lines stay.
    more = "kvdump_SOURCES = a.c \\\n\tb.c\nif X\nkvdump_SOURCES += c.c\nendif\n"
    assert cports.with_am_program(more, "kvdump") == "kvdump_SOURCES =\nif X\nendif\n"
    full = cports.with_make_block(text, block)
    assert cports.with_am_program(full, "kvdump") == full


def test_a_cmake_projects_crates_go_into_cmake(tmp_path: Path) -> None:
    """A C port of a CMake project (D-89): the crates join the top-level CMakeLists.txt
    through Corrosion, and a Makefile the project also ships stays as it is."""
    from codecoremorph.adapters.base import AdapterContext
    from codecoremorph.adapters.rust import RustAdapter
    from codecoremorph.core.models import CodemodChange, LanguageConfig
    from codecoremorph.sandbox.native import NativeExecutor

    work = tmp_path / "work"
    makefile = "all: libcjson.a\nlibcjson.a: cJSON.o\n\tar rcs $@ $^\n"
    files = {
        "CMakeLists.txt": "project(cJSON C)\r\nadd_library(cjson cJSON.c)\r\n",
        "Makefile": makefile,
        "rust/Cargo.toml": cports.workspace_manifest(["cjson_rs", "tool_rs"]),
        "rust/cjson_rs/Cargo.toml": cports.package_manifest(
            "cjson_rs", "2021", "cjson", "libcjson.a", ["cJSON.h"]
        ),
        "rust/tool_rs/Cargo.toml": cports.bin_manifest("tool_rs", "2021", "tool", "tool", []),
    }
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode("utf-8"))
    lang = LanguageConfig(
        language="rust", standard="rust2021", port_source="c", port_build_system="cmake"
    )
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=lang,
    )
    adapter = RustAdapter()
    changes: list[CodemodChange] = []
    problems = adapter._make_integration(ctx, adapter._writer(ctx, changes))  # pyright: ignore[reportPrivateUsage]
    assert [c.file for c in changes] == ["CMakeLists.txt"]
    top = (work / "CMakeLists.txt").read_bytes().decode("utf-8")
    assert top.startswith("project(cJSON C)\r\nadd_library(cjson cJSON.c)\r\n")  # CRLF kept
    assert "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)\r\n" in top
    assert "set_property(TARGET cjson APPEND PROPERTY LINK_LIBRARIES cjson_rs)\r\n" in top
    assert (work / "Makefile").read_text(encoding="utf-8") == makefile
    assert problems == []  # the program is imported too, under its executable's name


def test_what_names_a_deleted_source() -> None:
    sources = ["src/wf.c", "cJSON.c"]
    assert cports.names_sources("OBJS = wf.o\n", sources) == ["src/wf.c"]
    assert cports.names_sources("tool: ../cJSON.c\n", sources) == ["cJSON.c"]
    assert cports.names_sources("src\\wf.obj libwf.lo\n", sources) == ["src/wf.c"]
    assert cports.names_sources("SRC = my_wf.c wf.cpp cJSON.cc\n", sources) == []
    # A C++ port's sources (D-89): by their own extension, or their objects.
    cpp = ["src/ring.cpp", "lib/x.cc"]
    assert cports.names_sources('Include="..\\src\\ring.cpp"', cpp) == ["src/ring.cpp"]
    assert cports.names_sources("OBJS = x.o\n", cpp) == ["lib/x.cc"]
    assert cports.names_sources("SRC = ring.c ring.cppm x.cc++\n", cpp) == []
    assert cports.builds_c("script:\n  - g++ -c a.cpp\n")
    assert cports.builds_c("- run: MSBuild calc.sln\n")
    assert not cports.builds_c("# Make sure the docs build\n- run: sphinx-build\n")
    assert leftovers.is_build_file("Makefile-libbz2_so")
    assert leftovers.is_build_file("win/makefile.msc")
    assert leftovers.is_build_file("fuzzing/CMakeLists.txt")
    assert not leftovers.is_build_file("library_config/libcjson.pc.in")
    assert leftovers.is_ci_file(".github/workflows/CI.yml")
    assert leftovers.is_ci_file("appveyor.yml")
    assert not leftovers.is_ci_file("docs/ci.yml")
    assert cports.builds_c("script:\n  - cmake ..\n")
    assert not cports.builds_c("script:\n  - cargo test\n")
