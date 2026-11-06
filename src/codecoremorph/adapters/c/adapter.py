"""The C adapter (D-68): C projects built with Make or CMake.

C shares the C++ adapter's machinery, the libclang index, the builds and their diagnostics,
CTest and GoogleTest, the C declaration checks; it is a language of its own because rule packs
belong to one language, so the C++ modernization rules never apply to C. C's port to Rust is
the ``c_to_rust`` pack's (§11.7). C has no modernization pack.
"""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c.detect import C_STANDARDS, CMAKE_C_STANDARD, normalized
from codecoremorph.adapters.c.detect import detect as detect_project
from codecoremorph.adapters.cpp.adapter import CppAdapter
from codecoremorph.adapters.cpp.detect import masked_comments
from codecoremorph.adapters.cpp.indexing import build_files
from codecoremorph.adapters.cpp.make import BUILD_SYSTEM as MAKE
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    AdapterCapabilities,
    Boundary,
    FileRole,
    ProjectDetection,
    RuleSite,
    SymbolKind,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore, takes_variable_arguments


class CAdapter(CppAdapter):
    """C projects built with Make or CMake."""

    api_version = "1"
    language = "c"
    version = "1"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            parse_check=True,
            format=True,
            # Characterization tests of a C project are Rust tests in its port (D-68); they
            # aren't registered in the C build.
            register_test_files=False,
            extract_assertions=True,
            detect_disabled_tests=True,
            c_declarations=True,
            codemod_tools=["clang-tidy"],
            queries=["c_abi_definitions", "test_cases"],
            standards=list(C_STANDARDS),
        )

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        """Make or CMake, the standard the build files set (or one guessed from the code) and
        the layout (FR-1.4)."""
        return detect_project(root, files)

    def project_standard(self, ctx: AdapterContext) -> str | None:
        """The oldest C standard the build files set: ``-std=`` options in a Make build,
        ``C_STANDARD`` in CMake files (excluded paths don't count)."""
        if ctx.lang.build_system == MAKE:
            return super().project_standard(ctx)
        found: list[str] = []
        for rel in build_files(ctx.worktree, PathMatcher(ctx.lang.exclude)):
            try:
                text = TextFile.decode((ctx.worktree / rel).read_bytes()).text
            except BinaryFileError:
                continue
            found += [f"c{n}" for n in CMAKE_C_STANDARD.findall(masked_comments(text))]
        return self._lowest_flag_standard(found)

    def _lowest_flag_standard(self, found: list[str]) -> str | None:
        return super()._lowest_flag_standard([normalized(s) for s in found])

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        """``c_abi_definitions`` in C: every function and variable a source file defines that
        another file declares or uses, whether through a header or its own ``extern``
        declaration. All of C has C linkage, so that is the C interface a port to Rust keeps
        (D-68)."""
        if name not in ("c_abi_definitions", "test_cases"):
            return super().query(ctx, name, files)
        index_path = ctx.cache_dir / INDEX_FILE_NAME
        if not index_path.is_file():
            raise ConfigError(f"the {name} query needs an index", hint="Index the project first.")
        if name == "test_cases":
            return test_cases(index_path, files)
        return c_definitions(index_path, files)


def test_cases(index_path: Path, files: list[str]) -> list[RuleSite]:
    """The tests in the files among ``files``, by test ID: what a port translates (D-60). A C
    test's cases exist only when it runs (TAP points, automake's tests), so they come from the
    last test run: each test the build ran belongs to the program it runs, at the file that
    defines that program's ``main`` (or its first file)."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        targets = {t.name: t for t in store.build_targets()}
        entries = store.test_entries()
        mains = {
            s.file: s.range.start_line
            for s in store.symbols(kind=SymbolKind.FUNCTION, name="main")
            if s.is_definition
        }
    sites: list[RuleSite] = []
    for entry in entries:
        target = targets.get(entry.runs or "")
        if target is None or not target.files:
            continue
        home = next((f for f in target.files if f in mains), target.files[0])
        if home in wanted:
            sites.append(RuleSite(file=home, line=mains.get(home, 1), text=entry.test_id))
    return sorted(sites, key=lambda s: (s.file, s.text or ""))


def c_definitions(index_path: Path, files: list[str]) -> list[RuleSite]:
    """The definitions with C linkage, in the source files among ``files``, that another file
    declares or uses. Each site names a header that declares it, or else a file that uses it.
    A function that takes ``...`` or a ``va_list`` isn't one: stable Rust can't define it, so
    a port leaves it in C (D-70)."""
    sites: list[RuleSite] = []
    with IndexStore.open(index_path) as store:
        roles = {e.path: e.role for e in store.files()}
        for path in sorted(set(files)):
            if roles.get(path) is not FileRole.SOURCE:
                continue
            for symbol in store.used_elsewhere(path):
                if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.VARIABLE):
                    continue
                if symbol.boundary is not Boundary.C_ABI or takes_variable_arguments(symbol):
                    continue
                declaring = [f for f in store.files_declaring([symbol.id]) if f != path]
                headers = [f for f in declaring if roles.get(f) is FileRole.HEADER]
                if headers:
                    where = f"declared in {headers[0]}"
                else:
                    users = [f for f in store.files_referencing([symbol.id]) if f != path]
                    where = f"used by {(declaring or users)[0]}"
                sites.append(
                    RuleSite(
                        file=path,
                        line=symbol.range.start_line,
                        column=symbol.range.start_column,
                        text=f"{symbol.name}, {where}",
                    )
                )
    return sites
