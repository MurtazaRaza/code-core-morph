"""The Rust side of a Java → Rust port (SPEC §11.12, D-92): what the Rust adapter, as the port's
target, writes into the project, and the Java adapter, as its source, reads back.

The layout is a C# → Rust port's (:mod:`codecoremorph.adapters.rust.csharpports`): a Cargo
workspace under ``rust/`` of CodeCoreMorph's runtime and one crate ``<name>_rs`` for the Java
library, a ``cdylib`` the Java façades load through JNI while the port lasts and an ``rlib`` for
Rust code and tests. Its ``bridge`` module (one file per unit) is the C ABI of the Java side's
``codecoremorph_bridge.h``; cbindgen writes its header at configure, and the Java declarations
and the Rust JNI glue come from it. What differs is the runtime: ``codecoremorph_java`` (Java's
exceptions and semantics, kept after the retirement), the bridge's ``ccm.rs`` over it, and the
JNI glue the bridge ``include!``s (``jni.rs`` as shipped, ``jni_sys.rs`` from bindgen over the
JDK's ``jni.h`` and ``jni_g.rs`` from the bridge's header, both written at configure).

The translated tests are the crate's integration tests (``tests/<file>.rs``). The retirement
deletes the Java, its build, the façades' support, and the bridge with its header and the
``cdylib``, forbidding unsafe code.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from importlib import resources
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.csharp.ports import Writer, identifier_option
from codecoremorph.adapters.java import facades
from codecoremorph.adapters.java.jni import Bridge
from codecoremorph.adapters.rust import csharpports as shared
from codecoremorph.adapters.rust import ports
from codecoremorph.adapters.rust.jniglue import FIXED_FILE, GENERATED_FILE, SYS_FILE, rust_glue
from codecoremorph.core.errors import ConfigError

WORKSPACE_DIR = ports.WORKSPACE_DIR
WORKSPACE = f"{WORKSPACE_DIR}/Cargo.toml"
RUNTIME_CRATE = "codecoremorph_java"
SYMBOL_MAP = ports.SYMBOL_MAP
TEST_MAP = ports.TEST_MAP
TOP_MARK = "# Written by CodeCoreMorph for a Java → Rust port (D-92)"
SCAFFOLDS = frozenset({"rust-java-type", "rust-java-tests", "rust-java-retire"})
RUNTIME_FILES = ("Cargo.toml", "src/lib.rs", "src/exception.rs", "src/lang.rs")
BRIDGE_RUNTIME = "ccm.rs"
_PORT = "Java → Rust"

crate_name = shared.crate_name
crate_dir = shared.crate_dir
edition = shared.edition
bridge_crates = shared.bridge_crates
library_of = shared.library_of
bridge_header = shared.bridge_header
facade_library = shared.facade_library
cargo_target_dir = shared.cargo_target_dir
test_environment = shared.test_environment
retired_manifest = shared.retired_manifest


def unit_name(type_name: str, taken: Collection[str]) -> str:
    """The stem of a type unit's Rust module and bridge file, from its first type's name, never
    a Rust keyword nor a name the crate's layout takes."""
    return facades.unit_name(type_name, taken, reserved=ports.RUST_KEYWORDS | {"lib", "bridge"})


def runtime_text(rel: str) -> str:
    """A file of the Rust runtime (``codecoremorph_java/...``, ``ccm.rs``, ``jni.rs``)."""
    node = resources.files("codecoremorph.adapters.rust.java_runtime")
    for part in rel.split("/"):
        node = node / part
    return node.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the files


def workspace_manifest(module: str, members: Sequence[str]) -> str:
    listed = ", ".join(f'"{m}"' for m in sorted(members))
    return (
        f"{TOP_MARK}: the Rust that replaces\n"
        f"# the Java library {module}, and CodeCoreMorph's runtime of Java's exceptions and\n"
        "# semantics.\n"
        "[workspace]\n"
        f"members = [{listed}]\n"
        'resolver = "2"\n'
    )


def crate_manifest(name: str, module: str, edition_year: str) -> str:
    return (
        "[package]\n"
        f'name = "{crate_name(name)}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "[lib]\n"
        "# cdylib: the Java façades load it until the retirement; rlib: Rust code and\n"
        "# tests use it.\n"
        'crate-type = ["cdylib", "rlib"]\n\n'
        "[dependencies]\n"
        f'{RUNTIME_CRATE} = {{ path = "../{RUNTIME_CRATE}" }}\n\n'
        "[package.metadata.codecoremorph]\n"
        f'java-module = "{module}"\n'
    )


CBINDGEN_EXCLUDED = (
    "ccm_status",
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
        '# extern "C" functions at each configure (CodeCoreMorph, D-92); the Java declarations\n'
        "# and the JNI glue come from it. The bridge's own C types are codecoremorph_bridge.h's.\n"
        'language = "C"\n'
        'style = "type"\n'
        f'include_guard = "{crate.upper()}_BRIDGE_H"\n'
        'autogen_warning = "/* Written by cbindgen from src/bridge at each configure; do not '
        'edit. */"\n'
        f'includes = ["{facades.BRIDGE_RUNTIME_HEADER}"]\n'
        "usize_is_size_t = true\n\n"
        "[export]\n"
        'item_types = ["functions", "typedefs", "opaque"]\n'
        f"exclude = [{excluded}]\n\n"
        "[parse]\n"
        "parse_deps = false\n"
    )


def lib_rs(name: str, module: str) -> str:
    crate = crate_name(name)
    return (
        f"//! The Java library `{module}`, ported to Rust (CodeCoreMorph, D-92). Its Java façades\n"
        f"//! reach it only through `bridge` (the C header `include/{crate}/bridge.h` and its\n"
        "//! JNI glue), the one module that may use `unsafe`.\n"
        "#![deny(unsafe_code)]\n\n"
        "#[allow(unsafe_code)]\n"
        f"mod {ports.BRIDGE_MODULE};\n"
    )


def bridge_mod_rs(name: str) -> str:
    crate = crate_name(name)
    return (
        "//! The C ABI the Java façades call through JNI (CodeCoreMorph, D-92):\n"
        '//! `#[unsafe(no_mangle)] extern "C"` functions, one module per unit, each body in\n'
        f"//! `ccm::guard`, so no panic unwinds into the JVM. cbindgen writes them to\n"
        f"//! `include/{crate}/bridge.h`; the Java declarations and the JNI glue (`jni_g.rs`)\n"
        "//! come from it. The crate's only unsafe code; the retirement deletes it.\n"
        "#![allow(non_snake_case, non_camel_case_types)]\n\n"
        "pub mod ccm;\n\n"
        "/// bindgen's JNI types, from the JDK's `jni.h` at configure: never edit them.\n"
        "///\n"
        "/// cbindgen:ignore\n"
        "#[allow(non_upper_case_globals, non_camel_case_types, non_snake_case, dead_code)]\n"
        "#[allow(clippy::all)]\n"
        "pub mod jni_sys {\n"
        f'    include!("{SYS_FILE}");\n'
        "}\n\n"
        "/// The JNI glue's fixed part, as CodeCoreMorph ships it.\n"
        "///\n"
        "/// cbindgen:ignore\n"
        "#[allow(non_camel_case_types, dead_code, clippy::all)]\n"
        "pub mod jni {\n"
        f'    include!("{FIXED_FILE}");\n'
        "}\n\n"
        "/// The generated JNI glue: a JNI function per native method of CcmNative, written from\n"
        "/// the bridge's header at configure: never edit it.\n"
        "///\n"
        "/// cbindgen:ignore\n"
        "#[allow(non_snake_case, non_upper_case_globals, unused_mut, unused_unsafe)]\n"
        "#[allow(unused_imports)]\n"
        "#[allow(clippy::all)]\n"
        "mod jni_g {\n"
        f'    include!("{GENERATED_FILE}");\n'
        "}\n"
    )


def module_stub(types: str) -> str:
    return f"//! The Rust of {types}, ported from Java (CodeCoreMorph, D-92).\n"


def bridge_stub(types: str) -> str:
    return f"//! The bridge of {types}: the exports its Java façade calls (CodeCoreMorph, D-92).\n"


def tests_stub(source: str | None) -> str:
    what = source or "a Java test file (the unit's note names it)"
    return (
        f"//! The tests of {what}, translated from Java (CodeCoreMorph, D-92): a `#[test]` per\n"
        f"//! Java test case, each mapped in {TEST_MAP}.\n"
    )


SYS_STUB = (
    "// bindgen's JNI types, written from the JDK's jni.h when the Rust is configured\n"
    "// (CodeCoreMorph, D-92); do not edit.\n"
)

SYMBOL_MAP_TEXT = """\
# The Java symbols this port moved to Rust, and the Rust that took them over (CodeCoreMorph, D-92).
# One entry per public member of a ported type:
#
#   [[symbol]]
#   java = "java:shop.Cart#add(java.lang.String,int)"
#   rust = "shop_rs::cart::Cart::add"      # or a list of paths
#   note = "..."                           # optional: what Rust couldn't keep, or why rust is empty
"""

TEST_MAP_TEXT = """\
# The Java tests this port translated to Rust, and the Rust tests that took them over
# (CodeCoreMorph, D-92). One entry per Java test:
#
#   [[test]]
#   java = "junit:shop.CartTest#totalsItsItems"
#   rust = "nextest:shop_rs::cart_test::totals_its_items"
#   note = "..."                           # optional
"""


# --------------------------------------------------------------------------- reading back


def is_port(worktree: Path) -> bool:
    """Whether the project has a Java → Rust port's workspace."""
    path = worktree / WORKSPACE
    if not path.is_file():
        return False
    return TOP_MARK in path.read_bytes().decode("utf-8", errors="replace")


def bridge_dir(crate: str) -> str:
    return f"{crate}/src/bridge"


def glue_path(crate: str) -> str:
    return f"{bridge_dir(crate)}/{GENERATED_FILE}"


def sys_path(crate: str) -> str:
    return f"{bridge_dir(crate)}/{SYS_FILE}"


def glue_text(bridge: Bridge) -> str:
    return rust_glue(bridge)


def jni_bindgen_argv(jni_h: str, include: str, platform_include: str, out: str) -> list[str]:
    """bindgen over the JDK's ``jni.h``: the JNI types the glue uses."""
    return [
        "bindgen",
        jni_h,
        "--use-core",
        "--no-layout-tests",
        "--no-doc-comments",
        "--allowlist-type",
        "JNIEnv|JavaVM|j.*|JNINativeInterface_|JNIInvokeInterface_",
        "--allowlist-var",
        "JNI_.*",
        "-o",
        out,
        "--",
        f"-I{include}",
        f"-I{platform_include}",
    ]


# --------------------------------------------------------------------------- the scaffolds


def _identifier(options: Mapping[str, str], name: str, what: str) -> str:
    return identifier_option(options, name, what, _PORT)


def _module_line(out: Writer, rel: str, module: str, *, public: bool) -> None:
    text = out.read(rel)
    if text is None:
        raise ConfigError(
            f"{rel} isn't there: the port's first unit writes it",
            hint="Plan the port's type units first (port_java_type_to_rust).",
        )
    out.write(rel, ports.with_module(text, module, public=public))


def _base(out: Writer, name: str, module: str, standard: str) -> str:
    """The workspace, the runtime, the crate, its bridge and the Java support, written once;
    the crate's directory."""
    crate = crate_dir(name)
    library = crate_name(name)
    manifest = out.read(WORKSPACE)
    if manifest is None:
        out.write(WORKSPACE, workspace_manifest(module, [RUNTIME_CRATE, library]))
    else:
        updated = ports.with_member(ports.with_member(manifest, RUNTIME_CRATE), library)
        if updated != manifest:
            out.write(WORKSPACE, updated)
    for rel in RUNTIME_FILES:
        out.create(f"{WORKSPACE_DIR}/{RUNTIME_CRATE}/{rel}", runtime_text(f"{RUNTIME_CRATE}/{rel}"))
    out.create(f"{crate}/Cargo.toml", crate_manifest(name, module, edition(standard)))
    out.create(f"{crate}/cbindgen.toml", cbindgen_toml(name))
    out.create(f"{crate}/src/lib.rs", lib_rs(name, module))
    out.create(f"{crate}/src/bridge/mod.rs", bridge_mod_rs(name))
    out.create(f"{crate}/src/bridge/{BRIDGE_RUNTIME}", runtime_text(BRIDGE_RUNTIME))
    out.write(f"{crate}/src/bridge/{FIXED_FILE}", runtime_text(FIXED_FILE))
    out.create(sys_path(crate), SYS_STUB)
    out.create(glue_path(crate), glue_text(Bridge()))
    out.create(
        f"{crate}/include/{library}/{facades.BRIDGE_RUNTIME_HEADER}",
        facades.runtime_text(facades.BRIDGE_RUNTIME_HEADER),
    )
    facades.write_facade_support(out, module, library)
    out.create(SYMBOL_MAP, SYMBOL_MAP_TEXT)
    return crate


def _type(out: Writer, options: Mapping[str, str], standard: str) -> None:
    """A unit of types: the workspace, runtime, crate and Java support (the first unit), the
    unit's module and bridge file. The options are the unit's port's: ``crate`` (the stem of
    the Java library's crate), ``module`` (the unit's files' stem), ``build_target`` (the façade
    module's POM) and ``types`` (the Java types it ports)."""
    name = _identifier(options, "crate", "the crate's name")
    unit = _identifier(options, "module", "the unit's name")
    module = facades.module_option(out.worktree, options, _PORT)
    types = facades.type_names(options.get("types", "")) or unit
    crate = _base(out, name, module, standard)
    out.create(f"{crate}/src/{unit}.rs", module_stub(types))
    out.create(f"{crate}/src/bridge/{unit}.rs", bridge_stub(types))
    _module_line(out, f"{crate}/src/lib.rs", unit, public=True)
    _module_line(out, f"{crate}/src/bridge/mod.rs", unit, public=False)


def _tests(out: Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """A Java test file's Rust integration tests, ``tests/<module>.rs`` of the library's crate,
    and the test map. The Rust the tests use is there already: the type units wrote it."""
    name = _identifier(options, "crate", "the crate's name")
    module = _identifier(options, "module", "the test file's name")
    crate = crate_dir(name)
    if out.read(f"{crate}/Cargo.toml") is None:
        raise ConfigError(
            "a Java test file is translated to Rust after the types it tests are Rust, and "
            f"{crate}/Cargo.toml isn't there",
            hint="Plan the port's type units first (port_java_type_to_rust).",
        )
    source = next((f for f in files if f.endswith(".java")), None)
    out.create(TEST_MAP, TEST_MAP_TEXT)
    out.create(f"{crate}/tests/{module}.rs", tests_stub(source))


_JAVA_MODULE = re.compile(r'(?m)^\s*java-module\s*=\s*"([^"]+)"')


def retired_lib_rs(text: str, crate: str, manifest: str) -> str:
    """A crate root once no Java calls it: no ``mod bridge``, ``#![forbid(unsafe_code)]``, and
    the scaffold's doc, if it is still the scaffold's, without the bridge."""
    out = ports.retired_lib_rs(text, crate, None)
    match = _JAVA_MODULE.search(manifest)
    if match is not None:
        name = PurePosixPath(crate).name.removesuffix("_rs")
        doc = lib_rs(name, match.group(1)).split("#![deny", 1)[0]
        if out.startswith(doc):
            module = match.group(1)
            line = f"//! The Java library `{module}`, ported to Rust (CodeCoreMorph, D-92).\n"
            out = line + out[len(doc) :]
    return out


def _retire(out: Writer, files: Sequence[str]) -> None:
    """Delete the Java (its files, POMs, Maven's wrapper and the façades' support), then each
    crate's bridge, header, cbindgen file and ``cdylib``, its root forbidding unsafe code."""
    facades.retire_java(out, files)
    for crate in bridge_crates(out.worktree):
        out.delete_tree(f"{crate}/src/bridge")
        out.delete_tree(f"{crate}/include")
        out.delete(f"{crate}/cbindgen.toml")
        manifest = out.read(f"{crate}/Cargo.toml")
        lib = out.read(f"{crate}/src/lib.rs")
        if lib is not None:
            out.write(f"{crate}/src/lib.rs", retired_lib_rs(lib, crate, manifest or ""))
        if manifest:
            out.write(f"{crate}/Cargo.toml", retired_manifest(manifest))
    out.prune()


def scaffold(
    worktree: Path, tool: str, options: Mapping[str, str], files: Sequence[str], standard: str
) -> list[str]:
    """Run one of the Java → Rust scaffolds; the worktree paths it changed."""
    out = Writer(worktree)
    if tool == "rust-java-type":
        _type(out, options, standard)
    elif tool == "rust-java-tests":
        _tests(out, options, files)
    elif tool == "rust-java-retire":
        _retire(out, files)
    else:
        raise ConfigError(
            f"no Java → Rust scaffold {tool!r}", hint=f"Available: {sorted(SCAFFOLDS)}"
        )
    return out.changed
