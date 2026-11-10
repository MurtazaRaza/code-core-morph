"""The C adapter (D-68): C projects built with Make or CMake.

C shares the C++ adapter's machinery, the libclang index, the builds and their diagnostics,
CTest and GoogleTest, the C declaration checks; it is a language of its own because rule packs
belong to one language, so the C++ modernization rules never apply to C. C's port to Rust is
the ``c_to_rust`` pack's (§11.7). C has no modernization pack.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import characterize, rustports
from codecoremorph.adapters.c.detect import C_STANDARDS, CMAKE_C_STANDARD, normalized
from codecoremorph.adapters.c.detect import detect as detect_project
from codecoremorph.adapters.c.tests import disabled_markers, test_assertions
from codecoremorph.adapters.cpp.adapter import CppAdapter
from codecoremorph.adapters.cpp.detect import masked_comments
from codecoremorph.adapters.cpp.diagnostics import parse_compiler_output
from codecoremorph.adapters.cpp.indexing import COMPILE_COMMANDS, build_files
from codecoremorph.adapters.cpp.make import BUILD_SYSTEM as MAKE
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    AdapterCapabilities,
    Boundary,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    FileRole,
    LanguageConfig,
    ProjectDetection,
    RuleSite,
    Severity,
    SymbolKind,
    TargetKind,
    TestAssertions,
    TestSelection,
    ToolchainSpec,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile, majority_newline
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore, takes_variable_arguments

# GNU make's own order of the names it reads.
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
RUST_INSTALL_HINT = (
    "A Rust → C port runs with the Rust toolchain, which holds the C one: use Docker "
    "(sandbox.mode: docker) with the codecoremorph-rust image (D-75)."
)


class CAdapter(CppAdapter):
    """C projects built with Make or CMake."""

    api_version = "1"
    language = "c"
    version = "1"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            parse_check=True,
            format=True,
            # TAP programs in a Makefile build, which a port translates with the rest (D-74).
            register_test_files=True,
            extract_assertions=True,
            detect_disabled_tests=True,
            c_declarations=True,
            # The scaffolds of a Rust → C port, whose target C is (D-75).
            codemod_tools=["clang-tidy", "c-module", "c-program", "c-tests", "c-retire"],
            queries=["c_abi_definitions", "test_cases"],
            standards=list(C_STANDARDS),
        )

    # ------------------------------------------------------------------ a Rust → C port (D-75)

    def _rust_port(self, ctx: AdapterContext) -> bool:
        return ctx.lang.port_source == "rust"

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        """C++'s, but a Rust port's target needs Rust too: the Rust image has both, and the
        Rust toolchain installs the C++ one."""
        spec = super().toolchain_requirements(lang)
        if lang.port_source != "rust":
            return spec
        return spec.model_copy(
            update={
                "language": "rust",
                "image": "codecoremorph-rust",
                "install_hint": RUST_INSTALL_HINT,
            }
        )

    def configure(self, ctx: AdapterContext) -> BuildResult:
        """As C++'s; a Rust port's target configures its C with the sanitizers on, and has
        nothing to configure before the first unit writes it."""
        if not self._rust_port(ctx):
            return super().configure(ctx)
        if not self.builds(ctx):
            ctx.log_dir.mkdir(parents=True, exist_ok=True)
            note = ctx.log_dir / "configure.log"
            note.write_text("no C yet: the port's first unit writes it\n", encoding="utf-8")
            return BuildResult(success=True, duration_s=0.0, log_path=note)
        args = [*ctx.lang.configure_args, f"-D{rustports.SANITIZE}=ON"]
        lang = ctx.lang.model_copy(update={"configure_args": args})
        return super().configure(replace(ctx, lang=lang))

    def symbol_map_path(self) -> str:
        return rustports.SYMBOL_MAP

    def test_map_path(self) -> str:
        return rustports.TEST_MAP

    def lint(self, ctx: AdapterContext) -> list[Diagnostic]:
        """A Rust port's C (D-75): what the compiler warns about with ``-Wall -Wextra`` and what
        clang-tidy's bug-finding checks find, over every C file under ``c/``; and the build
        must keep its sanitizer option, or the tests would run the C without them."""
        if not self._rust_port(ctx):
            return super().lint(ctx)
        found: list[Diagnostic] = []
        top = ctx.worktree / rustports.TOP_CMAKE
        text = TextFile.decode(top.read_bytes()).text if top.is_file() else ""
        if text and rustports.SANITIZE not in masked_comments(text):
            found.append(
                Diagnostic(
                    file_path=rustports.TOP_CMAKE,
                    line=1,
                    severity=Severity.ERROR,
                    message=f"the {rustports.SANITIZE} option is gone: the tests must run the C "
                    "under AddressSanitizer and UndefinedBehaviorSanitizer",
                    origin=DiagnosticOrigin.ANALYZER,
                )
            )
        c_dir = ctx.worktree / rustports.C_DIR
        files = sorted(p.relative_to(ctx.worktree).as_posix() for p in c_dir.rglob("*.c"))
        # Configure copies the compile database to the cache: in Docker the build directory is
        # a volume the host doesn't see, so it isn't the place to look for it.
        if not files or not (ctx.cache_dir / COMPILE_COMMANDS).is_file():
            return found
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            rustports.clang_tidy_argv(
                ctx.sandbox(ctx.build_dir), [ctx.sandbox(ctx.worktree / f) for f in files]
            ),
            cwd=ctx.sandbox(ctx.worktree),
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "clang-tidy-lint.log",
        )
        stdout, stderr = full_output(result)
        mapper = self._mapper(ctx)
        found += [
            d
            for d in parse_compiler_output(
                stdout + "\n" + stderr, mapper, origin=DiagnosticOrigin.ANALYZER
            )
            if d.file_path is not None and d.file_path.startswith(f"{rustports.C_DIR}/")
        ]
        return found

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        mine = [s for s in codemods if s.tool in rustports.SCAFFOLDS]
        if not mine:
            return super().run_codemods(ctx, codemods, files)
        started = time.monotonic()
        changed: list[str] = []
        for spec in mine:
            changed += rustports.scaffold(
                ctx.worktree, spec.tool, spec.options, files, ctx.lang.standard
            )
        rest = [s for s in codemods if s.tool not in rustports.SCAFFOLDS]
        result = super().run_codemods(ctx, rest, files) if rest else None
        changes = [CodemodChange(file=f, replacements=1) for f in sorted(set(changed))]
        if result is None:
            return CodemodResult(changes=changes, duration_s=time.monotonic() - started)
        return result.model_copy(update={"changes": [*changes, *result.changes]})

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

    # ------------------------------------------------------------------ characterization (D-74)

    def characterization_test_file(self, test_dir: str, stem: str) -> str:
        return characterize.test_file(test_dir, stem)

    def register_test_files(
        self, ctx: AdapterContext, test_dir: str, files: list[str], sources: list[str]
    ) -> list[str]:
        """A C characterization test is a TAP program over ``codecoremorph_tap.h``: a marked
        block in the top-level Makefile builds each from its file, linked with the archives
        of the libraries compiling ``sources``, and runs it before the test target's recipe.
        Without ``files``, the block and the helper go (and nothing is left to do once a
        port's retirement deleted the Makefile)."""
        if ctx.lang.build_system != MAKE:
            raise ConfigError(
                "characterization tests of a C project need a Make build for now",
                hint="Set parity.characterization_tests.enabled to false (D-74).",
            )
        # By the directory's own entries: Windows finds `makefile` when the file is `Makefile`.
        present = {p.name for p in ctx.worktree.iterdir() if p.is_file()}
        name = next((n for n in MAKEFILES if n in present), None)
        helper = PurePosixPath(test_dir.rstrip("/")) / characterize.HELPER
        if name is None:
            if files:
                raise ConfigError(
                    "characterization tests can't be added to an automake build yet",
                    reason="the project has no top-level Makefile to add them to",
                    hint="Set parity.characterization_tests.enabled to false (D-74).",
                )
            (ctx.worktree / helper).unlink(missing_ok=True)
            return [helper.as_posix()]
        path = ctx.worktree / name
        decoded = TextFile.decode(path.read_bytes())
        newline = majority_newline(decoded.text)
        before = decoded.text.replace("\r\n", "\n")
        if files:
            archives, includes = self._characterized(ctx, sources)
            targets = characterize.test_targets(before, ctx.lang.test_targets)
            block = characterize.make_block(sorted(files), archives, includes, targets)
            (ctx.worktree / helper).parent.mkdir(parents=True, exist_ok=True)
            (ctx.worktree / helper).write_bytes(characterize.HELPER_TEXT.encode("utf-8"))
            text = characterize.with_block(before, block)
        else:
            (ctx.worktree / helper).unlink(missing_ok=True)
            text = characterize.with_block(before, None)
        if text != before:
            path.write_bytes(decoded.encode(text.replace("\n", newline)))
        return [name, helper.as_posix()]

    def _characterized(
        self, ctx: AdapterContext, sources: list[str]
    ) -> tuple[list[str], list[str]]:
        """The archives of the libraries that compile ``sources`` (every library's when none
        does), and the directories of the project headers those sources include."""
        index = ctx.cache_dir / INDEX_FILE_NAME
        if not index.is_file():
            return [], []
        wanted = set(sources)
        with IndexStore.open(index) as store:
            libraries = [t for t in store.build_targets() if t.kind is TargetKind.LIBRARY]
            roles = {e.path: e.role for e in store.files()}
            included = {d.to_file for d in store.dependencies() if d.from_file in wanted}
        chosen = [t for t in libraries if wanted & set(t.files)] or libraries
        archives = sorted({a for t in chosen for a in t.artifacts if a.endswith(".a")})
        headers = {f for f in included | wanted if roles.get(f) is FileRole.HEADER}
        dirs = sorted({str(PurePosixPath(h).parent) for h in headers} - {"."})
        return archives, dirs

    def characterization_tests(self, test_ids: list[str]) -> TestSelection:
        return TestSelection(test_ids=list(test_ids))

    def drop_tests(self, ctx: AdapterContext, file: str, test_ids: list[str]) -> int:
        path = ctx.worktree / file
        decoded = TextFile.decode(path.read_bytes())
        prefix = f"tap:{characterize.program(file)}/"
        names = [i.removeprefix(prefix) for i in test_ids if i.startswith(prefix)]
        text = characterize.drop_cases(decoded.text, names)
        if text != decoded.text:
            path.write_bytes(decoded.encode(text))
        return len(characterize.cases(text))

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        """GoogleTest cases, as in C++; each case of a characterization test, by the ID its
        program will give it (D-74); and the C tests of the last test run: each TAP point
        with the check that prints it, each program automake's harness ran with every check
        in its files (I-4, D-73)."""
        found = super().extract_assertions(ctx, files)
        for path in files:
            try:
                text = TextFile.decode((ctx.worktree / path).read_bytes()).text
            except (OSError, BinaryFileError):
                continue
            found += [
                TestAssertions(
                    file=path,
                    test=case.name,
                    test_id=f"tap:{characterize.program(path)}/{case.name}",
                    line=case.line,
                    assertions=[c.text for c in case.checks],
                )
                for case in characterize.cases(text)
            ]
        known = {a.test_id for a in found}
        found += [
            a
            for a in test_assertions(ctx.cache_dir / INDEX_FILE_NAME, ctx.worktree, files)
            if a.test_id not in known
        ]
        return found

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        """GoogleTest's markers, as in C++, and what disables a C test: ``#if 0``, a TAP skip
        or to-do, automake's skipped status 77 (I-3, D-73)."""
        found = super().detect_disabled_tests(ctx, files)
        for path in files:
            if not path.endswith((".c", ".h")):
                continue  # a port's Rust tests are the target adapter's to read
            try:
                text = TextFile.decode((ctx.worktree / path).read_bytes()).text
            except (OSError, BinaryFileError):
                continue
            found += [m for m in disabled_markers(path, text) if m not in found]
        return found

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
