"""The C++ side of a Java → C++ port (SPEC §11.12, D-92): what the C++ adapter, as the port's
target, writes into the project, and the Java adapter, as its source, reads back.

The layout is a C# port's (:mod:`codecoremorph.adapters.cpp.csharpports`): the C++ under
``cpp/``, a static library ``<name>_cpp`` of the ported types, which the retirement keeps, and
during the port a shared library ``<name>_native`` of ``extern "C"`` exports over it, which the
Java façades load through JNI, with the sanitizers when ``CODECOREMORPH_SANITIZE`` is on. What
differs is the runtime CodeCoreMorph ships (Java's exceptions and semantics,
``java_runtime``), the bridge library's JNI glue (the fixed ``codecoremorph_jni.c`` and the
``codecoremorph_jni.g.c`` generated from the bridge headers at configure,
:mod:`codecoremorph.adapters.cpp.jniglue`), the JDK's JNI headers, and the Java support in the
façade module (:mod:`codecoremorph.adapters.java.facades`).

The translated tests are GoogleTest programs under ``cpp/tests``, one per Java test file. The
retirement deletes the Java, its build, the façades' support and the bridge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path

from codecoremorph.adapters.cpp import csharpports as shared
from codecoremorph.adapters.cpp.jniglue import GENERATED_FILE as GLUE_FILE
from codecoremorph.adapters.cpp.jniglue import c_glue
from codecoremorph.adapters.csharp.ports import Writer, identifier_option
from codecoremorph.adapters.java import facades
from codecoremorph.adapters.java.jni import Bridge
from codecoremorph.core.errors import ConfigError

# The layout a C# port has (D-78), under the same names.
CPP_DIR = shared.CPP_DIR
TOP_CMAKE = shared.TOP_CMAKE
CPP_CMAKE = shared.CPP_CMAKE
BRIDGE_DIR = shared.BRIDGE_DIR
INCLUDE_DIR = shared.INCLUDE_DIR
SOURCE_DIR = shared.SOURCE_DIR
TESTS_DIR = shared.TESTS_DIR
TESTS_CMAKE = shared.TESTS_CMAKE
SYMBOL_MAP = shared.SYMBOL_MAP
TEST_MAP = shared.TEST_MAP
SANITIZE = shared.SANITIZE
SANITIZER_FLAGS = shared.SANITIZER_FLAGS
ASAN_OPTIONS = shared.ASAN_OPTIONS
UBSAN_OPTIONS = shared.UBSAN_OPTIONS
native_library = shared.native_library
cpp_library = shared.cpp_library
native_name = shared.native_name
listed = shared.listed
with_listed = shared.with_listed
test_programs = shared.test_programs
without_bridge = shared.without_bridge
test_environment = shared.test_environment
raw_ownership = shared.raw_ownership
clang_tidy_argv = shared.clang_tidy_argv
in_bridge = shared.in_bridge
BRIDGE_ONLY_CHECKS = shared.BRIDGE_ONLY_CHECKS

TOP_MARK = "# Written by CodeCoreMorph for a Java → C++ port (D-92)"
SCAFFOLDS = frozenset({"cpp-java-type", "cpp-java-tests", "cpp-java-retire"})
GLUE = f"{BRIDGE_DIR}/{GLUE_FILE}"

# The files of java_runtime (and the Java side's C ABI) and where the scaffold puts them. The
# first two stay after the retirement.
RUNTIME_FILES = {
    "codecoremorph_exceptions.hpp": f"{INCLUDE_DIR}/codecoremorph_exceptions.hpp",
    "codecoremorph_java.hpp": f"{INCLUDE_DIR}/codecoremorph_java.hpp",
    "codecoremorph_bridge.h": f"{BRIDGE_DIR}/codecoremorph_bridge.h",
    "codecoremorph_bridge.hpp": f"{BRIDGE_DIR}/codecoremorph_bridge.hpp",
    "codecoremorph_bridge.cpp": f"{BRIDGE_DIR}/codecoremorph_bridge.cpp",
    "codecoremorph_jni.h": f"{BRIDGE_DIR}/codecoremorph_jni.h",
    "codecoremorph_jni.c": f"{BRIDGE_DIR}/codecoremorph_jni.c",
}
# The bridge library's own sources, before the units' marked list.
_BRIDGE_FIXED = (
    "bridge/codecoremorph_bridge.cpp",
    "bridge/codecoremorph_jni.c",
    f"bridge/{GLUE_FILE}",
)
_HEADERS_LEFT_OUT = frozenset({"codecoremorph_bridge.h", "codecoremorph_jni.h"})


def runtime_text(file_name: str) -> str:
    """A runtime file as CodeCoreMorph ships it: the C++ runtime's, or the Java side's C ABI."""
    if file_name == facades.BRIDGE_RUNTIME_HEADER:
        return facades.runtime_text(file_name)
    package = resources.files("codecoremorph.adapters.cpp.java_runtime")
    return (package / file_name).read_text(encoding="utf-8")


def is_port(worktree: Path) -> bool:
    """Whether the project has a Java → C++ port's top-level CMake file."""
    top = worktree / TOP_CMAKE
    if not top.is_file():
        return False
    return TOP_MARK in top.read_bytes().decode("utf-8", errors="replace")


def is_runtime(rel: str) -> bool:
    """Whether ``rel`` is a file of the runtime CodeCoreMorph ships or generates, which no unit
    edits."""
    return rel in RUNTIME_FILES.values() or rel == GLUE


def bridge_headers(worktree: Path) -> list[str]:
    """The units' bridge headers (worktree paths), the runtime's own left out."""
    directory = worktree / BRIDGE_DIR
    if not directory.is_dir():
        return []
    return sorted(
        f"{BRIDGE_DIR}/{p.name}"
        for p in directory.glob("*.h")
        if p.is_file() and p.name not in _HEADERS_LEFT_OUT
    )


def port_sources(worktree: Path) -> list[str]:
    """The port's own C++ (the runtime and the generated glue left out)."""
    return [f for f in shared.port_sources(worktree) if not is_runtime(f)]


def top_cmake(name: str, module: str, standard: str) -> str:
    """The top-level CMake file: the C++ under ``cpp/``, sanitizers on request."""
    text = shared.top_cmake(name, module, standard)
    _, _, rest = text.partition("cmake_minimum_required")
    return (
        f"{TOP_MARK}: the C++ that replaces\n"
        f"# the Java library {module}, under cpp/. During the port the Java façades call it\n"
        "# through the bridge library, over JNI; afterwards it is the project's build.\n"
        f"cmake_minimum_required{rest}"
    )


def _sources(items: Iterable[str]) -> str:
    return "".join(f"  {s}\n" for s in sorted(set(items)))


def cpp_cmake(name: str, module: str, sources: Iterable[str], bridge: Iterable[str]) -> str:
    """``cpp/CMakeLists.txt``: the library of the ported types, the bridge over it with the JNI
    glue and the JDK's JNI headers, and the tests once there are some."""
    lib, native = cpp_library(name), native_library(name)
    fixed = "".join(f"  {s}\n" for s in _BRIDGE_FIXED)
    return (
        f"# The C++ the types of the Java library {module} move into (CodeCoreMorph, D-92): the\n"
        f"# library {lib}, which stays, and during the port the bridge {native}, which the Java\n"
        "# façades load through JNI.\n"
        f"add_library({lib} STATIC\n"
        f"{shared.SOURCES_BEGIN}\n"
        f"{_sources(sources)}"
        f"{shared.SOURCES_END}\n"
        ")\n"
        f"target_include_directories({lib} PUBLIC include src)\n"
        f"target_compile_options({lib} PRIVATE -Wall -Wextra)\n"
        f"{shared.BRIDGE_BEGIN}\n"
        "# The JDK's JNI headers, from the JDK the Java builds with.\n"
        'find_path(CCM_JNI_INCLUDE jni.h HINTS "$ENV{JAVA_HOME}/include"\n'
        "          REQUIRED NO_DEFAULT_PATH)\n"
        'find_path(CCM_JNI_MD_INCLUDE jni_md.h HINTS "${CCM_JNI_INCLUDE}/linux"\n'
        '          "${CCM_JNI_INCLUDE}/darwin" "${CCM_JNI_INCLUDE}/win32"\n'
        "          REQUIRED NO_DEFAULT_PATH)\n"
        f"add_library({native} SHARED\n"
        f"{fixed}"
        f"{shared.BRIDGE_SOURCES_BEGIN}\n"
        f"{_sources(bridge)}"
        f"{shared.BRIDGE_SOURCES_END}\n"
        ")\n"
        f"target_include_directories({native} PRIVATE bridge ${{CCM_JNI_INCLUDE}} "
        "${CCM_JNI_MD_INCLUDE})\n"
        f"target_link_libraries({native} PRIVATE {lib})\n"
        f"target_compile_options({native} PRIVATE -Wall -Wextra)\n"
        "# The JVM the Java tests run in preloads the shared ASan runtime (D-92).\n"
        f"if({SANITIZE})\n"
        f"  target_link_options({native} PRIVATE -shared-libasan)\n"
        "endif()\n"
        f"{shared.BRIDGE_END}\n"
        "if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt)\n"
        "  add_subdirectory(tests)\n"
        "endif()\n"
    )


def glue_text(bridge: Bridge) -> str:
    """``cpp/bridge/codecoremorph_jni.g.c`` for the bridge headers' exports."""
    return c_glue(bridge)


# --------------------------------------------------------------------------- scaffolds


def _identifier(options: Mapping[str, str], name: str, what: str) -> str:
    return identifier_option(options, name, what, "Java → C++")


def header_stub(name: str, unit: str, types: str) -> str:
    guard = f"{name}_{unit}_hpp".upper()
    return (
        f"// The C++ of {types}, ported from Java (CodeCoreMorph, D-92).\n"
        f"#ifndef {guard}\n#define {guard}\n\n#endif\n"
    )


def source_stub(unit: str, types: str) -> str:
    return (
        f'// The C++ of {types}, ported from Java (CodeCoreMorph, D-92).\n#include "{unit}.hpp"\n'
    )


def bridge_header_stub(name: str, unit: str, types: str) -> str:
    guard = f"{name}_bridge_{unit}_h".upper()
    return (
        f"/* The bridge of {types}: the exports its Java façade calls (CodeCoreMorph, D-92). */\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        f'#include "{facades.BRIDGE_RUNTIME_HEADER}"\n\n'
        '#ifdef __cplusplus\nextern "C" {\n#endif\n\n'
        "#ifdef __cplusplus\n}\n#endif\n\n#endif\n"
    )


def bridge_source_stub(unit: str, types: str) -> str:
    return (
        f"// The bridge of {types} (CodeCoreMorph, D-92).\n"
        f'#include "{unit}.h"\n\n'
        '#include "codecoremorph_bridge.hpp"\n'
        f'#include "{unit}.hpp"\n'
    )


def _base(out: Writer, name: str, module: str, standard: str) -> str:
    """The CMake files, the runtime, the glue and the Java support, written once;
    ``cpp/CMakeLists.txt``'s text."""
    out.create(TOP_CMAKE, top_cmake(name, module, standard))
    out.create(CPP_CMAKE, cpp_cmake(name, module, [], []))
    for file_name, rel in RUNTIME_FILES.items():
        out.create(rel, runtime_text(file_name))
    out.create(GLUE, glue_text(Bridge()))
    facades.write_facade_support(out, module, native_library(name))
    out.create(SYMBOL_MAP, SYMBOL_MAP_TEXT)
    return out.read(CPP_CMAKE) or ""


def _add(out: Writer, cmake: str, source: str, *, bridge: bool) -> str:
    updated = with_listed(cmake, source, bridge=bridge)
    if updated is None:
        raise ConfigError(
            f"{CPP_CMAKE} lost its marked list of the "
            f"{'bridge sources' if bridge else 'ported types'}",
            hint="Put the list back (the scaffold's markers), or add the source by hand.",
        )
    if updated != cmake:
        out.write(CPP_CMAKE, updated)
    return updated


def _type(out: Writer, options: Mapping[str, str], standard: str) -> None:
    """A unit of types: the CMake files, runtime and Java support (the first unit), the unit's
    C++ and bridge files, and its sources in the libraries. The options are the unit's port's:
    ``crate`` (the C++ libraries' stem), ``module`` (the unit's files' stem), ``build_target``
    (the façade module's POM) and ``types`` (the Java types it ports)."""
    name = _identifier(options, "crate", "the C++ libraries' name")
    unit = _identifier(options, "module", "the unit's name")
    module = facades.module_option(out.worktree, options, "Java → C++")
    types = facades.type_names(options.get("types", "")) or unit
    cmake = _base(out, name, module, standard)
    out.create(f"{SOURCE_DIR}/{unit}.hpp", header_stub(name, unit, types))
    out.create(f"{SOURCE_DIR}/{unit}.cpp", source_stub(unit, types))
    out.create(f"{BRIDGE_DIR}/{unit}.h", bridge_header_stub(name, unit, types))
    out.create(f"{BRIDGE_DIR}/{unit}.cpp", bridge_source_stub(unit, types))
    cmake = _add(out, cmake, f"src/{unit}.cpp", bridge=False)
    _add(out, cmake, f"bridge/{unit}.cpp", bridge=True)


def tests_cmake(name: str, test_programs: Iterable[str]) -> str:
    """``cpp/tests/CMakeLists.txt``: a GoogleTest program per translated Java test file."""
    return shared.googletest_cmake(name, test_programs, "Java", "D-92")


def tests_stub(source: str | None) -> str:
    what = source or "a Java test file (the unit's note names it)"
    return (
        f"// The tests of {what}, translated from Java (CodeCoreMorph, D-92): a GoogleTest test\n"
        f"// per Java test case, each mapped in {TEST_MAP}.\n"
        "#include <gtest/gtest.h>\n"
    )


def _tests(out: Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """A Java test file's GoogleTest program ``cpp/tests/<module>.cpp`` and its CTest test, and
    the test map. The C++ the tests use is there already: the type units wrote it."""
    name = _identifier(options, "crate", "the C++ libraries' name")
    program = _identifier(options, "module", "the test program's name")
    if out.read(CPP_CMAKE) is None:
        raise ConfigError(
            "a Java test file is translated to GoogleTest after the types it tests are C++, and "
            f"{CPP_CMAKE} isn't there",
            hint="Plan the port's type units first (port_java_type_to_cpp).",
        )
    source = next((f for f in files if f.endswith(".java")), None)
    out.create(TEST_MAP, TEST_MAP_TEXT)
    present = test_programs(out.read(TESTS_CMAKE) or "")
    out.write(TESTS_CMAKE, tests_cmake(name, [*present, program]))
    out.create(f"{TESTS_DIR}/{program}.cpp", tests_stub(source))


def _retire(out: Writer, files: Sequence[str]) -> None:
    """Delete the Java: the unit's Java files and POMs, Maven's wrapper, the façades' support,
    and the bridge with its block in ``cpp/CMakeLists.txt``."""
    facades.retire_java(out, files)
    out.delete_tree(BRIDGE_DIR)
    cmake = out.read(CPP_CMAKE)
    if cmake is not None:
        out.write(CPP_CMAKE, without_bridge(cmake))
    out.prune()


def scaffold(
    worktree: Path, tool: str, options: Mapping[str, str], files: Sequence[str], standard: str
) -> list[str]:
    """Run one of the Java → C++ scaffolds; the worktree paths it changed."""
    out = Writer(worktree)
    if tool == "cpp-java-type":
        _type(out, options, standard)
    elif tool == "cpp-java-tests":
        _tests(out, options, files)
    elif tool == "cpp-java-retire":
        _retire(out, files)
    else:
        raise ConfigError(
            f"no Java → C++ scaffold {tool!r}", hint=f"Available: {sorted(SCAFFOLDS)}"
        )
    return out.changed


def written_glue(worktree: Path, text: str) -> bool:
    """Write the generated glue when it changed; whether it did."""
    path = worktree / GLUE
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


SYMBOL_MAP_TEXT = """\
# The Java symbols this port moved to C++, and the C++ that took them over (CodeCoreMorph, D-92).
# One entry per public member of a ported type:
#
#   [[symbol]]
#   java = "java:shop.Cart#add(java.lang.String,int)"
#   cpp = "shop::Cart::add"        # or a list of names
#   note = "..."                   # optional: what C++ couldn't keep, or why cpp is empty
"""

TEST_MAP_TEXT = """\
# The Java tests this port translated to C++, and the GoogleTest tests that took them over
# (CodeCoreMorph, D-92). One entry per Java test:
#
#   [[test]]
#   java = "junit:shop.CartTest#totalsItsItems"
#   cpp = "gtest:CartTest.totalsItsItems"
#   note = "..."                   # optional
"""
