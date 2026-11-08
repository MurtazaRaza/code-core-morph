"""A C port's Rust side (D-68): the crate's files, its ``sys`` module, and its place in a Make
build. Pure functions; the adapter writes and runs.

The layout, under ``rust/`` beside the C project::

    rust/Cargo.toml                    the workspace, with the release profile the archive gets
    rust/Cargo.lock
    rust/symbol-map.toml               C names and the Rust items that took them over
    rust/<library>_rs/Cargo.toml       staticlib + rlib; the library, its archive, kept headers
    rust/<library>_rs/src/lib.rs       #![deny(unsafe_code)]; pub mod <module>; ...
    rust/<library>_rs/src/<module>.rs           the port of one C source file, in safe Rust
    rust/<library>_rs/src/bridge/mod.rs         the C side: `sys`, and a module per source file
    rust/<library>_rs/src/bridge/sys.rs         bindgen's view of the kept headers (generated)
    rust/<library>_rs/src/bridge/<module>.rs    the file's functions, under their C names

There is no façade: every C function already has C linkage, so the headers stay as they are
and the crate exports the functions they declare. The Makefile that builds the library's
archive gains a marked block that builds the workspace; the archive's rule starts from an
empty archive and folds the crate's staticlib into it (an ``ar`` MRI script), so whatever links
the archive links the Rust code too; and the programs' links gain Rust's native libraries.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

WORKSPACE_DIR = "rust"
SYS_MODULE = "sys"
SYS_FILE = "src/bridge/sys.rs"  # relative to the crate
MAKE_BEGIN = "# >>> codecoremorph: rust (written by CodeCoreMorph; do not edit) >>>"
MAKE_END = "# <<< codecoremorph: rust <<<"
RUST_LIBS = "CODECOREMORPH_RUST_LIBS"
FOLD_MARK = "ADDLIB $(CODECOREMORPH_RUST_"
# The recipe of what cargo makes. Make reads a file's time again only after a recipe for it
# ran: without one, an archive or program waiting for a crate cargo just rebuilt would see its
# old time and keep the old code until the next make (D-70).
CARGO_MADE = "\t@:"
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")

# --------------------------------------------------------------------------- the crate


def workspace_manifest(members: Collection[str]) -> str:
    listed = ", ".join(f'"{m}"' for m in sorted(members))
    return (
        "# The Rust port of this project (CodeCoreMorph, D-68): one crate per C library, folded\n"
        "# into the library's archive by the Make build.\n"
        "[workspace]\n"
        f"members = [{listed}]\n"
        'resolver = "2"\n\n' + RELEASE_PROFILE
    )


# The archive takes the crate with the standard library in it: LTO and no debug info keep it
# small (bzip2's was 23 MB without), and a panic can't unwind into C anyway.
RELEASE_PROFILE = (
    "[profile.release]\n"
    "lto = true\n"
    "codegen-units = 1\n"
    "debug = false\n"
    'strip = "debuginfo"\n'
    'panic = "abort"\n'
)


def with_release_profile(manifest: str) -> str:
    """The workspace manifest with the release profile, unless it has one."""
    if re.search(r"(?m)^\s*\[profile\.release\]\s*$", manifest):
        return manifest
    return manifest.rstrip("\n") + "\n\n" + RELEASE_PROFILE


def package_manifest(
    crate: str, edition_year: str, library: str, archive: str, headers: Sequence[str]
) -> str:
    return (
        "[package]\n"
        f'name = "{crate}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "[lib]\n"
        "# staticlib: folded into the C library's archive; rlib: Rust code and tests use it.\n"
        'crate-type = ["staticlib", "rlib"]\n\n'
        "[package.metadata.codecoremorph]\n"
        "# The C library it takes over, the archive the Make build folds it into, and the kept\n"
        "# headers its `sys` module is generated from (D-68). The retirement removes them.\n"
        f'c-library = "{library}"\n'
        f'c-archive = "{archive}"\n'
        f"c-headers = {_toml_list(headers)}\n"
    )


def _toml_list(items: Collection[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in sorted(set(items))) + "]"


_HEADERS = re.compile(r"(?m)^(\s*c-headers\s*=\s*)\[([^\]]*)\]")
_ARCHIVE = re.compile(r'(?m)^\s*c-archive\s*=\s*"([^"]+)"')
_LIBRARY = re.compile(r'(?m)^\s*c-library\s*=\s*"([^"]+)"')


def manifest_headers(manifest: str) -> list[str]:
    """The kept headers a C port's crate names, or none."""
    match = _HEADERS.search(manifest)
    return sorted(re.findall(r'"([^"]+)"', match.group(2))) if match else []


def manifest_archive(manifest: str) -> str | None:
    match = _ARCHIVE.search(manifest)
    return match.group(1) if match else None


def manifest_library(manifest: str) -> str | None:
    match = _LIBRARY.search(manifest)
    return match.group(1) if match else None


def with_headers(manifest: str, headers: Collection[str]) -> str:
    """The crate manifest naming ``headers`` too (a later unit keeps more of them)."""
    return _set_headers(manifest, {*manifest_headers(manifest), *headers})


def without_headers(manifest: str, gone: Collection[str]) -> str:
    """The crate manifest no longer naming ``gone`` (a retirement that keeps the public API
    deletes the other headers, D-72)."""
    return _set_headers(manifest, set(manifest_headers(manifest)) - set(gone))


def _set_headers(manifest: str, headers: Collection[str]) -> str:
    match = _HEADERS.search(manifest)
    if match is None:
        return manifest
    listed = _toml_list(headers)
    if match.group(2).strip() and listed == _toml_list(manifest_headers(manifest)):
        return manifest  # the same list: keep its spelling
    return manifest[: match.start()] + match.group(1) + listed + manifest[match.end() :]


def lib_rs(library: str) -> str:
    return (
        f"//! The C library `{library}`, ported to Rust (CodeCoreMorph, D-68). C reaches this\n"
        "//! crate only through `bridge`, which exports its functions under their C names, the\n"
        "//! one module that may use `unsafe`.\n"
        "#![deny(unsafe_code)]\n\n"
        "#[allow(unsafe_code)]\n"
        "pub mod bridge;\n"
    )


def bridge_mod_rs() -> str:
    return (
        "//! The C side of the crate: the functions its C headers declare, exported under their\n"
        '//! names (`#[no_mangle] pub extern "C"`), one module per C source file, over the\n'
        "//! headers' own types from `sys`.\n"
        "#![allow(clippy::missing_safety_doc)]\n\n"
        "/// The kept C headers' types, constants and the functions still in C, as bindgen reads\n"
        "/// them (written before each build; don't edit).\n"
        "/// cbindgen:ignore\n"
        "#[allow(\n"
        "    non_upper_case_globals,\n"
        "    non_camel_case_types,\n"
        "    non_snake_case,\n"
        "    dead_code,\n"
        "    improper_ctypes,\n"
        "    clippy::all,\n"
        "    clippy::pedantic\n"
        ")]\n"
        f"pub mod {SYS_MODULE};\n"
    )


def module_rs(sources: Collection[str]) -> str:
    return _doc(f"Ported from {', '.join(sorted(sources))} (CodeCoreMorph, D-68).")


def bridge_module_rs(module: str, sources: Collection[str]) -> str:
    return _doc(
        f"The C functions of {', '.join(sorted(sources))}, exported under their names over "
        f"`crate::{module}`."
    )


def bin_manifest(
    crate: str, edition_year: str, program: str, artifact: str, dependencies: Collection[str]
) -> str:
    """A C program's package: one ``bin`` named after the program, which the Make build copies
    to the program's path, so whatever runs the program runs Rust (D-68)."""
    deps = "".join(f'{d} = {{ path = "../{d}" }}\n' for d in sorted(dependencies))
    return (
        "[package]\n"
        f'name = "{crate}"\n'
        'version = "0.1.0"\n'
        f'edition = "{edition_year}"\n'
        "publish = false\n\n"
        "# The C program's name, which scripts and users call it by.\n"
        "[[bin]]\n"
        f'name = "{program}"\n'
        'path = "src/main.rs"\n\n'
        "[dependencies]\n"
        f"{deps}"
        "\n[package.metadata.codecoremorph]\n"
        f'c-program = "{program}"\n'
        f'c-artifact = "{artifact}"\n'
    )


_PROGRAM = re.compile(r'(?m)^\s*c-program\s*=\s*"([^"]+)"')
_ARTIFACT = re.compile(r'(?m)^\s*c-artifact\s*=\s*"([^"]+)"')


def manifest_program(manifest: str) -> tuple[str, str] | None:
    """``(bin, artifact)`` of a C program's package, or None."""
    program, artifact = _PROGRAM.search(manifest), _ARTIFACT.search(manifest)
    if program is None or artifact is None:
        return None
    return program.group(1), artifact.group(1)


def main_rs(sources: Collection[str]) -> str:
    return (
        _doc(f"Ported from {', '.join(sorted(sources))} (CodeCoreMorph, D-68).")
        + "#![forbid(unsafe_code)]\n\nfn main() {}\n"
    )


TEST_MAP_TEXT = """\
# The C tests this port moved to Rust, and the Rust tests that took them over (CodeCoreMorph,
# D-68). One entry per test of the baseline, by the IDs test reports use; one C test may map to
# several Rust tests:
#
#   [[test]]
#   c = "tap:tests/test_htab/the table grows"     # or "make:<target>", the target's exit status
#   rust = "nextest:wf_rs::htab::the_table_grows"   # or a list
#   note = "..."    # optional: behavior the port couldn't keep; without `rust`, why there is none
"""


def test_rs(source: str) -> str:
    return (
        _doc(
            f"Translated from {source} (CodeCoreMorph, D-68): one test per C test (a TAP point, "
            "an automake test), each named in rust/test-map.toml."
        )
        + "#![forbid(unsafe_code)]\n"
    )


def retired_lib_rs(text: str, library: str | None) -> str:
    """A C port crate's root once the C is gone: its scaffold doc without the bridge."""
    if library is None:
        return text
    scaffold_doc = lib_rs(library).split("#![deny", 1)[0]
    if not text.startswith(scaffold_doc):
        return text
    return (
        f"//! The C library `{library}`, ported to Rust (CodeCoreMorph, D-68).\n"
        + text[len(scaffold_doc) :]
    )


_WORKSPACE_DOC = workspace_manifest([]).split("[workspace]", 1)[0]


def retired_workspace(manifest: str) -> str:
    if manifest.startswith(_WORKSPACE_DOC):
        return (
            "# The Rust port of this project (CodeCoreMorph, D-68).\n"
            + manifest[len(_WORKSPACE_DOC) :]
        )
    return manifest


def placeholder_sys() -> str:
    """``sys.rs`` before bindgen first writes it."""
    return "//! Written by bindgen from the kept C headers before each build.\n"


def _doc(text: str) -> str:
    return f"//! {text}\n"


SYMBOL_MAP_TEXT = """\
# The C names this port moved to Rust, and the Rust items that took them over (CodeCoreMorph,
# D-68). One entry per function and variable a ported source file exports:
#
#   [[symbol]]
#   c = "htab_add"
#   rust = "wf_rs::htab::Table::add"    # or a list of paths
#   note = "..."    # optional: behavior the port couldn't keep, or why rust is empty
"""

# --------------------------------------------------------------------------- sys and cbindgen


def wrapper_header(headers: Sequence[str]) -> str:
    """The header bindgen reads: each kept header, by its path in the sandbox."""
    return "".join(f'#include "{h}"\n' for h in headers)


def bindgen_argv(
    wrapper: str, out: str, project: str, blocklist: Collection[str], clang_args: Sequence[str]
) -> list[str]:
    """bindgen over ``wrapper``: the items the project's headers declare (``project`` is the
    worktree in the sandbox), with what they need from system headers, the crate's own exports
    left out, and compile-time layout checks of every type."""
    argv = [
        "bindgen",
        wrapper,
        "--output",
        out,
        "--allowlist-file",
        _regex_escape(project.rstrip("/")) + "/.*",
        "--no-doc-comments",
        "--merge-extern-blocks",
        "--sort-semantically",
    ]
    if blocklist:
        names = "^(" + "|".join(_regex_escape(n) for n in sorted(blocklist)) + ")$"
        argv += ["--blocklist-function", names, "--blocklist-var", names]
    return [*argv, "--", "-x", "c", "-std=c11", *clang_args]


_REGEX_META = re.compile(r"([\\.+*?()|\[\]{}^$])")


def _regex_escape(text: str) -> str:
    """``text`` as a literal in a Rust regex (bindgen's): only the metacharacters escaped."""
    return _REGEX_META.sub(r"\\\1", text)


def cbindgen_config(includes: Sequence[str]) -> str:
    """cbindgen's settings for a C port's crate (D-68): its header declares the crate's
    functions and includes the kept headers, so their types keep their C definitions (cbindgen
    skips `sys`, so it names them as they are), and the source adapter reads both sides alike."""
    listed = ", ".join(f'"{h}"' for h in includes)
    # Functions alone: c_abi_kept compares those, and an exported global's Rust type (an
    # `AtomicUsize` standing for a `size_t`) means nothing to C.
    return (
        f'language = "C"\nusize_is_size_t = true\nstyle = "both"\nincludes = [{listed}]\n\n'
        '[export]\nitem_types = ["functions"]\n'
    )


_PATH_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter")
_JOINED = (*_PATH_FLAGS, "-D", "-U", "--target=", "--sysroot=")
_SEPARATE = (*_PATH_FLAGS, "-D", "-U", "-include", "-target", "--sysroot", "-isysroot")


def clang_args(entries: Sequence[Mapping[str, object]]) -> list[str]:
    """The flags that find and shape the project's headers, over every entry of a compile
    database: include directories (made absolute), macros, forced includes and the target, as
    the C adapter's own header checks take them, so bindgen reads the layouts the compiler
    builds (a ``-D`` can add a struct field)."""
    found: list[tuple[str, ...]] = []
    for entry in entries:
        directory = str(entry.get("directory") or ".")
        raw = entry.get("arguments")
        if isinstance(raw, list):
            argv = [str(a) for a in cast(list[object], raw)]
        else:
            argv = shlex.split(str(entry.get("command") or ""))
        i = 1
        while i < len(argv):
            arg = argv[i]
            group: tuple[str, ...] | None = None
            if arg in _SEPARATE and i + 1 < len(argv):
                value = argv[i + 1]
                if arg in _PATH_FLAGS or arg == "-include":
                    value = posixpath.normpath(posixpath.join(directory, value))
                group = (arg, value)
                i += 1
            elif arg in ("-m32", "-m64"):
                group = (arg,)
            else:
                flag = next((f for f in _JOINED if arg.startswith(f) and arg != f), None)
                if flag in _PATH_FLAGS:
                    group = (flag, posixpath.normpath(posixpath.join(directory, arg[len(flag) :])))
                elif flag is not None:
                    group = (arg,)
            if group is not None and group not in found:
                found.append(group)
            i += 1
    return [part for group in found for part in group]


_SYS_TYPE = re.compile(r"(?m)^\s*pub\s+(?:struct|union|type|enum)\s+(\w+)")
_OPAQUE = re.compile(
    r"(?m)^\s*pub\s+struct\s+(\w+)\s*\{\s*_unused\s*:\s*\[\s*u8\s*;\s*0\s*\]\s*,?\s*\}"
)
_REPR_C = re.compile(
    r"#\[\s*repr\s*\(\s*C\b[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*"
    r"(?:pub(?:\s*\([^)]*\))?\s+)?(struct|union|enum)\s+(\w+)"
)


def copied_c_types(sys_text: str, files: Mapping[str, str]) -> list[tuple[str, int, str, str]]:
    """``#[repr(C)]`` types of the crate (``files``: masked sources outside ``sys.rs``, by path)
    that copy a C type of the kept headers, as ``(file, line, name, sys name)``. The headers'
    types come from ``sys`` alone, so a layout never drifts from C's (D-68). Names compare
    without case or underscores; a type the headers only declare (opaque in ``sys``) is the
    crate's to define."""
    opaque = set(_OPAQUE.findall(sys_text))
    known = {_key(n): n for n in _SYS_TYPE.findall(sys_text) if n not in opaque}
    found: list[tuple[str, int, str, str]] = []
    for path, text in sorted(files.items()):
        for match in _REPR_C.finditer(text):
            name = match.group(2)
            original = known.get(_key(name))
            if original is not None:
                line = text.count("\n", 0, match.start(2)) + 1
                found.append((path, line, name, original))
    return found


def _key(name: str) -> str:
    return name.replace("_", "").lower()


_NATIVE = re.compile(r"native-static-libs:\s*(.*)")


def native_libs(output: str) -> list[str] | None:
    """The libraries rustc says a staticlib needs (``--print native-static-libs``)."""
    match = _NATIVE.search(output)
    return match.group(1).split() if match else None


# --------------------------------------------------------------------------- Make


@dataclass(frozen=True)
class Crate:
    """A crate the Make block builds, by the names the Makefile uses."""

    name: str
    library: str  # the C library's name: -l<library>
    archive: str  # relative to the Makefile's directory
    staticlib: str  # relative to the Makefile's directory
    target: str  # the archive rule's target, as the Makefile spells it

    @property
    def variable(self) -> str:
        return f"CODECOREMORPH_RUST_{self.name}"


@dataclass(frozen=True)
class Program:
    """A C program the port made a Rust ``bin``, by the names the Makefile uses."""

    name: str  # the bin's name
    binary: str  # what Cargo builds, relative to the Makefile's directory
    target: str  # the program's rule's target, as the Makefile spells it

    @property
    def variable(self) -> str:
        return f"CODECOREMORPH_RUST_BIN_{re.sub(r'[^A-Za-z0-9_]', '_', self.name)}"


def make_block(
    crates: Sequence[Crate],
    manifest: str,
    libs: Sequence[str],
    programs: Sequence[Program] = (),
) -> str:
    """The marked block: the workspace's build, which every archive and program waits for,
    and the native libraries the C programs' links need. ``manifest`` is relative to the
    Makefile."""
    lines = [
        MAKE_BEGIN,
        "CODECOREMORPH_CARGO = cargo",
        f"{RUST_LIBS} = {' '.join(libs)}",
    ]
    for crate in sorted(crates, key=lambda c: c.name):
        lines += [
            f"{crate.variable} = {crate.staticlib}",
            f"{crate.target}: $({crate.variable})",
            f"$({crate.variable}): codecoremorph-rust",
            CARGO_MADE,
        ]
    for program in sorted(programs, key=lambda p: p.name):
        # Here, after the variable: make expands a rule's prerequisites as it reads the rule.
        lines += [
            f"{program.variable} = {program.binary}",
            f"{program.target}: $({program.variable})",
            f"$({program.variable}): codecoremorph-rust",
            CARGO_MADE,
        ]
    lines += [
        ".PHONY: codecoremorph-rust",
        "codecoremorph-rust:",
        f"\t$(CODECOREMORPH_CARGO) build --release --locked --offline --manifest-path {manifest}",
        MAKE_END,
    ]
    return "\n".join(lines) + "\n"


def with_make_block(text: str, block: str) -> str:
    """``text`` with ``block`` in place of its marked block, or at the end."""
    start, end = text.find(MAKE_BEGIN), text.find(MAKE_END)
    if start != -1 and end > start:
        stop = text.find("\n", end)
        stop = len(text) if stop == -1 else stop + 1
        return text[:start] + block + text[stop:]
    body = text if text.endswith("\n") or not text else text + "\n"
    return body + ("\n" if body else "") + block


_ASSIGN = re.compile(r"^\s*(?:override\s+|export\s+)?([A-Za-z_][\w.]*)\s*(?:::?=|\?=|\+=|=)(.*)$")
_RULE = re.compile(r"^([^\s:#=][^:#=]*?)\s*::?(?![=])(.*)$")
_VAR = re.compile(r"\$[({]([A-Za-z_][\w.]*)[)}]")
_COMPILER = re.compile(
    r"\$[({](?:CC|CXX|LD|LINK|CCLD|LDSHARED)[)}]|^(?:cc|c\+\+|gcc|g\+\+|clang)\b"
)


def assignments(text: str) -> dict[str, str]:
    """The Makefile's simple variable assignments (the last one wins; ``+=`` appends)."""
    found: dict[str, str] = {}
    for line in _logical_lines(text):
        if line.startswith("\t"):
            continue
        match = _ASSIGN.match(line)
        if match is None:
            continue
        name, value = match.group(1), match.group(2).split("#", 1)[0].strip()
        if re.match(rf"^\s*(?:override\s+|export\s+)?{re.escape(name)}\s*\+=", line):
            value = f"{found.get(name, '')} {value}".strip()
        found[name] = value
    return found


def expand(text: str, variables: Mapping[str, str], depth: int = 8) -> str:
    """``$(NAME)`` and ``${NAME}`` replaced by their values, a few levels deep."""
    for _ in range(depth):
        replaced = _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
        if replaced == text:
            break
        text = replaced
    return text


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for line in text.splitlines():
        current += line
        if line.endswith("\\"):
            current = current[:-1] + " "
            continue
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class _Rule:
    target: str  # as spelled
    line: int  # index of the rule line
    recipe: tuple[int, ...]  # indexes of the physical recipe lines


def _rules(lines: Sequence[str]) -> list[tuple[list[str], _Rule]]:
    """Each rule: its targets as spelled, and where its recipe is."""
    found: list[tuple[list[str], _Rule]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = None if line.startswith("\t") or _ASSIGN.match(line) else _RULE.match(line)
        if match is None or line.lstrip().startswith(("#", ".PHONY", "include ", "-include ")):
            i += 1
            continue
        targets = match.group(1).split()
        header_end = i
        while lines[header_end].endswith("\\") and header_end + 1 < len(lines):
            header_end += 1  # the prerequisites go on
        recipe: list[int] = []
        j = header_end + 1
        while j < len(lines) and lines[j].startswith("\t"):
            recipe.append(j)
            while lines[j].endswith("\\") and j + 1 < len(lines):
                j += 1
                recipe.append(j)
            j += 1
        for target in targets:
            found.append((targets, _Rule(target=target, line=i, recipe=tuple(recipe))))
        i = j
    return found


def rule_making(text: str, path: str) -> str | None:
    """The target, as the Makefile spells it, of the rule that makes ``path`` (an archive or a
    program, relative to the Makefile's directory), if it has one with a recipe."""
    variables = assignments(text)
    wanted = posixpath.normpath(path)
    for _, rule in _rules(text.splitlines()):
        name = expand(rule.target, variables)
        if rule.recipe and posixpath.normpath(name) == wanted:
            return rule.target
    return None


def with_rust_program(text: str, program: Program) -> str:
    """The program's rule copies the ``bin`` Cargo builds to the program's path, so the recipes
    that run it (``make test``) keep working (D-68); the marked block gives it the ``bin`` as its
    prerequisite, after defining the variable. A rule that makes other targets too stays for
    the unit to split."""
    lines = text.splitlines()
    rule = next(
        (r for targets, r in _rules(lines) if r.target == program.target and len(targets) == 1),
        None,
    )
    if rule is None or not rule.recipe:
        return text
    replacement = [f"{program.target}:", f"\tcp $({program.variable}) $@"]
    if lines[rule.line : rule.recipe[-1] + 1] == replacement:
        return text
    out = [*lines[: rule.line], *replacement, *lines[rule.recipe[-1] + 1 :]]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def fold_archive(text: str, crate: Crate) -> str:
    """The archive's rule starts from an empty archive (an ``ar r`` recipe would keep the
    objects of deleted sources) and ends by folding the crate's staticlib into it."""
    lines = text.splitlines()
    rule = next(
        (r for _, r in _rules(lines) if r.target == crate.target and r.recipe),
        None,
    )
    if rule is None or any(FOLD_MARK in lines[i] for i in rule.recipe):
        return text
    removes = any(
        re.search(r"\brm\s+-f\b", lines[i])
        and (crate.target in lines[i] or "$@" in lines[i] or crate.archive in lines[i])
        for i in rule.recipe
    )
    fold = f"\tprintf 'OPEN $@\\nADDLIB $({crate.variable})\\nSAVE\\nEND\\n' | $(AR) -M"
    out = list(lines)
    out.insert(rule.recipe[-1] + 1, fold)
    if not removes:
        out.insert(rule.recipe[0], "\trm -f $@")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------------------- automake (D-70)

AUTOMAKE = "Makefile.am"
_AM_PRIMARY = re.compile(r"^\s*(\w+)_(LTLIBRARIES|LIBRARIES|PROGRAMS)\s*\+?=(.*)$")


def am_canonical(name: str) -> str:
    """automake's name for a library or program in its variables: every character but a
    letter, a digit, ``_`` and ``@`` becomes ``_`` (``libkvconf.la`` → ``libkvconf_la``)."""
    return re.sub(r"[^A-Za-z0-9_@]", "_", name)


def _outside_block(text: str) -> str:
    start, end = text.find(MAKE_BEGIN), text.find(MAKE_END)
    if start != -1 and end > start:
        return text[:start] + text[end + len(MAKE_END) :]
    return text


def am_defined(text: str) -> set[str]:
    """The variables a ``Makefile.am`` sets outside the marked block (automake refuses a
    ``+=`` before an ``=``, and a second ``=``)."""
    return set(assignments(_outside_block(text)))


def am_target(text: str, path: str, primaries: Collection[str]) -> str | None:
    """The canonical name of ``path`` (relative to the ``Makefile.am``) when one of the
    ``primaries`` (``LTLIBRARIES``, ``LIBRARIES``, ``PROGRAMS``) lists it, as in
    ``lib_LTLIBRARIES = libkvconf.la``."""
    body = _outside_block(text)
    variables = assignments(body)
    wanted = posixpath.normpath(path)
    for line in _logical_lines(body):
        match = _AM_PRIMARY.match(line)
        if match is None or match.group(2) not in primaries:
            continue
        for word in expand(match.group(3).split("#", 1)[0], variables).split():
            if posixpath.normpath(word.replace("$(EXEEXT)", "")) == wanted:
                return am_canonical(wanted)
    return None


@dataclass(frozen=True)
class AmLibrary:
    """A crate in the ``Makefile.am`` of its C library: a libtool library
    (``lib_LTLIBRARIES``) or a static one (``lib_LIBRARIES``)."""

    name: str  # the crate
    canonical: str  # the library's in automake's variables: libkvconf_la
    libtool: bool
    staticlib: str  # what Cargo builds, from the top source directory
    archive: str = ""  # the static archive's file name, libkvconf.a

    @property
    def variable(self) -> str:
        return f"CODECOREMORPH_RUST_{self.name}"

    @property
    def object(self) -> str:
        return f"codecoremorph-{self.name}"


@dataclass(frozen=True)
class AmProgram:
    """A C program of a ``Makefile.am`` that a Rust ``bin`` took over."""

    name: str  # the bin's name
    canonical: str  # the program's in automake's variables
    binary: str  # what Cargo builds, from the top source directory

    @property
    def variable(self) -> str:
        return f"CODECOREMORPH_RUST_BIN_{re.sub(r'[^A-Za-z0-9_]', '_', self.name)}"


def am_block(
    libraries: Sequence[AmLibrary],
    programs: Sequence[AmProgram],
    libs: Sequence[str],
    manifest: str,
    defined: Collection[str],
) -> str:
    """The marked block of a ``Makefile.am`` (D-70). Each crate becomes one relocatable object
    (``cc -r`` over its staticlib, the standard library in it) that the library takes in its
    ``LIBADD``: a libtool library through a libtool object (``.lo``), the only kind libtool
    puts in both the shared library and the static archive, with Rust's native libraries,
    which libtool hands on to whatever links the library. A program's link copies its Rust
    ``bin`` instead. ``manifest`` and the crates' paths are from the top source directory;
    ``defined`` is what the file sets outside the block, which the block appends to."""
    assigned = set(defined)
    lines = [MAKE_BEGIN, "CODECOREMORPH_CARGO = cargo"]

    def assign(name: str, value: str) -> None:
        lines.append(f"{name} {'+=' if name in assigned else '='} {value}")
        assigned.add(name)

    if libraries:
        lines.append(f"{RUST_LIBS} = {' '.join(libs)}")
    for library in sorted(libraries, key=lambda x: x.name):
        obj = library.object
        made = f"{obj}.lo" if library.libtool else f"{obj}.o"
        lines.append(f"{library.variable} = $(top_srcdir)/{library.staticlib}")
        added = f"{made} $({RUST_LIBS})" if library.libtool else made
        assign(f"{library.canonical}_LIBADD", added)
        assign(f"EXTRA_{library.canonical}_DEPENDENCIES", made)
        assign("CLEANFILES", f"{obj}.o {obj}.lo" if library.libtool else f"{obj}.o")
        lines += [
            f"{made}: $({library.variable})",
            f"\t$(CC) -r -nostdlib -o {obj}.o -Wl,--whole-archive $({library.variable}) "
            "-Wl,--no-whole-archive",
        ]
        if library.libtool:
            lines.append(
                '\tprintf "# %s - a libtool object file\\n# Generated by CodeCoreMorph for '
                "libtool\\npic_object='%s'\\nnon_pic_object='%s'\\n\" "
                f"$@ {obj}.o {obj}.o > $@"
            )
        lines += [f"$({library.variable}): codecoremorph-rust", CARGO_MADE]
    for program in sorted(programs, key=lambda p: p.name):
        lines.append(f"{program.variable} = $(top_srcdir)/{program.binary}")
        if f"{program.canonical}_SOURCES" not in assigned:
            assign(f"{program.canonical}_SOURCES", "")  # not the C program's default source
        # The link's arguments, the C objects and libraries, go to `:`.
        lines.append(f"{program.canonical}_LINK = cp $({program.variable}) $@ && :")
        assign(f"EXTRA_{program.canonical}_DEPENDENCIES", f"$({program.variable})")
        lines += [f"$({program.variable}): codecoremorph-rust", CARGO_MADE]
    lines += [
        ".PHONY: codecoremorph-rust",
        "codecoremorph-rust:",
        "\t$(CODECOREMORPH_CARGO) build --release --locked --offline --manifest-path "
        f"$(top_srcdir)/{manifest}",
        MAKE_END,
    ]
    return "\n".join(line.rstrip() for line in lines) + "\n"


def with_am_program(text: str, canonical: str) -> str:
    """The program builds no C: its ``_SOURCES`` is empty (the first assignment emptied, any
    ``+=`` after it removed), so the unit can delete the C files it lists."""
    lines = text.splitlines()
    head = re.compile(rf"^\s*{re.escape(canonical)}_SOURCES\s*(\+?)=")
    out: list[str] = []
    seen = False
    i = 0
    block = False
    while i < len(lines):
        line = lines[i]
        block = block or line == MAKE_BEGIN
        match = None if block else head.match(line)
        if line == MAKE_END:
            block = False
        if match is None:
            out.append(line)
            i += 1
            continue
        while lines[i].endswith("\\") and i + 1 < len(lines):
            i += 1  # its continuation lines go with it
        i += 1
        if not seen and not match.group(1):
            out.append(f"{canonical}_SOURCES =")
        seen = True
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def with_am_native_libs(text: str, archives: Collection[str]) -> str:
    """A static library (``lib_LIBRARIES``) hands on nothing, so each ``LDADD`` of the file
    that names one of ``archives`` (``libfoo.a``) gets ``$(CODECOREMORPH_RUST_LIBS)``."""
    if not archives:
        return text
    lines = text.splitlines()
    out = list(lines)
    i = 0
    block = False
    while i < len(lines):
        start = i
        block = block or lines[i] == MAKE_BEGIN
        logical = lines[i]
        while logical.endswith("\\") and i + 1 < len(lines):
            i += 1
            logical = logical[:-1] + " " + lines[i]
        if lines[start] == MAKE_END:
            block = False
        i += 1
        match = re.match(r"^\s*(?:\w+_)?LDADD\s*\+?=(.*)$", logical)
        if block or match is None or f"$({RUST_LIBS})" in logical:
            continue
        words = {posixpath.basename(w) for w in match.group(1).split("#", 1)[0].split()}
        if words & set(archives):
            out[i - 1] = out[i - 1] + f" $({RUST_LIBS})"
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def with_native_libs(text: str, crates: Sequence[Crate]) -> str:
    """Each link that takes one of the crates' archives (``-l<library>`` or the archive's
    path) gets ``$(CODECOREMORPH_RUST_LIBS)`` at its end; the archive rules don't link."""
    variables = assignments(text)
    lines = text.splitlines()
    folded = {
        i for _, r in _rules(lines) for i in r.recipe if any(r.target == c.target for c in crates)
    }
    words = {f"-l{c.library}" for c in crates} | {posixpath.basename(c.archive) for c in crates}
    out = list(lines)
    i = 0
    while i < len(lines):
        start = i
        logical = lines[i]
        while logical.endswith("\\") and i + 1 < len(lines):
            i += 1
            logical = logical[:-1] + " " + lines[i]
        end = i
        i += 1
        if not lines[start].startswith("\t") or start in folded or f"$({RUST_LIBS})" in logical:
            continue
        command = logical.strip().lstrip("@-+").strip()
        if not _COMPILER.search(command) or " -o" not in f" {expand(command, variables)}":
            continue
        tokens = {posixpath.basename(t) for t in expand(command, variables).split()}
        tokens |= set(expand(command, variables).split())
        if tokens & words:
            out[end] = out[end] + f" $({RUST_LIBS})"
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
