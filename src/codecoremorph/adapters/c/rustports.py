"""The C side of a Rust → C port (SPEC §11.8, D-75): what the C adapter, as the port's target,
writes into the crate, and the Rust adapter, as its source, reads back.

The C goes under ``c/`` of the repository: a static library ``<crate>_c`` with one source and
one header per ported module, and the translated tests under ``c/tests``. A top-level
``CMakeLists.txt`` builds it, with AddressSanitizer and UndefinedBehaviorSanitizer when
``CODECOREMORPH_SANITIZE`` is on (CodeCoreMorph's builds turn it on). During the port Cargo
links the library through a build script in the library's package that runs nothing
(:data:`RUST_C_LINK_SCRIPT`, the one I-12 accepts): it reads ``c/cargo-link`` and the directory
the library is in. Each ported module stays a Rust façade over the C, through a ``sys`` module
bindgen writes from the module's header; ``[package.metadata.codecoremorph.c-modules]`` in the
library's ``Cargo.toml`` names the header of each, both as paths from the repository's root.
The crate is the repository's root package or a member of a workspace below it, such as the
``rust/`` workspace a C → Rust port leaves (the round trip). The retirement deletes the Rust;
the CMake build stays.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

from codecoremorph.adapters.c.characterize import HELPER, HELPER_TEXT
from codecoremorph.adapters.rust.cargo import find_crates, find_packages
from codecoremorph.adapters.rust.symbols import module_path
from codecoremorph.core.errors import ConfigError
from codecoremorph.invariants.checks import RUST_C_LINK_SCRIPT

__all__ = ["RUST_C_LINK_SCRIPT"]

C_DIR = "c"
TOP_CMAKE = "CMakeLists.txt"
C_CMAKE = "c/CMakeLists.txt"
TESTS_CMAKE = "c/tests/CMakeLists.txt"
CARGO_LINK = "c/cargo-link"
BUILD_SCRIPT = "build.rs"
SYMBOL_MAP = "c/symbol-map.toml"
TEST_MAP = "c/test-map.toml"
SANITIZE = "CODECOREMORPH_SANITIZE"
LIB_DIR_ENV = "CODECOREMORPH_C_LIB_DIR"
METADATA = "package.metadata.codecoremorph.c-modules"
TOP_MARK = "# Written by CodeCoreMorph for a Rust → C port (D-75)"
SOURCES_BEGIN = "  # >>> codecoremorph: ported modules >>>"
SOURCES_END = "  # <<< codecoremorph: ported modules <<<"
TESTS_BEGIN = "# >>> codecoremorph: translated tests >>>"
TESTS_END = "# <<< codecoremorph: translated tests <<<"
SANITIZER_FLAGS = "-fsanitize=address,undefined"


def library(crate: str) -> str:
    return f"{crate}_c"


def top_cmake(crate: str, standard: str) -> str:
    """The top-level CMake file: the C library and its tests, sanitizers on request."""
    number = standard.removeprefix("c")
    return (
        f"{TOP_MARK}: the C that replaces\n"
        f"# the crate {crate}, under c/. During the port Cargo links it (build.rs); afterwards\n"
        "# it is the project's build.\n"
        "cmake_minimum_required(VERSION 3.16)\n"
        f"project({crate} C)\n"
        f"set(CMAKE_C_STANDARD {number})\n"
        "set(CMAKE_C_STANDARD_REQUIRED ON)\n"
        "set(CMAKE_C_EXTENSIONS OFF)\n"
        "set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)\n"
        f'option({SANITIZE} "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)\n'
        f"if({SANITIZE})\n"
        f"  add_compile_options({SANITIZER_FLAGS} -fno-sanitize-recover=undefined\n"
        "                      -fno-omit-frame-pointer)\n"
        f"  add_link_options({SANITIZER_FLAGS})\n"
        "endif()\n"
        "enable_testing()\n"
        "add_subdirectory(c)\n"
    )


def c_cmake(crate: str, sources: Iterable[str]) -> str:
    """``c/CMakeLists.txt``: the library of the ported modules, and the tests once there are
    some."""
    listed = "".join(f"  {s}\n" for s in sorted(set(sources)))
    lib = library(crate)
    return (
        f"# The C library the modules of the crate {crate} move into (CodeCoreMorph, D-75).\n"
        f"add_library({lib} STATIC\n"
        f"{SOURCES_BEGIN}\n"
        f"{listed}"
        f"{SOURCES_END}\n"
        ")\n"
        f"target_include_directories({lib} PUBLIC include)\n"
        f"target_compile_options({lib} PRIVATE -Wall -Wextra)\n"
        f"target_link_libraries({lib} PUBLIC m)\n"
        "if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt)\n"
        "  add_subdirectory(tests)\n"
        "endif()\n"
    )


_SOURCES = re.compile(
    rf"(?ms)^{re.escape(SOURCES_BEGIN)}[ \t]*\r?\n(.*?)^{re.escape(SOURCES_END)}[ \t]*$"
)


def library_sources(text: str) -> list[str]:
    match = _SOURCES.search(text)
    if match is None:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def with_source(text: str, source: str) -> str | None:
    """``c/CMakeLists.txt`` compiling ``source`` (relative to ``c/``) too; None when its marked
    list is gone."""
    match = _SOURCES.search(text)
    if match is None:
        return None
    sources = library_sources(text)
    if source in sources:
        return text
    listed = "".join(f"  {s}\n" for s in sorted({*sources, source}))
    return text[: match.start(1)] + listed + text[match.end(1) :]


def tests_cmake(crate: str, programs: Mapping[str, tuple[str, str]]) -> str:
    """``c/tests/CMakeLists.txt``: one program per translated test file (``program →
    (file, prefix)``), each of its cases a CTest test named ``<prefix>::<case>``, read from
    the file's ``CCM_RUN`` lines when CMake configures (D-75)."""
    lines = [
        "# The tests translated from Rust (CodeCoreMorph, D-75): one program per Rust test file,",
        "# over codecoremorph_tap.h. Each case is a CTest test named as its Rust test, read from",
        "# the file's CCM_RUN lines, so a new case needs no change here.",
        "# CCM_SOURCE_DIR and CCM_BINARY_DIR are the project's directories, for data files and",
        "# programs.",
        "function(codecoremorph_tap_tests program file prefix)",
        "  add_executable(${program} ${file})",
        f"  target_link_libraries(${{program}} PRIVATE {library(crate)})",
        "  target_include_directories(${program} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})",
        "  target_compile_definitions(${program} PRIVATE",
        '    "CCM_SOURCE_DIR=\\"${CMAKE_SOURCE_DIR}\\""',
        '    "CCM_BINARY_DIR=\\"${CMAKE_BINARY_DIR}\\"")',
        "  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${file})",
        '  file(STRINGS ${file} runs REGEX "^[ \\t]*CCM_RUN[ \\t]*\\\\(")',
        "  foreach(run IN LISTS runs)",
        '    string(REGEX REPLACE "^[ \\t]*CCM_RUN[ \\t]*\\\\([ \\t]*([A-Za-z_][A-Za-z0-9_]*).*"',
        '           "\\\\1" case "${run}")',
        "    add_test(NAME ${prefix}::${case} COMMAND ${program} ${case})",
        "  endforeach()",
        "endfunction()",
        "",
        TESTS_BEGIN,
    ]
    for program, (file, prefix) in sorted(programs.items()):
        lines.append(f"codecoremorph_tap_tests({program} {file} {prefix})")
    lines.append(TESTS_END)
    return "\n".join(lines) + "\n"


_TESTS_CALL = re.compile(r"^\s*codecoremorph_tap_tests\(\s*(\S+)\s+(\S+)\s+(\S+)\s*\)", re.M)


def test_programs(text: str) -> dict[str, tuple[str, str]]:
    """The translated test programs ``c/tests/CMakeLists.txt`` builds: ``program → (file,
    prefix)``."""
    return {m.group(1): (m.group(2), m.group(3)) for m in _TESTS_CALL.finditer(text)}


_CASE_RUN = re.compile(r"^[ \t]*CCM_RUN[ \t]*\([ \t]*([A-Za-z_]\w*)", re.M)


def test_ids(cmake_text: str, files: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """``ctest:<prefix>::<case>`` for each case of each translated test program, with the
    file (relative to ``c/tests``) and the case's function: ``files`` maps those files to
    their text."""
    found: dict[str, tuple[str, str]] = {}
    for _, (file, prefix) in sorted(test_programs(cmake_text).items()):
        text = files.get(file)
        if text is None:
            continue
        for match in _CASE_RUN.finditer(text):
            found[f"ctest:{prefix}::{match.group(1)}"] = (file, match.group(1))
    return found


def cargo_link(crate: str) -> str:
    return (
        "# The C libraries the crate links while it is ported to C (CodeCoreMorph, D-75), one\n"
        "# per line, as cargo:rustc-link-lib takes them; build.rs reads this file.\n"
        f"static={library(crate)}\n"
        "m\n"
    )


def linked_library(text: str) -> str | None:
    """The first static library ``c/cargo-link`` names: the CMake target Cargo needs."""
    for line in text.splitlines():
        name = line.strip().removeprefix("static=")
        if line.strip().startswith("static=") and re.fullmatch(r"[A-Za-z_]\w*", name):
            return name
    return None


def header_path(crate: str, stem: str) -> str:
    return f"{C_DIR}/include/{crate}/{stem}.h"


def source_path(stem: str) -> str:
    return f"{C_DIR}/src/{stem}.c"


def header_stub(crate: str, stem: str, module: str) -> str:
    guard = re.sub(r"\W", "_", f"{crate}_{stem}_h").upper()
    return (
        f"/* {crate}/{stem}.h - the module {module}, ported to C (CodeCoreMorph, D-75). */\n"
        f"#ifndef {guard}\n"
        f"#define {guard}\n"
        "\n"
        f"#endif /* {guard} */\n"
    )


def source_stub(crate: str, stem: str, module: str) -> str:
    return f'/* The module {module}, ported to C. */\n#include "{crate}/{stem}.h"\n'


def sys_path(module_file: str) -> str:
    """Where a façade's ``sys`` module goes: ``src/stats.rs`` and ``src/stats/mod.rs`` both
    declare ``mod sys;`` and find it at ``src/stats/sys.rs``."""
    directory, name = posixpath.split(module_file)
    if name == "mod.rs":
        return posixpath.join(directory, "sys.rs")
    return posixpath.join(directory, name.removesuffix(".rs"), "sys.rs")


def placeholder_sys(header: str) -> str:
    return (
        f"//! Written by bindgen from {header} before each build (CodeCoreMorph, D-75): the C\n"
        "//! this façade calls.\n"
    )


def configure_argv(source_dir: str, build_dir: str) -> list[str]:
    """CMake's configure of the C part, as the C adapter configures a CMake project (Ninja, a
    compile database, a debug build), with the sanitizers on."""
    return [
        "cmake",
        "-S",
        source_dir,
        "-B",
        build_dir,
        "-G",
        "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DCMAKE_BUILD_TYPE=Debug",
        f"-D{SANITIZE}=ON",
    ]


_REGEX_META = re.compile(r"([\\.+*?()|\[\]{}^$])")


def bindgen_argv(header: str, out: str, include_dir: str) -> list[str]:
    """bindgen over a ported module's header (sandbox paths): its items, and what they need
    from the headers it includes, with compile-time layout checks."""
    return [
        "bindgen",
        header,
        "--output",
        out,
        "--allowlist-file",
        _REGEX_META.sub(r"\\\1", header),
        "--no-doc-comments",
        "--merge-extern-blocks",
        "--sort-semantically",
        "--",
        "-x",
        "c",
        "-std=c11",
        "-I",
        include_dir,
    ]


def c_modules(manifest: str) -> dict[str, str]:
    """The ported modules a crate's manifest names: module file → its C header, both from the
    repository's root."""
    try:
        data = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        return {}
    node: object = data
    for key in ("package", "metadata", "codecoremorph", "c-modules"):
        if not isinstance(node, dict):
            return {}
        node = cast(dict[str, object], node).get(key)
    if not isinstance(node, dict):
        return {}
    return {str(k): v for k, v in cast(dict[str, object], node).items() if isinstance(v, str) and v}


def with_c_module(manifest: str, module_file: str, header: str) -> str:
    """The crate's manifest naming ``module_file``'s header too."""
    if c_modules(manifest).get(module_file) == header:
        return manifest
    newline = "\r\n" if "\r\n" in manifest else "\n"
    entry = f'"{module_file}" = "{header}"'
    heading = f"[{METADATA}]"
    lines = manifest.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == heading:
            lines.insert(i + 1, entry)
            return newline.join(lines) + newline
    text = manifest if manifest.endswith(("\n", "\r\n")) or not manifest else manifest + newline
    return f"{text}{newline}{heading}{newline}{entry}{newline}"


def without_c_modules(manifest: str) -> str:
    """The manifest without the c-modules table (nothing needs it once the Rust is gone)."""
    heading = f"[{METADATA}]"
    out: list[str] = []
    skipping = False
    for line in manifest.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == heading:
            skipping = True
            continue
        if skipping and stripped.startswith("["):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


TIDY_CHECKS = "-*,bugprone-*,clang-analyzer-*,-bugprone-easily-swappable-parameters"


def clang_tidy_argv(build_dir: str, files: Sequence[str]) -> list[str]:
    """clang-tidy's bug-finding checks over the C part, with the build's flags (``-Wall
    -Wextra``, whose warnings it reports too)."""
    return ["clang-tidy", "-p", build_dir, "--quiet", f"--checks={TIDY_CHECKS}", *files]


# --------------------------------------------------------------------------- the scaffolds

SCAFFOLDS = frozenset({"c-module", "c-program", "c-tests", "c-retire"})
PROGRAMS_BEGIN = "# >>> codecoremorph: programs >>>"
PROGRAMS_END = "# <<< codecoremorph: programs <<<"


class _Writer:
    """Writes files under the worktree with LF endings, remembering what it changed."""

    def __init__(self, worktree: Path):
        self.worktree = worktree
        self.changed: list[str] = []

    def read(self, rel: str) -> str | None:
        path = self.worktree / rel
        if not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="replace")

    def write(self, rel: str, text: str) -> None:
        path = self.worktree / rel
        data = text.encode("utf-8")
        if path.is_file() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.changed.append(rel)

    def create(self, rel: str, text: str) -> None:
        if self.read(rel) is None:
            self.write(rel, text)

    def delete(self, rel: str) -> None:
        path = self.worktree / rel
        if path.is_file():
            path.unlink()
            self.changed.append(rel)


def _option(options: Mapping[str, str], name: str) -> str:
    value = options.get(name, "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ConfigError(
            f"the Rust → C scaffold needs a {name} option (a C identifier), got {value!r}",
            hint="The planner sets it from the unit's port; re-plan the run.",
        )
    return value


def _rust_file(files: Sequence[str]) -> str:
    """The unit's Rust file: a module's unit also has the crate root, for its attributes, so the
    root is the unit's file only when it has no other (the root's own unit)."""
    rust = [f for f in files if f.endswith(".rs")]
    found = next((f for f in rust if not f.endswith("src/lib.rs")), None) or next(iter(rust), None)
    if found is None:
        raise ConfigError(
            "a Rust → C scaffold found no Rust file among the unit's files",
            hint="The planner lists the module, program or test file; re-plan the run.",
        )
    return found


def _in(package: str, rel: str) -> str:
    """``rel`` inside a package's directory (``.`` for the repository's root)."""
    return rel if package in ("", ".") else f"{package}/{rel}"


def _package_name(manifest: str) -> str | None:
    try:
        data = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        return None
    package = data.get("package")
    if not isinstance(package, dict):
        return None
    name = cast(dict[str, object], package).get("name")
    return name if isinstance(name, str) else None


def package_of(worktree: Path, rel: str) -> str:
    """The directory of the package a worktree file belongs to (the deepest one holding it),
    ``.`` for the root package."""
    found = "."
    for package in find_packages(worktree):
        if package != "." and rel.startswith(f"{package}/") and len(package) > len(found):
            found = package
    return found


def library_package(worktree: Path, crate: str) -> str:
    """The directory of the package whose crate is ``crate``: it gets the build script that
    links the C, and its manifest names the façades. ``.`` when no manifest names it."""
    for package in find_packages(worktree):
        manifest = worktree / package / "Cargo.toml"
        name = _package_name(manifest.read_bytes().decode("utf-8", errors="replace"))
        if name is not None and name.replace("-", "_") == crate:
            return package
    return "."


def inner_path(package: str, rel: str) -> str:
    """A worktree path relative to its package's directory."""
    return rel if package in ("", ".") else rel.removeprefix(f"{package}/")


def _base(out: _Writer, crate: str, standard: str) -> str:
    """The build every unit needs: the top-level and c/ CMake files, the link list, the
    library package's build script and the symbol map. Returns c/CMakeLists.txt's text."""
    out.create(TOP_CMAKE, top_cmake(crate, standard))
    out.create(C_CMAKE, c_cmake(crate, []))
    out.create(CARGO_LINK, cargo_link(crate))
    out.create(_in(library_package(out.worktree, crate), BUILD_SCRIPT), RUST_C_LINK_SCRIPT)
    out.create(SYMBOL_MAP, SYMBOL_MAP_TEXT)
    return out.read(C_CMAKE) or ""


def _add_source(out: _Writer, cmake: str, source: str) -> str:
    updated = with_source(cmake, source)
    if updated is None:
        raise ConfigError(
            f"{C_CMAKE} lost its marked list of ported modules ({SOURCES_BEGIN.strip()})",
            hint="Put the markers back around the library's sources.",
        )
    out.write(C_CMAKE, updated)
    return updated


def _module(out: _Writer, options: Mapping[str, str], files: Sequence[str], standard: str) -> None:
    crate, stem = _option(options, "crate"), _option(options, "module")
    module_file = _rust_file(files)
    package = package_of(out.worktree, module_file)
    module = module_path(crate, inner_path(package, module_file)) or module_file
    cmake = _base(out, crate, standard)
    header = header_path(crate, stem)
    out.create(header, header_stub(crate, stem, module))
    out.create(source_path(stem), source_stub(crate, stem, module))
    _add_source(out, cmake, f"src/{stem}.c")
    manifest_path = _in(package, "Cargo.toml")
    manifest = out.read(manifest_path)
    if manifest is not None:
        out.write(manifest_path, with_c_module(manifest, module_file, header))
    out.create(sys_path(module_file), placeholder_sys(header))


def _program(out: _Writer, options: Mapping[str, str], files: Sequence[str], standard: str) -> None:
    """A program: its code a function of the library, ``<crate>_<bin>_main``, which the Rust
    program calls until the retirement, and a CMake program whose ``main`` calls it."""
    crate, stem = _option(options, "crate"), _option(options, "module")
    program = options.get("bin") or stem
    rust_file = _rust_file(files)
    cmake = _base(out, crate, standard)
    entry = f"{crate}_{stem}_main"
    header = header_path(crate, stem)
    out.create(
        header,
        header_stub(crate, stem, f"the program {program} ({rust_file})").replace(
            "\n#endif", f"\nint {entry}(int argc, char **argv);\n\n#endif", 1
        ),
    )
    out.create(source_path(stem), source_stub(crate, stem, f"the program {program}"))
    out.create(
        f"{C_DIR}/src/{stem}_entry.c",
        f'/* The program {program} (CodeCoreMorph, D-75). */\n#include "{crate}/{stem}.h"\n\n'
        f"int main(int argc, char **argv)\n{{\n    return {entry}(argc, argv);\n}}\n",
    )
    cmake = _add_source(out, cmake, f"src/{stem}.c")
    block = (
        f"{PROGRAMS_BEGIN}\n"
        f"add_executable({program} src/{stem}_entry.c)\n"
        f"target_link_libraries({program} PRIVATE {library(crate)})\n"
        f"{PROGRAMS_END}\n"
    )
    if f"add_executable({program} " not in cmake:
        out.write(C_CMAKE, cmake.rstrip("\n") + "\n" + block)


def test_program(stem: str) -> str:
    """A translated test file's program: ``test_<stem>``, or the stem itself when it already
    starts so (``tests/test_htab.rs`` gives ``test_htab``)."""
    stem = re.sub(r"\W", "_", stem)
    return stem if stem.startswith("test_") else f"test_{stem}"


def test_names(rust_file: str, package: str, test_ids: Sequence[str]) -> tuple[str, str, str]:
    """``(program, file, prefix)`` of a Rust test file's translation (``rust_file`` relative to
    its package): ``tests/api.rs`` gives ``test_api``, a module's ``#[cfg(test)]`` tests
    ``test_<module>``; the prefix is what its tests' IDs share, so each case's CTest name is
    its Rust test's ID (D-75)."""
    parts = PurePosixPath(rust_file).parts
    if parts and parts[0] == "tests":
        stem = PurePosixPath(parts[1]).stem
    else:
        inner = list(parts[1:]) if parts and parts[0] == "src" else list(parts)
        if inner and inner[-1] == "mod.rs":
            inner = inner[:-1]
        elif inner:
            inner[-1] = PurePosixPath(inner[-1]).stem
        # The crate root's tests are named after the crate, as the planner names them.
        stem = package if inner in (["lib"], []) else "_".join(inner)
    program = test_program(stem)
    paths = [i.removeprefix("nextest:").split("::") for i in test_ids]
    common: list[str] = []
    for segments in zip(*[p[:-1] for p in paths], strict=False):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    return program, f"{program}.c", "::".join(common) or package


def _tests(out: _Writer, options: Mapping[str, str], files: Sequence[str], standard: str) -> None:
    crate = _option(options, "crate")
    rust_file = _rust_file(files)
    test_ids = [t for t in options.get("tests", "").split(",") if t]
    package = package_of(out.worktree, rust_file)
    program, file, prefix = test_names(inner_path(package, rust_file), crate, test_ids)
    named = options.get("module", "")
    if re.fullmatch(r"test_\w+", named):  # the planner's name for the program wins
        program, file = named, f"{named}.c"
    _base(out, crate, standard)
    out.create(TEST_MAP, TEST_MAP_TEXT)
    out.write(f"{C_DIR}/tests/{HELPER}", HELPER_TEXT)
    programs = test_programs(out.read(TESTS_CMAKE) or "")
    programs[program] = (file, prefix)
    out.write(TESTS_CMAKE, tests_cmake(crate, programs))
    out.create(
        f"{C_DIR}/tests/{file}",
        f"/* The tests of {rust_file}, translated from Rust (CodeCoreMorph, D-75). */\n"
        f'#include "{HELPER}"\n\n'
        "int main(int argc, char **argv)\n{\n    CCM_SELECT(argc, argv);\n"
        "    return ccm_done();\n}\n",
    )


def _retire(out: _Writer, files: Sequence[str]) -> None:
    """The Rust goes: each package's manifest, build script and Rust files, each workspace's
    (or lone package's) manifest, lock and toolchain files, and the link list. The packages
    and workspaces are those whose manifests the unit names (``files``), so an excluded one
    stays; a unit that names none takes every one. The CMake build, the C and the maps stay,
    and so do files Cargo doesn't own beside them (a workspace's ``.gitignore``, an earlier
    port's maps)."""
    named = {posixpath.dirname(f) or "." for f in files if posixpath.basename(f) == "Cargo.toml"}
    packages = [p for p in find_packages(out.worktree) if not named or p in named]
    roots = [r for r in find_crates(out.worktree) if not named or r in named]
    for rel in files:
        if rel.endswith(".rs"):
            out.delete(rel)
    for package in packages:
        for rel in ("Cargo.toml", "Cargo.lock", BUILD_SCRIPT):
            out.delete(_in(package, rel))
        for directory in ("src", "tests", "benches", "examples"):
            root = out.worktree / _in(package, directory)
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.rs")):
                out.delete(path.relative_to(out.worktree).as_posix())
            for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
                if not any(path.iterdir()):
                    path.rmdir()
            if not any(root.iterdir()):
                root.rmdir()
    for root in roots:
        for rel in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "rust-toolchain",
                    ".cargo/config.toml", ".cargo/config"):  # fmt: skip
            out.delete(_in(root, rel))
    out.delete(CARGO_LINK)
    for package in sorted({*packages, *roots}, key=len, reverse=True):
        directory = out.worktree / package
        if package not in ("", ".") and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def scaffold(
    worktree: Path, tool: str, options: Mapping[str, str], files: Sequence[str], standard: str
) -> list[str]:
    """Run one of the Rust → C scaffolds; the worktree paths it changed."""
    out = _Writer(worktree)
    if tool == "c-module":
        _module(out, options, files, standard)
    elif tool == "c-program":
        _program(out, options, files, standard)
    elif tool == "c-tests":
        _tests(out, options, files, standard)
    elif tool == "c-retire":
        _retire(out, files)
    else:
        raise ConfigError(f"no Rust → C scaffold {tool!r}", hint=f"Available: {sorted(SCAFFOLDS)}")
    return out.changed


SYMBOL_MAP_TEXT = """\
# The Rust items this port moved to C, and the C that took them over (CodeCoreMorph, D-75).
# One entry per public item of a ported module:
#
#   [[symbol]]
#   rust = "stats::stats::mean"
#   c = "stats_mean"          # or a list of names
#   note = "..."              # optional: what C couldn't keep, or why c is empty
"""

TEST_MAP_TEXT = """\
# The Rust tests this port translated to C, and the C tests that took them over (CodeCoreMorph,
# D-75). One entry per Rust test; a translated test is a CTest test named as the Rust test:
#
#   [[test]]
#   rust = "nextest:stats::stats::tests::mean_of_three"
#   c = "ctest:stats::stats::tests::mean_of_three"
#   note = "..."              # optional
"""
