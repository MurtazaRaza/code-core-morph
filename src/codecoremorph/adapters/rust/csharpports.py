"""The Rust side of a C# → Rust port (SPEC §11.10, D-84): what the Rust adapter, as the port's
target, writes into the project, and the C# adapter, as its source, reads back.

The Rust goes under ``rust/``, a Cargo workspace of CodeCoreMorph's ``codecoremorph_runtime``
(.NET's decimal and exceptions, kept after the retirement) and one crate ``<name>_rs`` for the
C# library: a ``cdylib`` the C# façades load through P/Invoke while the port lasts, and an
``rlib`` for Rust code and tests. Its ``bridge`` module (one file per unit, ``src/bridge/``) is
the C ABI of the C# side's ``codecoremorph_bridge.h`` (D-78), so the façades are the ones a port
to C++ writes; cbindgen writes its header (``include/<crate>/bridge.h``) at configure, and the
C# declarations come from it. The bridge holds the crate's only unsafe code.

A program's code is a function of the library (``src/<stem>_main.rs``), which the C# program
calls through the bridge until the retirement and a Cargo program calls
(``src/bin/<stem>.rs``). The translated tests are the crate's integration tests
(``tests/<file>.rs``). The retirement deletes the C#, its build, the façades' support, the
bridge, its headers and the ``cdylib``; with ``keep_source_api`` only the programs and the tests
that need them, the library staying a .NET library over the Rust.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from importlib import resources
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.csharp import ports as csharp
from codecoremorph.adapters.rust import ports
from codecoremorph.adapters.rust.cargo import TARGET_DIR
from codecoremorph.core.errors import ConfigError

WORKSPACE_DIR = ports.WORKSPACE_DIR
WORKSPACE = f"{WORKSPACE_DIR}/Cargo.toml"
RUNTIME_CRATE = "codecoremorph_runtime"
SYMBOL_MAP = ports.SYMBOL_MAP
TEST_MAP = ports.TEST_MAP
TOP_MARK = "# Written by CodeCoreMorph for a C# → Rust port (D-84)"
SCAFFOLDS = frozenset(
    {"rust-csharp-type", "rust-csharp-program", "rust-csharp-tests", "rust-csharp-retire"}
)
EDITIONS = ("2018", "2021", "2024")
# The runtime crate's files, as shipped (csharp_runtime/codecoremorph_runtime).
RUNTIME_FILES = ("Cargo.toml", "src/lib.rs", "src/decimal.rs", "src/exceptions.rs")
BRIDGE_RUNTIME = "ccm.rs"
_PORT = "C# → Rust"


def crate_name(name: str) -> str:
    """The crate of the C# library whose stem is ``name`` (``shop`` gives ``shop_rs``)."""
    return f"{name}_rs"


def crate_dir(name: str) -> str:
    return f"{WORKSPACE_DIR}/{crate_name(name)}"


def unit_name(type_name: str, taken: Collection[str]) -> str:
    """The stem of a type unit's Rust module and bridge file, from its first type's name, never
    a Rust keyword nor a name the crate's layout takes (``lib``, ``bridge``)."""
    return csharp.unit_name(type_name, taken, reserved=ports.RUST_KEYWORDS | {"lib", "bridge"})


def edition(standard: str) -> str:
    """``rust2021`` gives ``2021``."""
    year = standard.removeprefix("rust")
    if year not in EDITIONS:
        raise ConfigError(
            f"a C# → Rust port targets Rust 2018, 2021 or 2024, not {standard}",
            hint="Set migration.target.standard to rust2018, rust2021 or rust2024.",
        )
    return year


def runtime_text(rel: str) -> str:
    """A file of the Rust runtime (``codecoremorph_runtime/...`` or ``ccm.rs``), as shipped."""
    package = resources.files("codecoremorph.adapters.rust.csharp_runtime")
    node = package
    for part in rel.split("/"):
        node = node / part
    return node.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the files


def workspace_manifest(project: str, members: Collection[str]) -> str:
    listed = ", ".join(f'"{m}"' for m in sorted(members))
    return (
        f"{TOP_MARK}: the Rust that replaces\n"
        f"# the C# library {project}, and CodeCoreMorph's runtime of .NET's decimal and\n"
        "# exceptions. The port adds a member per C# project with ported types.\n"
        "[workspace]\n"
        f"members = [{listed}]\n"
        'resolver = "2"\n'
    )


def crate_manifest(name: str, project: str, edition_year: str) -> str:
    return (
        "[package]\n"
        f'name = "{crate_name(name)}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "[lib]\n"
        "# cdylib: the C# façades load it until the retirement; rlib: Rust code and tests use it.\n"
        'crate-type = ["cdylib", "rlib"]\n\n'
        "[dependencies]\n"
        f'{RUNTIME_CRATE} = {{ path = "../{RUNTIME_CRATE}" }}\n\n'
        "[package.metadata.codecoremorph]\n"
        f'csharp-project = "{project}"\n'
    )


# What cbindgen leaves to codecoremorph_bridge.h, which its header includes: the bridge's own C
# types and functions, and the C library functions the runtime imports.
CBINDGEN_EXCLUDED = (
    "ccm_status",
    "ccm_decimal",
    "ccm_release_fn",
    "ccm_error_part",
    "ccm_free",
    "ccm_strdup",
    "CCM_OK",
    "CCM_ERROR",
    "CCM_MANAGED",
    "malloc",
    "free",
)


def cbindgen_toml(name: str) -> str:
    crate = crate_name(name)
    excluded = ", ".join(f'"{e}"' for e in CBINDGEN_EXCLUDED)
    return (
        f"# cbindgen writes the bridge's C header, include/{crate}/bridge.h, from the crate's\n"
        '# extern "C" functions at each configure (CodeCoreMorph, D-84); the C# declarations of\n'
        "# the façades come from it. The bridge's own C types are codecoremorph_bridge.h's.\n"
        'language = "C"\n'
        'style = "type"\n'
        f'include_guard = "{crate.upper()}_BRIDGE_H"\n'
        'autogen_warning = "/* Written by cbindgen from src/bridge at each configure; do not '
        'edit. */"\n'
        f'includes = ["{csharp.BRIDGE_RUNTIME_HEADER}"]\n'
        "usize_is_size_t = true\n\n"
        "[export]\n"
        "# The exports, their handles and function pointer types: not the crate's constants.\n"
        'item_types = ["functions", "typedefs", "opaque"]\n'
        f"exclude = [{excluded}]\n\n"
        "[parse]\n"
        "parse_deps = false\n"
    )


def lib_rs(name: str, project: str) -> str:
    crate = crate_name(name)
    return (
        f"//! The C# library `{project}`, ported to Rust (CodeCoreMorph, D-84). Its C# façades\n"
        f"//! reach it only through `bridge` (the C header `include/{crate}/bridge.h`), the one\n"
        "//! module that may use `unsafe`.\n"
        "#![deny(unsafe_code)]\n\n"
        "#[allow(unsafe_code)]\n"
        f"mod {ports.BRIDGE_MODULE};\n"
    )


def bridge_mod_rs(name: str) -> str:
    crate = crate_name(name)
    return (
        "//! The C ABI the C# façades call (CodeCoreMorph, D-84): `#[unsafe(no_mangle)]`\n"
        '//! `extern "C"` functions, one module per unit, each body in `ccm::guard`, so no panic\n'
        f"//! unwinds into .NET. cbindgen writes them to `include/{crate}/bridge.h`, and the C#\n"
        "//! declarations come from it. The crate's only unsafe code; the retirement deletes it.\n"
        "#![allow(non_snake_case, non_camel_case_types)]\n\n"
        "pub mod ccm;\n"
    )


def module_stub(types: str) -> str:
    return f"//! The Rust of {types}, ported from C# (CodeCoreMorph, D-84).\n"


def bridge_stub(types: str) -> str:
    return f"//! The bridge of {types}: the exports its C# façade calls (CodeCoreMorph, D-84).\n"


def program_stub(stem: str, program: str) -> str:
    return (
        f"//! The program {program}, ported from C# (CodeCoreMorph, D-84). `{stem}_main` is its\n"
        "//! `Main`, given the arguments without the program's name, returning the exit code. Its\n"
        "//! other code goes in modules from its C# namespaces.\n\n"
        f"pub fn {stem}_main(args: &[String]) -> i32 {{\n"
        "    let _ = args;\n"
        f'    todo!("port {program}")\n'
        "}\n"
    )


def program_export(name: str, stem: str) -> str:
    """The export a C# program's ``Main`` calls."""
    return f"{name}_{stem}_main"


def program_bridge(name: str, stem: str, program: str) -> str:
    export = program_export(name, stem)
    return (
        f"//! The bridge of the program {program}: the export its C# Main calls (CodeCoreMorph,\n"
        "//! D-84).\n"
        "use std::ffi::c_char;\n"
        "use std::io::Write;\n\n"
        "use super::ccm::{self, ccm_status};\n\n"
        "/// Runs the program with its arguments, its exit code in *result.\n"
        "#[unsafe(no_mangle)]\n"
        f'pub extern "C" fn {export}(\n'
        "    args_data: *const *const c_char,\n"
        "    args_count: i32,\n"
        "    result: *mut i32,\n"
        ") -> ccm_status {\n"
        "    let status = ccm::guard(|| {\n"
        '        let args: Vec<String> = ccm::strings(args_data, args_count, "args")?\n'
        "            .into_iter()\n"
        "            .map(Option::unwrap_or_default)\n"
        "            .collect();\n"
        f"        ccm::put(result, crate::{stem}_main::{stem}_main(&args));\n"
        "        Ok(())\n"
        "    });\n"
        "    // The .NET host exits without flushing Rust's buffered output.\n"
        "    let _ = std::io::stdout().flush();\n"
        "    let _ = std::io::stderr().flush();\n"
        "    status\n"
        "}\n"
    )


def program_entry(name: str, stem: str, program: str) -> str:
    crate = crate_name(name)
    return (
        f"//! The program {program} (CodeCoreMorph, D-84): `{stem}_main`, in the library, is its\n"
        "//! main.\n\n"
        "fn main() {\n"
        "    let args: Vec<String> = std::env::args().skip(1).collect();\n"
        f"    std::process::exit({crate}::{stem}_main::{stem}_main(&args));\n"
        "}\n"
    )


def tests_stub(source: str | None) -> str:
    what = source or "a C# test file (the unit's note names it)"
    return (
        f"//! The tests of {what}, translated from C# (CodeCoreMorph, D-84): a `#[test]` per C#\n"
        f"//! test case, each mapped in {TEST_MAP}.\n"
    )


SYMBOL_MAP_TEXT = """\
# The C# symbols this port moved to Rust, and the Rust that took them over (CodeCoreMorph, D-84).
# One entry per public member of a ported type:
#
#   [[symbol]]
#   csharp = "csharp:Shop.Cart.Add(Shop.Item,System.Int32)"
#   rust = "shop_rs::cart::Cart::add"      # or a list of paths
#   note = "..."                           # optional: what Rust couldn't keep, or why rust is empty
"""

TEST_MAP_TEXT = """\
# The C# tests this port translated to Rust, and the Rust tests that took them over
# (CodeCoreMorph, D-84). One entry per C# test:
#
#   [[test]]
#   csharp = "dotnet:Shop.Tests.CartTests#AddTakesFromStock"
#   rust = "nextest:shop_rs::cart_tests::add_takes_from_stock"
#   note = "..."                           # optional
"""


# --------------------------------------------------------------------------- reading back


def _read(path: Path) -> str | None:
    return path.read_bytes().decode("utf-8", errors="replace") if path.is_file() else None


def is_port(worktree: Path) -> bool:
    """Whether the project has a C# → Rust port's workspace."""
    text = _read(worktree / WORKSPACE)
    return text is not None and TOP_MARK in text


_PACKAGE_NAME = re.compile(r'(?m)^\s*name\s*=\s*"([^"]+)"')


def bridge_crates(worktree: Path) -> list[str]:
    """The port's crates with a bridge (worktree paths): those with a ``cbindgen.toml``."""
    base = worktree / WORKSPACE_DIR
    if not base.is_dir():
        return []
    return sorted(
        p.parent.relative_to(worktree).as_posix()
        for p in base.glob("*/cbindgen.toml")
        if (p.parent / "Cargo.toml").is_file()
    )


_EDITION = re.compile(r'(?m)^\s*edition\s*=\s*"(\d{4})"')


def native_crates(worktree: Path) -> list[tuple[str, list[str], str]]:
    """The port's crates whose ``native`` module calls native libraries (D-84 (9)): each
    crate's worktree path, the libraries' linker names and the crate's edition."""
    base = worktree / WORKSPACE_DIR
    if not base.is_dir():
        return []
    found: list[tuple[str, list[str], str]] = []
    for manifest in sorted(base.glob("*/Cargo.toml")):
        text = _read(manifest) or ""
        names = native_libraries(text)
        if names:
            match = _EDITION.search(text)
            year = match.group(1) if match else "2021"
            found.append((manifest.parent.relative_to(worktree).as_posix(), names, year))
    return found


def library_of(worktree: Path, crate: str) -> str:
    """A crate's library name (what the façades' ``DllImport`` loads)."""
    manifest = _read(worktree / crate / "Cargo.toml") or ""
    match = _PACKAGE_NAME.search(manifest)
    return (match.group(1) if match else PurePosixPath(crate).name).replace("-", "_")


def bridge_header(crate: str, library: str) -> str:
    """A crate's cbindgen header (worktree path)."""
    return f"{crate}/{ports.bridge_header_path(library)}"


def facade_library(worktree: Path) -> tuple[str, str] | None:
    """The crate the façades call and its library name, or None before the first unit. A
    port takes one C# library (D-78), so there is one."""
    crates = bridge_crates(worktree)
    if not crates:
        return None
    return crates[0], library_of(worktree, crates[0])


def cargo_target_dir(cache_dir: Path) -> Path:
    """Where the Rust adapter, as the port's target, has Cargo build (its own cache): the
    C# adapter's cache with the target language's directory (the session gives each target
    adapter ``cache_dir / language``)."""
    return cache_dir / "rust" / TARGET_DIR


def test_environment(lib_dir: str) -> dict[str, str]:
    """What ``dotnet test`` needs to load the ``cdylib``: its directory on the loader's path
    (no sanitizers: the bridge is the only unsafe code, D-84)."""
    return {"LD_LIBRARY_PATH": lib_dir, "DYLD_LIBRARY_PATH": lib_dir}


# --------------------------------------------------------------------------- the scaffolds


def _identifier(options: Mapping[str, str], name: str, what: str) -> str:
    return csharp.identifier_option(options, name, what, _PORT)


def _module_line(out: csharp.Writer, rel: str, module: str, *, public: bool) -> None:
    text = out.read(rel)
    if text is None:
        raise ConfigError(
            f"{rel} isn't there: the port's first unit writes it",
            hint="Plan the port's type units first (port_csharp_type_to_rust).",
        )
    out.write(rel, ports.with_module(text, module, public=public))


def _base(out: csharp.Writer, name: str, project: str, standard: str) -> str:
    """The workspace, the runtime, the crate and its bridge, written once; the crate's
    directory."""
    crate = crate_dir(name)
    library = crate_name(name)
    manifest = out.read(WORKSPACE)
    if manifest is None:
        out.write(WORKSPACE, workspace_manifest(project, [RUNTIME_CRATE, library]))
    else:
        updated = ports.with_member(ports.with_member(manifest, RUNTIME_CRATE), library)
        if updated != manifest:
            out.write(WORKSPACE, updated)
    for rel in RUNTIME_FILES:
        out.create(f"{WORKSPACE_DIR}/{RUNTIME_CRATE}/{rel}", runtime_text(f"{RUNTIME_CRATE}/{rel}"))
    out.create(f"{crate}/Cargo.toml", crate_manifest(name, project, edition(standard)))
    out.create(f"{crate}/cbindgen.toml", cbindgen_toml(name))
    out.create(f"{crate}/src/lib.rs", lib_rs(name, project))
    out.create(f"{crate}/src/bridge/mod.rs", bridge_mod_rs(name))
    out.create(f"{crate}/src/bridge/{BRIDGE_RUNTIME}", runtime_text(BRIDGE_RUNTIME))
    out.create(
        f"{crate}/include/{library}/{csharp.BRIDGE_RUNTIME_HEADER}",
        csharp.runtime_text(csharp.BRIDGE_RUNTIME_HEADER),
    )
    header = bridge_header(crate, library)
    csharp.write_facade_support(out, project, library, header, "Rust")
    out.create(SYMBOL_MAP, SYMBOL_MAP_TEXT)
    return crate


def _type(out: csharp.Writer, options: Mapping[str, str], standard: str) -> None:
    """A unit of types: the workspace, runtime and crate (the first unit), the unit's module
    and bridge file. The options are the unit's port's: ``crate`` (the stem of the C# library's
    crate), ``module`` (the unit's files' stem), ``build_target`` (the façade project's file)
    and ``types`` (the C# types it ports)."""
    name = _identifier(options, "crate", "the crate's name")
    unit = _identifier(options, "module", "the unit's name")
    project = csharp.facade_project(out.worktree, options, _PORT)
    types = csharp.type_names(options.get("types", "")) or unit
    crate = _base(out, name, project, standard)
    out.create(f"{crate}/src/{unit}.rs", module_stub(types))
    out.create(f"{crate}/src/bridge/{unit}.rs", bridge_stub(types))
    _module_line(out, f"{crate}/src/lib.rs", unit, public=True)
    _module_line(out, f"{crate}/src/bridge/mod.rs", unit, public=False)
    _native(out, crate, options.get("native", "").split(","))


def _program(out: csharp.Writer, options: Mapping[str, str], standard: str) -> None:
    """A program: its code a function of the library, ``<stem>_main``, which the C# program's
    ``Main`` calls through the bridge until the retirement, and a Cargo program that calls
    it. The options are the unit's port's: ``crate``, ``module`` (the stem), ``build_target``
    (the program's project, which gets the façades' support) and ``bin`` (its name)."""
    name = _identifier(options, "crate", "the crate's name")
    stem = _identifier(options, "module", "the program's stem")
    project = csharp.facade_project(out.worktree, options, _PORT)
    program = options.get("bin") or stem
    crate = _base(out, name, project, standard)
    out.create(f"{crate}/src/{stem}_main.rs", program_stub(stem, program))
    out.create(f"{crate}/src/bridge/{stem}.rs", program_bridge(name, stem, program))
    out.create(f"{crate}/src/bin/{stem}.rs", program_entry(name, stem, program))
    _module_line(out, f"{crate}/src/lib.rs", f"{stem}_main", public=True)
    _module_line(out, f"{crate}/src/bridge/mod.rs", stem, public=False)


def _tests(out: csharp.Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """A C# test file's Rust integration tests, ``tests/<module>.rs`` of the library's crate,
    and the test map. The Rust the tests use is there already: the type units wrote it."""
    name = _identifier(options, "crate", "the crate's name")
    module = _identifier(options, "module", "the test file's name")
    crate = crate_dir(name)
    if out.read(f"{crate}/Cargo.toml") is None:
        raise ConfigError(
            "a C# test file is translated to Rust after the types it tests are Rust, and "
            f"{crate}/Cargo.toml isn't there",
            hint="Plan the port's type units first (port_csharp_type_to_rust).",
        )
    # The C# files are the unit's read-only references, which a run doesn't pass to codemods.
    source = next((f for f in files if f.lower().endswith(".cs")), None)
    out.create(TEST_MAP, TEST_MAP_TEXT)
    out.create(f"{crate}/tests/{module}.rs", tests_stub(source))


# --------------------------------------------------------------------------- native libraries

NATIVE_DIR = "src/native"
_NATIVE_LIST = re.compile(r"(?m)^[ \t]*native[ \t]*=[ \t]*\[([^\]]*)\][ \t]*$")


def native_link_name(library: str) -> str:
    """The linker's name of a library a C# `DllImport` names: ``libsodium`` and
    ``libsodium.so.23`` are ``sodium``, ``kernel32.dll`` is ``kernel32``."""
    stem = library.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = re.sub(r"\.(?:dll|dylib|so)(?:\.\d+)*$", "", stem)
    stem = stem.removeprefix("lib") or stem
    return re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "native"


def native_libraries(manifest: str) -> list[str]:
    """The native libraries a crate's manifest says its ``native`` module calls."""
    match = _NATIVE_LIST.search(manifest)
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def with_native_libraries(manifest: str, names: Collection[str]) -> str:
    """The manifest with ``names`` among its native libraries (sorted, once)."""
    current = native_libraries(manifest)
    merged = sorted({*current, *names})
    if merged == current:
        return manifest
    line = "native = [" + ", ".join(f'"{n}"' for n in merged) + "]"
    if _NATIVE_LIST.search(manifest):
        return _NATIVE_LIST.sub(lambda _m: line, manifest, count=1)
    header = _METADATA_HEADER.search(manifest)
    if header is not None:
        return manifest[: header.end()] + line + "\n" + manifest[header.end() :]
    return manifest.rstrip("\n") + "\n\n[package.metadata.codecoremorph]\n" + line + "\n"


_METADATA_HEADER = re.compile(r"(?m)^\[package\.metadata\.codecoremorph\][ \t]*\r?\n")


NATIVE_MOD_DOC = (
    "//! The native libraries the C# called through P/Invoke, which the Rust calls itself\n"
    "//! (CodeCoreMorph, D-84). Each `<library>_sys` holds bindgen's declarations from the\n"
    "//! library's C header, written at each configure: never edit it. The safe functions the\n"
    "//! rest of the crate calls go here, each `unsafe` block with a `// SAFETY:` comment.\n"
    "//! Besides the bridge, the one module that may hold unsafe code; after the retirement, the\n"
    "//! only one.\n"
)


def with_native_module(text: str, name: str) -> str:
    """``src/native/mod.rs`` declaring the library's ``<name>_sys`` module and linking it, once.
    The module ``include!``s bindgen's file rather than being a module file: cbindgen, which
    writes the bridge's header from the crate's source, expands no macro, so it doesn't take
    the library's declarations for the bridge's (the M12 benchmark's first unit calling
    libsodium found it copying them all into the header)."""
    if re.search(rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+{re.escape(name)}_sys\b", text):
        return text
    return (
        text.rstrip("\n")
        + "\n\n"
        + f"/// bindgen's declarations of {name} (`{name}_sys.rs`), included rather than a module\n"
        + "/// file, so cbindgen, which expands no macro, leaves them out of the bridge's header.\n"
        + "///\n"
        + "/// cbindgen:ignore\n"
        + "#[allow(non_upper_case_globals, non_camel_case_types, non_snake_case, dead_code)]\n"
        + "#[allow(clippy::all)]\n"
        + f"pub mod {name}_sys {{\n"
        + f'    include!("{name}_sys.rs");\n'
        + "}\n\n"
        + f'#[link(name = "{name}")]\n'
        + 'unsafe extern "C" {}\n'
    )


def native_sys_stub(name: str) -> str:
    return f"//! Written by bindgen from the C header of {name} at each configure; do not edit.\n"


def native_wrapper(name: str) -> str:
    """The header bindgen reads for a native library: its own, from the system's include path."""
    return f"#include <{name}.h>\n"


_EDITION_RUST = {"2024": "1.85"}  # the first Rust of an edition newer than bindgen's default


def native_bindgen_argv(wrapper: str, out: str, name: str, edition_year: str) -> list[str]:
    """bindgen over a native library's header: what the library's own headers declare (its
    ``<name>.h`` and a ``<name>/`` directory beside it), for the crate's edition (and for 2024,
    the first Rust that has it, which bindgen's default target predates)."""
    target = ["--rust-target", _EDITION_RUST[edition_year]] if edition_year in _EDITION_RUST else []
    return [
        "bindgen",
        wrapper,
        "--output",
        out,
        "--allowlist-file",
        rf".*/{re.escape(name)}(\.h|/.*)",
        "--no-doc-comments",
        "--merge-extern-blocks",
        "--sort-semantically",
        *target,
        "--rust-edition",
        edition_year,
        "--",
        "-x",
        "c",
        "-std=c11",
    ]


_NATIVE_DECL = "#[allow(unsafe_code)]\nmod native;\n"


def _native(out: csharp.Writer, crate: str, libraries: Sequence[str]) -> None:
    """A unit whose types call native libraries (D-82): the crate's ``native`` module, a
    ``<name>_sys`` module per library that bindgen writes at configure, the libraries in the
    manifest, and the module in the crate root, the one besides the bridge that may hold
    unsafe code."""
    names = sorted({native_link_name(lib) for lib in libraries if lib.strip()})
    if not names:
        return
    manifest = out.read(f"{crate}/Cargo.toml") or ""
    updated = with_native_libraries(manifest, names)
    if updated != manifest:
        out.write(f"{crate}/Cargo.toml", updated)
    module = out.read(f"{crate}/{NATIVE_DIR}/mod.rs") or NATIVE_MOD_DOC
    for name in names:
        module = with_native_module(module, name)
        out.create(f"{crate}/{NATIVE_DIR}/{name}_sys.rs", native_sys_stub(name))
    out.write(f"{crate}/{NATIVE_DIR}/mod.rs", module)
    lib = out.read(f"{crate}/src/lib.rs") or ""
    if not re.search(r"(?m)^\s*mod\s+native\s*;", lib):
        bridge = "#[allow(unsafe_code)]\nmod bridge;"
        lib = (
            lib.replace(bridge, _NATIVE_DECL + bridge, 1)
            if bridge in lib
            else lib.rstrip("\n") + "\n\n" + _NATIVE_DECL
        )
        out.write(f"{crate}/src/lib.rs", lib)


_CDYLIB = re.compile(
    r'(?ms)^\[lib\]\s*\n(?:#[^\n]*\n)*crate-type\s*=\s*\["cdylib",\s*"rlib"\]\s*\n\n?'
)


_METADATA = re.compile(r"(?ms)\n*^\[package\.metadata\.codecoremorph\]\s*\n(?:[^\[\n][^\n]*\n?)*")
_PROJECT = re.compile(r'(?m)^\s*csharp-project\s*=\s*"([^"]+)"')


def csharp_project(manifest: str) -> str | None:
    """The C# project a crate's manifest says it ports."""
    match = _PROJECT.search(manifest)
    return match.group(1) if match else None


def retired_manifest(manifest: str) -> str:
    """A crate's manifest once no C# calls it: no ``cdylib`` (Cargo's default ``rlib``), and
    no C# project."""
    out = _METADATA.sub("\n", _CDYLIB.sub("", manifest))
    return out.rstrip("\n") + "\n"


_FORBID = re.compile(r"(?m)^([ \t]*)#!\[\s*forbid\s*\(\s*unsafe_code\s*\)\s*\]")


def retired_lib_rs(text: str, name: str, project: str | None, *, native: bool = False) -> str:
    """A crate root once no C# calls it: no ``mod bridge``, ``#![forbid(unsafe_code)]``, and
    the scaffold's doc, if it is still the scaffold's, without the bridge. A crate whose Rust
    calls native libraries (``native``) keeps its ``mod native``, the one module that may hold
    unsafe code, so its root still denies it (D-84 (9))."""
    out = ports.retired_lib_rs(text, crate_name(name), None)
    if native:
        out = _FORBID.sub(r"\1#![deny(unsafe_code)]", out)
    if project is not None:
        doc = lib_rs(name, project).split("#![deny", 1)[0]
        if out.startswith(doc):
            line = f"//! The C# library `{project}`, ported to Rust (CodeCoreMorph, D-84).\n"
            if native:
                line += (
                    "//! `native` calls the native libraries the C# called, the one module that "
                    "may use\n//! `unsafe`.\n"
                )
            out = line + out[len(doc) :]
    return out


def _retire(out: csharp.Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """Delete the C#: the unit's C# files and projects, its solutions and MSBuild files and the
    façades' support; then each crate's bridge, header, cbindgen file and ``cdylib``, its root
    forbidding unsafe code (denying it, where a ``native`` module stays; its ``<library>_sys``
    modules stay as bindgen last wrote them, the manifest no longer listing the libraries).
    With ``keep`` (``keep_source_api``) the unit names only the programs and the tests that
    need them, and the Rust keeps its bridge for the façades."""
    keep = options.get("keep") == "true"
    csharp.retire_csharp(out, files, keep=keep)
    if not keep:
        for crate in bridge_crates(out.worktree):
            library = library_of(out.worktree, crate)
            out.delete_tree(f"{crate}/src/bridge")
            out.delete_tree(f"{crate}/include")
            out.delete(f"{crate}/cbindgen.toml")
            manifest = out.read(f"{crate}/Cargo.toml") or ""
            lib = out.read(f"{crate}/src/lib.rs")
            if lib is not None:
                name = library.removesuffix("_rs")
                native = out.read(f"{crate}/{NATIVE_DIR}/mod.rs") is not None
                out.write(
                    f"{crate}/src/lib.rs",
                    retired_lib_rs(lib, name, csharp_project(manifest), native=native),
                )
            if manifest:
                out.write(f"{crate}/Cargo.toml", retired_manifest(manifest))
    out.prune()


def scaffold(
    worktree: Path, tool: str, options: Mapping[str, str], files: Sequence[str], standard: str
) -> list[str]:
    """Run one of the C# → Rust scaffolds; the worktree paths it changed."""
    out = csharp.Writer(worktree)
    if tool == "rust-csharp-type":
        _type(out, options, standard)
    elif tool == "rust-csharp-program":
        _program(out, options, standard)
    elif tool == "rust-csharp-tests":
        _tests(out, options, files)
    elif tool == "rust-csharp-retire":
        _retire(out, options, files)
    else:
        raise ConfigError(f"no C# → Rust scaffold {tool!r}", hint=f"Available: {sorted(SCAFFOLDS)}")
    return out.changed
