"""A module port's Rust side (D-58): names, the workspace scaffold, the bridge prelude and its
C++ helpers, and the checks on unsafe code. Pure functions; the adapter writes and runs.

The layout, under ``rust/`` beside the CMake project::

    rust/Cargo.toml                  the workspace: one crate per C++ library target
    rust/Cargo.lock
    rust/symbol-map.toml             C++ names and the Rust items that took them over
    rust/<target>_rs/Cargo.toml      staticlib + rlib, its CMake target in package.metadata
    rust/<target>_rs/cbindgen.toml   writes include/<crate>/bridge.h from the crate
    rust/<target>_rs/include/<crate>/bridge.h    cbindgen's output (regenerated, committed)
    rust/<target>_rs/include/<crate>/bridge.hpp  C++98 helpers for the façades
    rust/<target>_rs/src/lib.rs      #![deny(unsafe_code)]; pub mod <module>; ...
    rust/<target>_rs/src/<module>.rs           the port, in safe Rust
    rust/<target>_rs/src/bridge/mod.rs         the C interface's shared types
    rust/<target>_rs/src/bridge/<module>.rs    the module's extern "C" functions
"""

from __future__ import annotations

import re
from collections.abc import Collection

WORKSPACE_DIR = "rust"
CPP_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx", ".c", ".cc", ".cpp", ".cxx", ".c++")
SYMBOL_MAP = f"{WORKSPACE_DIR}/symbol-map.toml"
BRIDGE_MODULE = "bridge"

# Keywords (strict and reserved, every edition) and names the layout takes.
RUST_KEYWORDS = frozenset(
    """as break const continue crate else enum extern false fn for if impl in let loop match mod
    move mut pub ref return self Self static struct super trait true type unsafe use where
    while async await dyn abstract become box do final macro override priv typeof unsized
    virtual yield try gen union""".split()  # noqa: SIM905 - a word list reads best as words
)
_TAKEN_MODULES = frozenset({"lib", "main", BRIDGE_MODULE})
_DENY_UNSAFE = re.compile(r"#!\[\s*(?:deny|forbid)\s*\([^\]]*\bunsafe_code\b[^\]]*\)\s*\]")
_ALLOW_UNSAFE = re.compile(r"#!?\[\s*(?:allow|expect|warn)\s*\([^\]]*\bunsafe_code\b[^\]]*\)\s*\]")
_BRIDGE_DECL = re.compile(r"\s*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+bridge\s*;")


def identifier(text: str) -> str:
    """A snake_case Rust identifier for ``text``."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "module"
    return name if name[0].isalpha() else f"m_{name}"


def port_names(build_target: str, stem: str, taken: Collection[str]) -> tuple[str, str]:
    """``(crate, module)`` for a module of ``build_target``: the crate ``<target>_rs``, and a
    module name from the file stem that is no keyword, no name the layout takes, and not in
    ``taken``."""
    crate = f"{identifier(build_target)}_rs"
    module = identifier(stem)
    if module in RUST_KEYWORDS or module in _TAKEN_MODULES:
        module = f"{module}_mod"
    base, n = module, 2
    while module in taken:
        module, n = f"{base}_{n}", n + 1
    return crate, module


def pascal(crate: str) -> str:
    """``calc_rs`` → ``CalcRs``: the prefix of the bridge's C types."""
    return "".join(part[:1].upper() + part[1:] for part in crate.split("_") if part)


# --------------------------------------------------------------------------- the workspace


def workspace_manifest(members: Collection[str]) -> str:
    listed = ", ".join(f'"{m}"' for m in sorted(members))
    return (
        "# The Rust port of this project (CodeCoreMorph, D-58): one crate per C++ library\n"
        "# target, built and linked by CMake through Corrosion.\n"
        "[workspace]\n"
        f"members = [{listed}]\n"
        'resolver = "2"\n'
    )


_MEMBERS = re.compile(r"(?ms)^(\s*members\s*=\s*\[)(.*?)(\])")


def with_member(manifest: str, member: str) -> str:
    """The workspace manifest with ``member`` listed (sorted, once)."""
    match = _MEMBERS.search(manifest)
    if match is None:
        return manifest.rstrip("\n") + f'\nmembers = ["{member}"]\n'
    members = re.findall(r'"([^"]+)"', match.group(2))
    if member in members:
        return manifest
    listed = ", ".join(f'"{m}"' for m in sorted({*members, member}))
    return manifest[: match.start(2)] + listed + manifest[match.end(2) :]


def package_manifest(crate: str, edition_year: str, build_target: str) -> str:
    return (
        "[package]\n"
        f'name = "{crate}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "[lib]\n"
        "# staticlib: CMake links it into the C++ target; rlib: Rust code and tests use it.\n"
        'crate-type = ["staticlib", "rlib"]\n\n'
        "[package.metadata.codecoremorph]\n"
        f'cmake-target = "{build_target}"\n'
    )


_CMAKE_TARGET = re.compile(r'(?m)^\s*cmake-target\s*=\s*"([^"]+)"')


def cmake_target(manifest: str) -> str | None:
    """The CMake target a crate's manifest links it into, if it names one."""
    match = _CMAKE_TARGET.search(manifest)
    return match.group(1) if match else None


def lib_rs(crate: str, build_target: str) -> str:
    return (
        f"//! The C++ library `{build_target}`, ported to Rust (CodeCoreMorph, D-58). C++ reaches\n"
        f"//! this crate only through `bridge` (the C header `include/{crate}/bridge.h`), the one\n"
        "//! module that may use `unsafe`.\n"
        "#![deny(unsafe_code)]\n\n"
        "#[allow(unsafe_code)]\n"
        f"pub mod {BRIDGE_MODULE};\n"
    )


def with_module(text: str, module: str, *, public: bool = True) -> str:
    """A module file (``lib.rs``, ``bridge/mod.rs``) declaring ``mod <module>;`` once, after
    the declarations already there."""
    declaration = f"{'pub ' if public else ''}mod {module};"
    if re.search(rf"(?m)^\s*(?:pub\s+)?mod\s+{re.escape(module)}\s*;", text):
        return text
    return text.rstrip("\n") + "\n" + declaration + "\n"


def bridge_mod_rs(crate: str) -> str:
    prefix = pascal(crate)
    return (
        '//! The C interface of the crate, for the C++ façades: `#[no_mangle] extern "C"`\n'
        "//! functions over `#[repr(C)]` types and opaque handles, one module per C++ module.\n"
        f"//! cbindgen writes it to `include/{crate}/bridge.h`. Pointers come from the façades: a\n"
        f"//! `{prefix}Str` points at `len` readable bytes (or `len` is 0), and a `{prefix}Sink`\n"
        "//! stays valid during the call.\n"
        "#![allow(clippy::missing_safety_doc)]\n\n"
        "use std::ffi::c_void;\n\n"
        "/// Borrowed bytes, such as a `std::string`'s (bytes, not necessarily UTF-8).\n"
        "#[repr(C)]\n"
        "#[derive(Clone, Copy)]\n"
        f"pub struct {prefix}Str {{\n"
        "    pub ptr: *const u8,\n"
        "    pub len: usize,\n"
        "}\n\n"
        "/// Where Rust writes bytes for C++: `push` appends them to the C++ object behind `ctx`\n"
        f"/// (`{crate}::into(std::string&)` in `bridge.hpp`).\n"
        "#[repr(C)]\n"
        f"pub struct {prefix}Sink {{\n"
        "    pub ctx: *mut c_void,\n"
        '    pub push: extern "C" fn(ctx: *mut c_void, ptr: *const u8, len: usize),\n'
        "}\n\n"
        f"impl {prefix}Str {{\n"
        "    /// The bytes.\n"
        "    ///\n"
        "    /// # Safety\n"
        "    ///\n"
        "    /// `ptr` points at `len` readable bytes that outlive `'a`, or `len` is 0.\n"
        "    pub unsafe fn bytes<'a>(self) -> &'a [u8] {\n"
        "        if self.len == 0 {\n"
        "            &[]\n"
        "        } else {\n"
        "            std::slice::from_raw_parts(self.ptr, self.len)\n"
        "        }\n"
        "    }\n"
        "}\n\n"
        f"impl {prefix}Sink {{\n"
        "    /// Appends `bytes` to the C++ object.\n"
        "    pub fn push(&self, bytes: &[u8]) {\n"
        "        (self.push)(self.ctx, bytes.as_ptr(), bytes.len());\n"
        "    }\n"
        "}\n"
    )


def _doc(text: str) -> str:
    """``text`` as a module's doc comment, wrapped at 100 columns."""
    lines: list[str] = []
    line = "//!"
    for word in text.split():
        if len(line) + 1 + len(word) > 100 and line != "//!":
            lines.append(line)
            line = "//!"
        line += f" {word}"
    return "\n".join([*lines, line]) + "\n"


def module_rs(sources: Collection[str]) -> str:
    return _doc(f"Ported from {', '.join(sorted(sources))} (CodeCoreMorph, D-58).")


def bridge_module_rs(module: str, sources: Collection[str]) -> str:
    return _doc(
        f"The C interface of `crate::{module}`, which the C++ façade "
        f"({', '.join(sorted(sources))}) calls."
    )


def bridge_hpp(crate: str) -> str:
    prefix = pascal(crate)
    guard = f"{crate.upper()}_BRIDGE_HPP"
    return f"""\
// C++ helpers for the façades over the {crate} crate (CodeCoreMorph, D-58): std::string to Rust
// as borrowed bytes, and the bytes Rust writes back into a std::string or a vector. C++98.
#ifndef {guard}
#define {guard}

#include <string>
#include <vector>

#include "{crate}/bridge.h"

extern "C" {{

inline void {crate}_append_to_string(void* ctx, const uint8_t* ptr, size_t len)
{{
    static_cast<std::string*>(ctx)->append(reinterpret_cast<const char*>(ptr), len);
}}

inline void {crate}_push_to_vector(void* ctx, const uint8_t* ptr, size_t len)
{{
    static_cast<std::vector<std::string>*>(ctx)->push_back(
        std::string(reinterpret_cast<const char*>(ptr), len));
}}

}} // extern "C"

namespace {crate} {{

// The bytes of s, borrowed for the call.
inline {prefix}Str str(const std::string& s)
{{
    {prefix}Str view;
    view.ptr = reinterpret_cast<const uint8_t*>(s.data());
    view.len = s.size();
    return view;
}}

// A sink that appends what Rust writes to out.
inline {prefix}Sink into(std::string& out)
{{
    {prefix}Sink sink;
    sink.ctx = &out;
    sink.push = {crate}_append_to_string;
    return sink;
}}

// A sink that adds each string Rust writes to out.
inline {prefix}Sink into(std::vector<std::string>& out)
{{
    {prefix}Sink sink;
    sink.ctx = &out;
    sink.push = {crate}_push_to_vector;
    return sink;
}}

}} // namespace {crate}

#endif
"""


def cbindgen_toml(crate: str) -> str:
    prefix = pascal(crate)
    return (
        f"# cbindgen writes the bridge's C header, include/{crate}/bridge.h, from the crate's\n"
        '# extern "C" functions; CodeCoreMorph regenerates it before each build of a port unit.\n'
        'language = "C"\n'
        f'include_guard = "{crate.upper()}_BRIDGE_H"\n'
        f'autogen_warning = "/* Generated by cbindgen from the {crate} crate. Do not edit. */"\n'
        "cpp_compat = true\n"
        "usize_is_size_t = true\n"
        'style = "both"\n\n'
        "[export]\n"
        f'include = ["{prefix}Str", "{prefix}Sink"]\n'
    )


def bridge_header_path(crate: str) -> str:
    """The generated header, relative to the crate."""
    return f"include/{crate}/bridge.h"


SYMBOL_MAP_TEXT = """\
# The C++ names this port moved to Rust, and the Rust items that took them over (CodeCoreMorph,
# D-58). One entry per function and type the ported modules' headers declare publicly:
#
#   [[symbol]]
#   cpp = "calc::Rational::str"
#   rust = "calc_rs::rational::Rational::to_string"    # or a list of paths
#   note = "..."    # optional: behavior the port couldn't keep, or why rust is empty
"""


# --------------------------------------------------------------------------- unsafe code


def unsafe_problems(
    lib_path: str, lib_text: str, files: dict[str, str]
) -> list[tuple[str, int, str]]:
    """Where unsafe code escapes the bridge, as ``(file, line, what)``. The crate root
    (``lib_text``, masked) must deny ``unsafe_code``, and only its ``mod bridge`` declaration may
    allow it; ``files`` are the crate's other sources, masked, by worktree path, and none of
    them may allow it (the bridge's own files need not: the declaration covers them)."""
    found: list[tuple[str, int, str]] = []
    if _DENY_UNSAFE.search(lib_text) is None:
        found.append((lib_path, 1, "the crate root must keep #![deny(unsafe_code)]"))
    for match in _ALLOW_UNSAFE.finditer(lib_text):
        if _BRIDGE_DECL.match(lib_text, match.end()) is None:
            found.append((lib_path, _line(lib_text, match.start()), "allows unsafe code"))
    for path, text in sorted(files.items()):
        for match in _ALLOW_UNSAFE.finditer(text):
            found.append((path, _line(text, match.start()), "allows unsafe code"))
    return found


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------- Phase 14 (D-60)

TEST_MAP = f"{WORKSPACE_DIR}/test-map.toml"

TEST_MAP_TEXT = """\
# The C++ tests this port moved to Rust, and the Rust tests that took them over (CodeCoreMorph,
# D-60). One entry per test of the baseline, by the IDs test reports use:
#
#   [[test]]
#   cpp = "gtest:Rational.AddsFractions"     # or "ctest:<name>" for a CTest entry
#   rust = "nextest:calc_rs::rational_test::adds_fractions"
#   note = "..."    # optional: behavior the port couldn't keep; without `rust`, why there is none
"""

WRAPPER_NOTE = "only runs a GoogleTest binary, whose cases are mapped one by one"


def bin_manifest(
    crate: str, edition_year: str, bin_name: str, build_target: str, dependencies: Collection[str]
) -> str:
    """A program's package: one ``bin`` named after the C++ executable, so CTest and scripts
    keep calling it by its name (Corrosion imports it as a CMake executable of that name)."""
    deps = "".join(f'{d} = {{ path = "../{d}" }}\n' for d in sorted(dependencies))
    return (
        "[package]\n"
        f'name = "{crate}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "# The C++ executable's name: CTest entries and scripts call the program by it.\n"
        "[[bin]]\n"
        f'name = "{bin_name}"\n'
        'path = "src/main.rs"\n\n'
        "[dependencies]\n"
        f"{deps}"
        "\n[package.metadata.codecoremorph]\n"
        f'cmake-target = "{build_target}"\n'
    )


def main_rs(sources: Collection[str]) -> str:
    return (
        _doc(f"Ported from {', '.join(sorted(sources))} (CodeCoreMorph, D-60).")
        + "#![forbid(unsafe_code)]\n\nfn main() {}\n"
    )


def test_rs(source: str) -> str:
    return (
        _doc(
            f"Translated from {source} (CodeCoreMorph, D-60): one test per C++ test case, each "
            f"named in {TEST_MAP}."
        )
        + "#![forbid(unsafe_code)]\n"
    )


def with_dev_dependencies(manifest: str, dependencies: Collection[str]) -> str:
    """A package manifest with path ``dev-dependencies`` on the workspace's ``dependencies``
    (a test that uses another target's crate); those already listed stay."""
    missing = [
        d for d in sorted(dependencies) if not re.search(rf"(?m)^\s*{re.escape(d)}\s*=", manifest)
    ]
    if not missing:
        return manifest
    lines = "".join(f'{d} = {{ path = "../{d}" }}\n' for d in missing)
    header = re.search(r"(?m)^\[dev-dependencies\]\s*$", manifest)
    if header is not None:
        end = manifest.find("\n", header.end())
        end = len(manifest) if end < 0 else end + 1
        return manifest[:end] + lines + manifest[end:]
    return manifest.rstrip("\n") + "\n\n[dev-dependencies]\n" + lines


def with_test_entries(text: str, entries: Collection[tuple[str, str]]) -> str:
    """The test map with ``(source test ID, note)`` entries that map to no target test,
    appended unless the test is already mapped."""
    added = ""
    for source, note in entries:
        if re.search(rf'(?m)^\s*cpp\s*=\s*"{re.escape(source)}"', text):
            continue
        added += f'\n[[test]]\ncpp = "{source}"\nnote = "{note}"\n'
    return text if not added else text.rstrip("\n") + "\n" + added


_CMAKE_TARGET_COMMANDS = (
    "add_executable",
    "target_link_libraries",
    "target_include_directories",
    "target_compile_definitions",
    "target_compile_options",
    "target_compile_features",
    "target_link_options",
    "target_sources",
    "set_target_properties",
)


def without_cmake_target(text: str, target: str) -> str:
    """CMake text without the commands that define or configure ``target``
    (``add_executable(<target> ...)`` and the ``target_*(<target> ...)`` that follow it), each
    with its whole lines: a Rust program takes the name over (D-60)."""
    pattern = re.compile(
        rf"(?im)^[ \t]*(?:{'|'.join(_CMAKE_TARGET_COMMANDS)})[ \t]*\("
        rf"[ \t\r\n]*{re.escape(target)}(?=[\s)])"
    )
    out = text
    while (match := pattern.search(out)) is not None:
        depth, i = 0, out.index("(", match.start())
        while i < len(out):
            if out[i] == "(":
                depth += 1
            elif out[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = out.find("\n", i)
        end = len(out) if end < 0 else end + 1
        before, after = out[: match.start()], out[end:]
        if before.endswith(("\n\n", "\r\n\r\n")):
            # No blank line twice where the commands were.
            for newline in ("\r\n", "\n"):
                if after.startswith(newline):
                    after = after[len(newline) :]
                    break
        out = before + after
    return out


_BRIDGE_LINES = re.compile(
    r"(?m)^[ \t]*#\[\s*allow\s*\(\s*unsafe_code\s*\)\s*\][ \t]*\r?\n"
    r"[ \t]*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+bridge\s*;[ \t]*\r?\n?"
)


def retired_lib_rs(text: str, crate: str, build_target: str | None) -> str:
    """A crate root once C++ is gone: no ``mod bridge``, ``#![forbid(unsafe_code)]``, and the
    scaffold's doc, if it is still the scaffold's, without the bridge."""
    out = _BRIDGE_LINES.sub("", text)
    out = re.sub(
        r"(?m)^[ \t]*#!\[\s*deny\s*\(\s*unsafe_code\s*\)\s*\]", "#![forbid(unsafe_code)]", out
    )
    if build_target is not None:
        scaffold_doc = lib_rs(crate, build_target).split("#![deny", 1)[0]
        if out.startswith(scaffold_doc):
            out = (
                f"//! The C++ library `{build_target}`, ported to Rust "
                "(CodeCoreMorph, D-58, D-60).\n" + out[len(scaffold_doc) :]
            )
    return out


_LIB_SECTION = re.compile(
    r'(?ms)^\[lib\]\s*\n(?:#[^\n]*\n)*crate-type\s*=\s*\["staticlib",\s*"rlib"\]\s*\n\n?'
)
_METADATA = re.compile(r"(?ms)\n*^\[package\.metadata\.codecoremorph\]\s*\n(?:[^\[\n][^\n]*\n?)*")


def retired_manifest(manifest: str) -> str:
    """A package manifest once C++ is gone: no ``staticlib`` (Cargo's default ``rlib``) and no
    CMake target."""
    out = _LIB_SECTION.sub("", manifest)
    out = _METADATA.sub("\n", out)
    return out.rstrip("\n") + "\n"


_WORKSPACE_DOC = (
    "# The Rust port of this project (CodeCoreMorph, D-58): one crate per C++ library\n"
    "# target, built and linked by CMake through Corrosion.\n"
)


def retired_workspace(manifest: str) -> str:
    if manifest.startswith(_WORKSPACE_DOC):
        return (
            "# The Rust port of this project (CodeCoreMorph, D-58, D-60).\n"
            + manifest[len(_WORKSPACE_DOC) :]
        )
    return manifest


_UNSAFE = re.compile(r"\bunsafe\b")


def unsafe_keywords(path: str, masked: str) -> list[tuple[str, int, str]]:
    """Each ``unsafe`` in a masked source, as ``(file, line, what)``."""
    return [(path, _line(masked, m.start()), "uses unsafe code") for m in _UNSAFE.finditer(masked)]
