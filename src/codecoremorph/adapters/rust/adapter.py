"""The Rust adapter (SPEC §11.5, §11.6, D-56, D-58): Cargo builds and clippy with JSON
diagnostics, nextest tests, the ``rust-crate`` and ``rust-module`` scaffolds, rustfmt, the C
header a crate exports (cbindgen), the bridge headers of a module port, and a symbol index.

In a port run it is the *target* adapter of a migration pair: the C++ adapter builds and
tests the project through CMake, which builds the crates with Corrosion; this adapter adds
what only Cargo knows. Every command goes through ``ctx.executor``.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import shutil
import time
from collections.abc import Callable, Collection
from pathlib import Path, PurePath, PurePosixPath

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.rust import ports
from codecoremorph.adapters.rust.cargo import (
    EDITIONS,
    NEXTEST_PROFILE,
    cbindgen_config,
    crate_files,
    edition,
    fetched_packages,
    find_crates,
    find_packages,
    library_compiling,
    nextest_config,
    parse_cargo_messages,
    parse_nextest_junit,
    valid_crate_name,
    with_corrosion_block,
)
from codecoremorph.adapters.rust.symbols import mask, scan_crate
from codecoremorph.adapters.rust.tests import scan_tests, test_binary, test_id
from codecoremorph.core.errors import ConfigError, SandboxError
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    IndexMeta,
    LanguageConfig,
    RuleSite,
    Severity,
    SkippedReplacement,
    SymbolIndex,
    TestAssertions,
    TestCaseResult,
    TestReport,
    TestSelection,
    ToolchainSpec,
    ToolRequirement,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import INDEX_FILE_NAME, FileRow, IndexStore, SymbolRow

MIN_RUST = "1.80"
TARGET_DIR = "cargo-target"  # in ctx.cache_dir, outside the worktree
# Every build is a verification of a new tree, so incremental caches only cost time and disk
# (and their directory names overflow Windows' path limit in deep cache directories).
CARGO_ENV = {"CARGO_INCREMENTAL": "0"}


class RustAdapter(LanguageAdapter):
    """Rust crates built with Cargo, inside a C++ project's CMake tree or on their own."""

    api_version = "1"
    language = "rust"
    version = "1"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            format=True,
            exported_c_headers=True,
            extract_assertions=True,
            codemod_tools=[
                "rust-crate",
                "rust-module",
                "rust-bin",
                "rust-tests",
                "rust-retire",
                "rustfmt",
            ],
            standards=list(EDITIONS),
        )

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(
            language="rust",
            image="codecoremorph-rust",
            tools=[
                ToolRequirement(name="cargo", min_version=MIN_RUST, purpose="Rust builds"),
                ToolRequirement(name="rustc", min_version=MIN_RUST, purpose="Rust compiler"),
                ToolRequirement(
                    name="cargo-nextest", version_args=["--version"], purpose="Rust tests"
                ),
                ToolRequirement(name="cbindgen", purpose="the C header a crate exports"),
            ],
            install_hint="codecoremorph toolchain install rust",
        )

    # ------------------------------------------------------------------ helpers

    def crates(self, ctx: AdapterContext) -> list[str]:
        return find_crates(ctx.worktree, ctx.lang.exclude)

    def _cargo(
        self,
        ctx: AdapterContext,
        crate: str,
        args: list[str],
        log: str,
        *,
        timeout_s: float,
        env: dict[str, str] | None = None,
        target_dir: bool = True,
    ) -> tuple[bool, str, Path, float]:
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        argv = ["cargo", *args, "--manifest-path", ctx.sandbox(ctx.worktree / crate / "Cargo.toml")]
        if target_dir:
            argv += ["--target-dir", ctx.sandbox(ctx.cache_dir / TARGET_DIR)]
        result = ctx.executor.run(
            argv,
            cwd=ctx.sandbox(ctx.worktree / crate),
            env={**CARGO_ENV, **(env or {})},
            timeout_s=timeout_s,
            log_path=ctx.log_dir / log,
        )
        stdout, stderr = full_output(result)
        return result.ok, stdout + "\n" + stderr, result.log_path, result.duration_s

    def _roots(self, ctx: AdapterContext) -> list[str]:
        return [ctx.sandbox(ctx.worktree), str(ctx.worktree)]

    def _check(self, ctx: AdapterContext, subcommand: str, *, analyzer: bool) -> BuildResult:
        started = time.monotonic()
        diagnostics: list[Diagnostic] = []
        success = True
        log_path = ctx.log_dir / f"cargo-{subcommand}.log"
        for crate in self.crates(ctx):
            ok, output, log_path, _ = self._cargo(
                ctx,
                crate,
                [subcommand, "--locked", "--all-targets", "--message-format=json"],
                f"cargo-{subcommand}-{_slug(crate)}.log",
                timeout_s=ctx.lang.build_timeout_s,
            )
            found = parse_cargo_messages(output, crate, self._roots(ctx), analyzer=analyzer)
            diagnostics += found
            if not ok:
                success = False
                if not any(d.severity is Severity.ERROR for d in found):
                    tail = "\n".join(output.strip().splitlines()[-15:])
                    diagnostics.append(
                        Diagnostic(
                            file_path=f"{crate}/Cargo.toml",
                            severity=Severity.ERROR,
                            message=f"cargo {subcommand} failed:\n{tail}",
                            origin=DiagnosticOrigin.BUILD_SYSTEM,
                        )
                    )
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("no crates\n", encoding="utf-8")
        return BuildResult(
            success=success,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
            log_path=log_path,
        )

    # ------------------------------------------------------------------ the contract

    def configure(self, ctx: AdapterContext) -> BuildResult:
        """The dependency-fetch step (SPEC §11.5): ``cargo fetch --locked`` for each crate whose
        ``Cargo.lock`` takes packages from a registry, with the network on for that command
        only (``sandbox.network: fetch_only``). Builds then run offline. A crate without such
        packages needs nothing, and no network."""
        started = time.monotonic()
        log_path = self._note(ctx, "cargo-configure.log")
        # A module port's bridge headers come first: the C++ build includes them (D-58).
        diagnostics = self._bridge_headers(ctx)
        for crate in self.crates(ctx):
            lock = _text(ctx.worktree / crate / "Cargo.lock")
            if lock is None:
                diagnostics.append(
                    Diagnostic(
                        file_path=f"{crate}/Cargo.lock",
                        severity=Severity.ERROR,
                        message="the crate has no Cargo.lock; builds run --locked, so commit one",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                )
                continue
            if not fetched_packages(lock):
                continue
            ctx.executor.set_network(True)
            try:
                ok, output, log_path, _ = self._cargo(
                    ctx,
                    crate,
                    ["fetch", "--locked"],
                    f"cargo-fetch-{_slug(crate)}.log",
                    timeout_s=ctx.lang.build_timeout_s,
                    env={"CARGO_NET_OFFLINE": "false"},
                    target_dir=False,
                )
            finally:
                ctx.executor.set_network(False)
            if not ok:
                tail = "\n".join(output.strip().splitlines()[-15:])
                diagnostics.append(
                    Diagnostic(
                        file_path=f"{crate}/Cargo.lock",
                        severity=Severity.ERROR,
                        message=f"cargo fetch failed:\n{tail}",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                )
        return BuildResult(
            success=not diagnostics,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
            log_path=log_path,
        )

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        """``cargo build --all-targets`` over every crate: libraries, programs and tests."""
        return self._check(ctx, "build", analyzer=False)

    def builds(self, ctx: AdapterContext) -> bool:
        return bool(self.crates(ctx))

    def clean_build(self, ctx: AdapterContext) -> None:
        """Remove the Cargo target directory (in the cache, never in the worktree)."""
        shutil.rmtree(ctx.cache_dir / TARGET_DIR, ignore_errors=True)

    def clippy(self, ctx: AdapterContext) -> BuildResult:
        """``cargo clippy`` over every crate: compiler and clippy diagnostics."""
        return self._check(ctx, "clippy", analyzer=True)

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        started = time.monotonic()
        config = ctx.cache_dir / "nextest.toml"
        store = ctx.cache_dir / "nextest"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(nextest_config(ctx.sandbox(store)), encoding="utf-8")
        cases: list[TestCaseResult] = []
        log_path = self._note(ctx, "nextest.log")
        args = ["nextest", "run", "--locked", "--no-tests=pass"]
        args += ["--config-file", ctx.sandbox(config), "--profile", NEXTEST_PROFILE]
        for crate in self.crates(ctx):
            ok, output, log_path, _ = self._cargo(
                ctx, crate, args, f"nextest-{_slug(crate)}.log", timeout_s=ctx.lang.test_timeout_s
            )
            report = store / NEXTEST_PROFILE / "junit.xml"
            if report.is_file():
                cases += parse_nextest_junit(report.read_text(encoding="utf-8"))
                report.unlink()
            elif not ok:
                raise SandboxError(
                    f"the tests of crate {crate} didn't run",
                    reason="\n".join(output.strip().splitlines()[-10:]),
                )
        if selection is not None and selection.test_ids is not None:
            wanted = set(selection.test_ids)
            cases = [c for c in cases if c.test_id in wanted]
        return TestReport(cases=cases, duration_s=time.monotonic() - started, log_path=log_path)

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        """The packages' Rust files and their items, by path (the scanner of D-58): enough for
        the symbol map and a unit's context."""
        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.cache_dir / INDEX_FILE_NAME
        rows: list[FileRow] = []
        symbols: list[SymbolRow] = []
        seen: set[str] = set()
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            base = ctx.worktree / package
            sources: dict[str, tuple[str, str]] = {}
            for file in sorted(base.rglob("*.rs")):
                rel = file.relative_to(ctx.worktree).as_posix()
                if rel in seen or _skipped(PurePosixPath(rel)):
                    continue
                seen.add(rel)
                digest = hashlib.sha256(file.read_bytes()).hexdigest()
                rows.append((rel, "rust", digest, "source", 0, 0, "utf-8", "lf"))
                text = _text(file)
                if text is not None:
                    sources[file.relative_to(base).as_posix()] = (rel, text)
            name = _package_name(_text(base / "Cargo.toml") or "") or posixpath.basename(package)
            for item in scan_crate(name.replace("-", "_"), sources):
                symbols.append(
                    (
                        f"rust:{item.path}",
                        item.path.rsplit("::", 1)[-1],
                        item.path,
                        item.kind.value,
                        item.file,
                        item.line,
                        item.column,
                        item.line,
                        item.column,
                        None,
                        "public" if item.public else "none",
                        1,
                        "c_abi" if item.c_abi else "none",
                    )
                )
        meta = IndexMeta(
            adapter="rust",
            adapter_version=self.version,
            stats={"files": len(rows), "symbols": len(symbols)},
        )
        with IndexStore.open(path, reset=True) as store:
            store.write(files=rows, symbols=symbols)
            store.set_meta(meta)
        return SymbolIndex(path=path, meta=meta)

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        started = time.monotonic()
        changes: list[CodemodChange] = []
        skipped: list[SkippedReplacement] = []
        diagnostics: list[Diagnostic] = []
        for spec in codemods:
            if spec.tool == "rust-crate":
                changes += self._scaffold(ctx, spec, files)
            elif spec.tool == "rust-module":
                found, problems = self._module_scaffold(ctx, spec, files)
                changes += found
                diagnostics += problems
            elif spec.tool == "rust-bin":
                found, problems = self._bin_scaffold(ctx, spec, files)
                changes += found
                diagnostics += problems
            elif spec.tool == "rust-tests":
                found, problems = self._tests_scaffold(ctx, spec, files)
                changes += found
                diagnostics += problems
            elif spec.tool == "rust-retire":
                changes += self._retire(ctx, spec, files)
            elif spec.tool == "rustfmt":
                changes += self._rustfmt(ctx)
            else:
                skipped.append(
                    SkippedReplacement(file="-", check=spec.tool, reason="not a Rust codemod")
                )
        return CodemodResult(
            changes=changes,
            skipped=skipped,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
        )

    def format(self, ctx: AdapterContext, files: list[str]) -> None:
        self._rustfmt(ctx)

    # ------------------------------------------------------------------ the scaffold

    def _scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> list[CodemodChange]:
        """The ``rust-crate`` codemod: a staticlib crate for the unit's C++ module, imported
        (and linked to the module's C++ target) in a marked block of the top-level
        ``CMakeLists.txt``. ``options``: ``crate`` (default: the module's file stem),
        ``dir`` (default ``rust/<crate>``)."""
        sources = [f for f in files if f.endswith((".cpp", ".cc", ".cxx", ".c"))]
        name = spec.options.get("crate") or (
            posixpath.splitext(posixpath.basename(sources[0]))[0] if sources else "port"
        )
        if not valid_crate_name(name):
            name = "".join(c if c.isalnum() else "_" for c in name.lower()) or "port"
        if spec.options.get("prefix_project", "true") == "true":
            project = _project_name(ctx.worktree)
            if project and not name.startswith(project):
                name = f"{project}_{name}"
        if not valid_crate_name(name):
            raise ConfigError(f"{name!r} is not a crate name", hint="Set the codemod's `crate`.")
        directory = spec.options.get("dir") or f"rust/{name}"
        try:
            year = edition(ctx.lang.target_standard or ctx.lang.standard)
        except ValueError as exc:
            raise ConfigError(
                "the rust-crate codemod needs a Rust target edition",
                reason=str(exc),
                hint="Set migration.target to {language: rust, standard: rust2021}.",
            ) from None
        changes: list[CodemodChange] = []
        doc = f"The Rust port of {', '.join(sources) or 'the module'} (CodeCoreMorph, D-56)."
        for rel, text in crate_files(name, year, doc=doc).items():
            path = ctx.worktree / directory / rel
            if path.exists():
                continue  # a crate that is already there stays as it is
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            changes.append(CodemodChange(file=f"{directory}/{rel}", replacements=1))
        top = ctx.worktree / "CMakeLists.txt"
        if top.is_file():
            target = next(
                (
                    t
                    for s in sources
                    if (t := library_compiling(_cmake_files(ctx.worktree), s)) is not None
                ),
                None,
            )
            original = TextFile.decode(top.read_bytes())
            updated = with_corrosion_block(
                original.text, f"{directory}/Cargo.toml", (target, name) if target else None
            )
            if updated != original.text:
                top.write_bytes(original.encode(updated))
                changes.append(CodemodChange(file="CMakeLists.txt", replacements=1))
        return changes

    def _module_scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> tuple[list[CodemodChange], list[Diagnostic]]:
        """The ``rust-module`` codemod (D-58): the workspace, the crate of the unit's library
        target, the unit's module and bridge module, the symbol map, the lockfile, the marked
        Corrosion block and the bridge header. Idempotent: what is there stays. ``options``:
        ``crate``, ``module`` and ``build_target``, from the unit (``PortUnit``)."""
        crate = spec.options.get("crate", "")
        module = spec.options.get("module", "")
        target = spec.options.get("build_target", "")
        if not (valid_crate_name(crate) and valid_crate_name(module) and target):
            raise ConfigError(
                "the rust-module codemod needs a module port's unit",
                reason=f"crate={crate!r}, module={module!r}, build_target={target!r}",
                hint="Use it in a port rule with `port_units: module`.",
            )
        try:
            year = edition(ctx.lang.target_standard or ctx.lang.standard)
        except ValueError as exc:
            raise ConfigError(
                "the rust-module codemod needs a Rust target edition",
                reason=str(exc),
                hint="Set migration.target to {language: rust, standard: rust2021}.",
            ) from None
        workspace = ports.WORKSPACE_DIR
        package = f"{workspace}/{crate}"
        sources = sorted(
            f
            for f in files
            if not f.startswith(f"{workspace}/") and f.lower().endswith(ports.CPP_EXTENSIONS)
        )
        changes: list[CodemodChange] = []

        def current(rel: str) -> str | None:
            return _text(ctx.worktree / rel)

        def write(rel: str, text: str) -> None:
            path = ctx.worktree / rel
            old = path.read_bytes() if path.is_file() else None
            data = text.encode("utf-8") if old is None else TextFile.decode(old).encode(text)
            if data != old:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                changes.append(CodemodChange(file=rel, replacements=1))

        manifest = current(f"{workspace}/Cargo.toml")
        write(
            f"{workspace}/Cargo.toml",
            ports.workspace_manifest([crate])
            if manifest is None
            else ports.with_member(manifest, crate),
        )
        if current(f"{package}/Cargo.toml") is None:
            write(f"{package}/Cargo.toml", ports.package_manifest(crate, year, target))
            write(f"{package}/cbindgen.toml", ports.cbindgen_toml(crate))
            write(f"{package}/include/{crate}/bridge.hpp", ports.bridge_hpp(crate))
            write(f"{package}/src/lib.rs", ports.lib_rs(crate, target))
            write(f"{package}/src/bridge/mod.rs", ports.bridge_mod_rs(crate))
        if current(f"{package}/src/{module}.rs") is None:
            write(f"{package}/src/{module}.rs", ports.module_rs(sources))
        lib = current(f"{package}/src/lib.rs") or ports.lib_rs(crate, target)
        write(f"{package}/src/lib.rs", ports.with_module(lib, module))
        if current(f"{package}/src/bridge/{module}.rs") is None:
            write(f"{package}/src/bridge/{module}.rs", ports.bridge_module_rs(module, sources))
        bridge = current(f"{package}/src/bridge/mod.rs") or ports.bridge_mod_rs(crate)
        write(f"{package}/src/bridge/mod.rs", ports.with_module(bridge, module, public=False))
        if current(ports.SYMBOL_MAP) is None:
            write(ports.SYMBOL_MAP, ports.SYMBOL_MAP_TEXT)
        problems = self._lock_workspace(ctx, workspace, changes)
        top = ctx.worktree / "CMakeLists.txt"
        if top.is_file():
            original = TextFile.decode(top.read_bytes())
            updated = with_corrosion_block(
                original.text,
                f"{workspace}/Cargo.toml",
                (target, crate),
                (target, f"{package}/include"),
            )
            if updated != original.text:
                top.write_bytes(original.encode(updated))
                changes.append(CodemodChange(file="CMakeLists.txt", replacements=1))
        header = f"{package}/{ports.bridge_header_path(crate)}"
        before = current(header)
        problems += self._bridge_headers(ctx)
        if current(header) != before:
            changes.append(CodemodChange(file=header, replacements=1))
        once = {c.file: c for c in changes}  # a file written twice is one change
        return list(once.values()), problems

    def _writer(
        self, ctx: AdapterContext, changes: list[CodemodChange]
    ) -> Callable[[str, str], None]:
        """A function that writes a worktree file, keeping its line endings, and records it."""

        def write(rel: str, text: str) -> None:
            path = ctx.worktree / rel
            old = path.read_bytes() if path.is_file() else None
            data = text.encode("utf-8") if old is None else TextFile.decode(old).encode(text)
            if data != old:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                changes.append(CodemodChange(file=rel, replacements=1))

        return write

    def _edition_year(self, ctx: AdapterContext, tool: str) -> str:
        try:
            return edition(ctx.lang.target_standard or ctx.lang.standard)
        except ValueError as exc:
            raise ConfigError(
                f"the {tool} codemod needs a Rust target edition",
                reason=str(exc),
                hint="Set migration.target to {language: rust, standard: rust2021}.",
            ) from None

    def _bin_scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> tuple[list[CodemodChange], list[Diagnostic]]:
        """The ``rust-bin`` codemod (D-60): a package for the unit's C++ executable whose
        ``bin`` keeps the executable's name, with the crates it links as dependencies, in the
        workspace; the C++ target's commands go from the CMake files, so the Rust program
        takes the name over (Corrosion imports it). The C++ sources stay for the agent to
        port, then delete. ``options``: ``crate``, ``bin``, ``build_target``,
        ``dependencies`` (comma-separated crates)."""
        crate = spec.options.get("crate", "")
        name = spec.options.get("bin", "")
        target = spec.options.get("build_target", "")
        if not (valid_crate_name(crate) and name and target):
            raise ConfigError(
                "the rust-bin codemod needs an executable's unit",
                reason=f"crate={crate!r}, bin={name!r}, build_target={target!r}",
                hint="Use it in a port rule with `port_units: executable`.",
            )
        year = self._edition_year(ctx, "rust-bin")
        dependencies = [d for d in spec.options.get("dependencies", "").split(",") if d]
        workspace = ports.WORKSPACE_DIR
        package = f"{workspace}/{crate}"
        sources = sorted(f for f in files if f.lower().endswith(ports.CPP_EXTENSIONS))
        changes: list[CodemodChange] = []
        write = self._writer(ctx, changes)
        manifest = _text(ctx.worktree / workspace / "Cargo.toml")
        write(
            f"{workspace}/Cargo.toml",
            ports.workspace_manifest([crate])
            if manifest is None
            else ports.with_member(manifest, crate),
        )
        if _text(ctx.worktree / package / "Cargo.toml") is None:
            write(
                f"{package}/Cargo.toml",
                ports.bin_manifest(crate, year, name, target, dependencies),
            )
        if _text(ctx.worktree / package / "src" / "main.rs") is None:
            write(f"{package}/src/main.rs", ports.main_rs(sources))
        if _text(ctx.worktree / ports.TEST_MAP) is None:
            write(ports.TEST_MAP, ports.TEST_MAP_TEXT)
        for rel, text in _cmake_files(ctx.worktree):
            updated = ports.without_cmake_target(text, target)
            if updated != text:
                write(rel, updated)
        problems = self._lock_workspace(ctx, workspace, changes)
        once = {c.file: c for c in changes}
        return list(once.values()), problems

    def _tests_scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> tuple[list[CodemodChange], list[Diagnostic]]:
        """The ``rust-tests`` codemod (D-60): the integration test that a C++ test file is
        translated into (``rust/<crate>/tests/<module>.rs``), the test map, entries mapping the
        CTest entries that only run the unit's GoogleTest binary to nothing, and
        ``dev-dependencies`` on the other crates the test uses. ``options``: ``crate``,
        ``module`` (the test file's name), ``wrappers``, ``dependencies``."""
        crate = spec.options.get("crate", "")
        module = spec.options.get("module", "")
        if not (valid_crate_name(crate) and valid_crate_name(module)):
            raise ConfigError(
                "the rust-tests codemod needs a test file's unit",
                reason=f"crate={crate!r}, module={module!r}",
                hint="Use it in a port rule with `port_units: tests`.",
            )
        package = f"{ports.WORKSPACE_DIR}/{crate}"
        changes: list[CodemodChange] = []
        write = self._writer(ctx, changes)
        manifest = _text(ctx.worktree / package / "Cargo.toml")
        if manifest is None:
            return [], [
                Diagnostic(
                    file_path=f"{package}/Cargo.toml",
                    severity=Severity.ERROR,
                    message=f"the crate {crate} isn't there: its library's module units must "
                    "commit before its tests are translated",
                    origin=DiagnosticOrigin.BUILD_SYSTEM,
                )
            ]
        sources = sorted(f for f in files if f.lower().endswith(ports.CPP_EXTENSIONS))
        test_file = f"{package}/tests/{module}.rs"
        if _text(ctx.worktree / test_file) is None:
            write(test_file, ports.test_rs(", ".join(sources) or module))
        dependencies = [
            d for d in spec.options.get("dependencies", "").split(",") if d and d != crate
        ]
        if dependencies:
            write(f"{package}/Cargo.toml", ports.with_dev_dependencies(manifest, dependencies))
        test_map = _text(ctx.worktree / ports.TEST_MAP) or ports.TEST_MAP_TEXT
        wrappers = [w for w in spec.options.get("wrappers", "").split(",") if w]
        write(
            ports.TEST_MAP,
            ports.with_test_entries(test_map, [(w, ports.WRAPPER_NOTE) for w in wrappers]),
        )
        problems: list[Diagnostic] = []
        if dependencies:
            problems = self._lock_workspace(ctx, ports.WORKSPACE_DIR, changes)
        once = {c.file: c for c in changes}
        return list(once.values()), problems

    def _retire(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> list[CodemodChange]:
        """The ``rust-retire`` codemod (D-60): delete the unit's files outside the workspace
        (the C++ and CMake files left) and, unless ``keep`` is ``true`` (the kept C++ API
        needs it), each crate's bridge: ``src/bridge/``, ``include/``, ``cbindgen.toml``, the
        ``staticlib`` crate type and the CMake metadata, with ``#![forbid(unsafe_code)]`` at
        the root."""
        changes: list[CodemodChange] = []
        workspace = ports.WORKSPACE_DIR
        for rel in sorted(files):
            path = ctx.worktree / rel
            if rel.startswith(f"{workspace}/") or not path.is_file():
                continue
            path.unlink()
            changes.append(CodemodChange(file=rel, replacements=1))
        if spec.options.get("keep") == "true":
            return changes
        write = self._writer(ctx, changes)
        root = _text(ctx.worktree / workspace / "Cargo.toml")
        if root is not None:
            write(f"{workspace}/Cargo.toml", ports.retired_workspace(root))
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            if not package.startswith(f"{workspace}/"):
                continue
            base = ctx.worktree / package
            manifest = _text(base / "Cargo.toml") or ""
            name = (_package_name(manifest) or posixpath.basename(package)).replace("-", "_")
            target = ports.cmake_target(manifest)
            removed = [p for p in (base / "src" / "bridge", base / "include") if p.is_dir()]
            removed += [
                p for p in (base / "cbindgen.toml", base / "src" / "bridge.rs") if p.is_file()
            ]
            for gone in removed:
                if gone.is_dir():
                    for file in sorted(gone.rglob("*")):
                        if file.is_file():
                            changes.append(
                                CodemodChange(
                                    file=file.relative_to(ctx.worktree).as_posix(), replacements=1
                                )
                            )
                    shutil.rmtree(gone)
                else:
                    gone.unlink()
                    changes.append(
                        CodemodChange(
                            file=gone.relative_to(ctx.worktree).as_posix(), replacements=1
                        )
                    )
            lib = _text(base / "src" / "lib.rs")
            if lib is not None:
                write(f"{package}/src/lib.rs", ports.retired_lib_rs(lib, name, target))
            if manifest:
                write(f"{package}/Cargo.toml", ports.retired_manifest(manifest))
        once = {c.file: c for c in changes}
        return list(once.values())

    def _lock_workspace(
        self, ctx: AdapterContext, workspace: str, changes: list[CodemodChange]
    ) -> list[Diagnostic]:
        """``cargo update --workspace --offline``: the lockfile, with every member, and nothing
        else re-resolved."""
        lock = ctx.worktree / workspace / "Cargo.lock"
        before = lock.read_bytes() if lock.is_file() else None
        ok, output, _, _ = self._cargo(
            ctx,
            workspace,
            ["update", "--workspace", "--offline"],
            "cargo-update.log",
            timeout_s=600,
            target_dir=False,
        )
        if not ok:
            tail = "\n".join(output.strip().splitlines()[-10:])
            return [
                Diagnostic(
                    file_path=f"{workspace}/Cargo.lock",
                    severity=Severity.ERROR,
                    message=f"cargo couldn't update the workspace's lockfile:\n{tail}",
                    origin=DiagnosticOrigin.BUILD_SYSTEM,
                )
            ]
        if lock.is_file() and lock.read_bytes() != before:
            changes.append(CodemodChange(file=f"{workspace}/Cargo.lock", replacements=1))
        return []

    def _bridge_headers(self, ctx: AdapterContext) -> list[Diagnostic]:
        """cbindgen writes each package's bridge header (``include/<crate>/bridge.h``) when the
        package has a ``cbindgen.toml``: a module port's crate (D-58). Others have none."""
        problems: list[Diagnostic] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            base = ctx.worktree / package
            config = base / "cbindgen.toml"
            if not config.is_file():
                continue
            name = _package_name(_text(base / "Cargo.toml") or "") or posixpath.basename(package)
            out = base / ports.bridge_header_path(name.replace("-", "_"))
            out.parent.mkdir(parents=True, exist_ok=True)
            ctx.log_dir.mkdir(parents=True, exist_ok=True)
            result = ctx.executor.run(
                [
                    "cbindgen",
                    "--config",
                    ctx.sandbox(config),
                    "--output",
                    ctx.sandbox(out),
                    "--quiet",
                    ctx.sandbox(base),
                ],
                cwd=ctx.sandbox(base),
                timeout_s=600,
                log_path=ctx.log_dir / f"cbindgen-bridge-{_slug(package)}.log",
            )
            if not result.ok:
                stdout, stderr = full_output(result)
                tail = "\n".join((stdout + stderr).strip().splitlines()[-10:])
                problems.append(
                    Diagnostic(
                        file_path=f"{package}/src/lib.rs",
                        severity=Severity.ERROR,
                        message=f"cbindgen couldn't write the bridge header:\n{tail}",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                )
        return problems

    # ------------------------------------------------------------------ module ports (D-58)

    def port_names(self, build_target: str, stem: str, taken: Collection[str]) -> tuple[str, str]:
        return ports.port_names(build_target, stem, taken)

    def symbol_map_path(self) -> str:
        return ports.SYMBOL_MAP

    def unsafe_sites(self, ctx: AdapterContext) -> list[RuleSite]:
        """In each package with a bridge: the crate root must keep ``#![deny(unsafe_code)]``,
        and no file but the root's ``mod bridge`` declaration may allow unsafe code. In the
        port's other packages (under ``rust/``), and in every package's programs and tests,
        no code is unsafe (D-60)."""
        sites: list[RuleSite] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            base = ctx.worktree / package
            src = base / "src"
            lib = src / "lib.rs"
            bridged = (src / "bridge").is_dir() or (src / "bridge.rs").is_file()
            ported = package.startswith(f"{ports.WORKSPACE_DIR}/")
            for file in sorted(base.rglob("*.rs")):
                rel = file.relative_to(ctx.worktree)
                inner = file.relative_to(base).as_posix()
                if _skipped(rel) or not ported:
                    continue
                library = inner.startswith("src/") and not (
                    inner.startswith("src/bin/") or inner == "src/main.rs"
                )
                if bridged and library:
                    continue  # the root's deny covers them, and the bridge may be unsafe
                text = _text(file)
                if text is not None:
                    for path, line, what in ports.unsafe_keywords(rel.as_posix(), mask(text)):
                        sites.append(RuleSite(file=path, line=line, text=what))
            if not bridged:
                continue
            others: dict[str, str] = {}
            for file in sorted(src.rglob("*.rs")):
                inner = file.relative_to(src).as_posix()
                if file == lib or inner == "bridge.rs" or inner.startswith("bridge/"):
                    continue
                text = _text(file)
                if text is not None:
                    others[file.relative_to(ctx.worktree).as_posix()] = mask(text)
            lib_path = lib.relative_to(ctx.worktree).as_posix()
            for file, line, what in ports.unsafe_problems(lib_path, mask(_text(lib) or ""), others):
                sites.append(RuleSite(file=file, line=line, text=what))
        return sites

    def lint(self, ctx: AdapterContext) -> list[Diagnostic]:
        """clippy over every crate: compiler and clippy diagnostics, by file."""
        return self.clippy(ctx).diagnostics

    def apply_lint_fixes(self, ctx: AdapterContext) -> list[str]:
        """``cargo clippy --fix`` over every crate: the machine-applicable suggestions (a
        needless ``vec!``, a manual ``is_multiple_of``, an unused ``mut``), applied until none
        is left, as ``cargo fix`` does. Code that doesn't compile is left as it is; the build
        that follows reports it."""
        changed: list[str] = []
        for crate in self.crates(ctx):
            files = sorted((ctx.worktree / crate).rglob("*.rs"))
            before = {f: f.read_bytes() for f in files}
            self._cargo(
                ctx,
                crate,
                ["clippy", "--fix", "--allow-dirty", "--allow-staged", "--allow-no-vcs",
                 "--locked", "--all-targets"],
                f"clippy-fix-{_slug(crate)}.log",
                timeout_s=ctx.lang.build_timeout_s,
            )  # fmt: skip
            changed += [
                f.relative_to(ctx.worktree).as_posix()
                for f in files
                if f.is_file() and f.read_bytes() != before[f]
            ]
        return changed

    # ------------------------------------------------------------------ the rest (D-60)

    def test_map_path(self) -> str:
        return ports.TEST_MAP

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        """The ``#[test]`` functions of the Rust files among ``files``, by the test ID nextest
        reports, with their checks (the ``assert!`` family, ``panic!``, ``unwrap_err``,
        ``#[should_panic]``)."""
        found: list[TestAssertions] = []
        packages = find_packages(ctx.worktree, ctx.lang.exclude)
        for rel in sorted(f for f in files if f.endswith(".rs")):
            package = max(
                (p for p in packages if p == "." or rel.startswith(f"{p}/")),
                key=len,
                default=None,
            )
            text = _text(ctx.worktree / rel)
            if package is None or text is None:
                continue
            manifest = _text(ctx.worktree / package / "Cargo.toml") or ""
            name = _package_name(manifest) or posixpath.basename(package)
            inner = rel if package == "." else rel[len(package) + 1 :]
            binary = test_binary(name, manifest, inner)
            if binary is None:
                continue
            for test in scan_tests(text):
                found.append(
                    TestAssertions(
                        file=rel,
                        test=test.path,
                        test_id=test_id(binary[0], binary[1], test.path),
                        line=test.line,
                        assertions=list(test.checks),
                    )
                )
        return found

    def unsafe_uses(self, ctx: AdapterContext) -> list[RuleSite]:
        """Every ``unsafe`` in the port's packages (under ``rust/``), for the report."""
        sites: list[RuleSite] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            if not package.startswith(f"{ports.WORKSPACE_DIR}/"):
                continue
            for file in sorted((ctx.worktree / package).rglob("*.rs")):
                rel = file.relative_to(ctx.worktree)
                text = None if _skipped(rel) else _text(file)
                if text is not None:
                    for path, line, what in ports.unsafe_keywords(rel.as_posix(), mask(text)):
                        sites.append(RuleSite(file=path, line=line, text=what))
        return sites

    def _rustfmt(self, ctx: AdapterContext) -> list[CodemodChange]:
        changes: list[CodemodChange] = []
        for crate in self.crates(ctx):
            files = sorted((ctx.worktree / crate).rglob("*.rs"))
            before = {f: f.read_bytes() for f in files}
            manifest = ctx.sandbox(ctx.worktree / crate / "Cargo.toml")
            ctx.executor.run(
                ["cargo", "fmt", "--manifest-path", manifest],
                cwd=ctx.sandbox(ctx.worktree / crate),
                timeout_s=300,
                log_path=ctx.log_dir / f"rustfmt-{_slug(crate)}.log",
            )
            changes += [
                CodemodChange(file=f.relative_to(ctx.worktree).as_posix(), replacements=1)
                for f in files
                if f.is_file() and f.read_bytes() != before[f]
            ]
        return changes

    # ------------------------------------------------------------------ the C ABI

    def exported_c_headers(self, ctx: AdapterContext, files: list[str]) -> list[Path]:
        """The C header of each crate holding one of ``files``, as cbindgen writes it from the
        crate's public ``extern "C"`` items (D-56)."""
        crates = [
            c
            for c in find_packages(ctx.worktree, ctx.lang.exclude)
            if any(c == "." or f.startswith(f"{c}/") for f in files)
        ]
        config = ctx.cache_dir / "cbindgen.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(cbindgen_config(), encoding="utf-8")
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        headers: list[Path] = []
        for crate in crates:
            out = ctx.cache_dir / f"cbindgen-{_slug(crate)}.h"
            out.unlink(missing_ok=True)
            argv = ["cbindgen", "--config", ctx.sandbox(config), "--output", ctx.sandbox(out)]
            result = ctx.executor.run(
                [*argv, "--quiet", ctx.sandbox(ctx.worktree / crate)],
                cwd=ctx.sandbox(ctx.worktree / crate),
                timeout_s=600,
                log_path=ctx.log_dir / f"cbindgen-{_slug(crate)}.log",
            )
            if not result.ok or not out.is_file():
                stdout, stderr = full_output(result)
                raise SandboxError(
                    f"cbindgen couldn't read crate {crate}",
                    reason="\n".join((stdout + stderr).strip().splitlines()[-10:]) or None,
                    hint='cbindgen reads the crate\'s public extern "C" functions; it must parse.',
                )
            headers.append(out)
        return headers

    def _note(self, ctx: AdapterContext, name: str) -> Path:
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.log_dir / name
        if not path.exists():
            path.write_text("", encoding="utf-8")
        return path


def _slug(crate: str) -> str:
    return crate.replace("/", "-").replace("\\", "-") or "root"


def _skipped(rel: PurePath) -> bool:
    return any(part in (".git", ".codecoremorph", "target", "build") for part in rel.parts)


def _text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def _cmake_files(worktree: Path) -> list[tuple[str, str]]:
    """The worktree's ``CMakeLists.txt`` files with their text, outside build trees."""
    found: list[tuple[str, str]] = []
    for path in sorted(worktree.rglob("CMakeLists.txt")):
        rel = path.relative_to(worktree)
        text = None if _skipped(rel) else _text(path)
        if text is not None:
            found.append((rel.as_posix(), text))
    return found


_NAME = re.compile(r'(?ms)^\s*\[package\]\s*$.*?^\s*name\s*=\s*"([^"]+)"')


def _package_name(manifest: str) -> str | None:
    match = _NAME.search(manifest)
    return match.group(1) if match else None


def _project_name(worktree: Path) -> str | None:
    """The CMake project's name, lowercased, for crate names (``textkit``)."""
    text = _text(worktree / "CMakeLists.txt")
    match = re.search(r"^\s*project\s*\(\s*([A-Za-z0-9_]+)", text or "", re.I | re.M)
    return match.group(1).lower() if match else None
