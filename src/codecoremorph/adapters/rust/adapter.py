"""The Rust adapter (SPEC §11.5, §11.6, D-56, D-58): Cargo builds and clippy with JSON
diagnostics, nextest tests, the ``rust-crate`` and ``rust-module`` scaffolds, rustfmt, the C
header a crate exports (cbindgen), the bridge headers of a module port, and a symbol index.

In a port run it is the *target* adapter of a migration pair: the C++ adapter builds and
tests the project through CMake, which builds the crates with Corrosion; this adapter adds
what only Cargo knows. Every command goes through ``ctx.executor``.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import time
from collections.abc import Callable, Collection
from pathlib import Path, PurePath, PurePosixPath
from typing import cast

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.c import rustports
from codecoremorph.adapters.rust import cports, ports, source
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
    nested_manifest_dirs,
    nextest_config,
    parse_cargo_messages,
    parse_nextest_junit,
    valid_crate_name,
    with_corrosion_block,
)
from codecoremorph.adapters.rust.scip import Document, ScipError, read_index
from codecoremorph.adapters.rust.symbols import mask, module_path, scan_crate
from codecoremorph.adapters.rust.tests import scan_tests, test_binary, test_id
from codecoremorph.core.errors import ConfigError, SandboxError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    ExecResult,
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
            # A port from Rust's detectors (D-75).
            queries=["rust_modules", "rust_programs", "test_cases"],
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
                ToolRequirement(name="bindgen", purpose="the C headers a C port keeps (D-68)"),
                *(
                    [ToolRequirement(name="rust-analyzer", purpose="a Rust source's index (D-75)")]
                    if lang.port_target is not None
                    else []
                ),
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
            env={**CARGO_ENV, **self._c_env(ctx), **(env or {})},
            timeout_s=timeout_s,
            log_path=ctx.log_dir / log,
        )
        stdout, stderr = full_output(result)
        return result.ok, stdout + "\n" + stderr, result.log_path, result.duration_s

    # ------------------------------------------------------------------ the C of a Rust → C port

    def _c_part(self, ctx: AdapterContext) -> dict[str, str] | None:
        """A crate being ported to C (D-75): its ported modules (module file → C header), or
        None when it has no C part. The crate is the root package or a workspace's member;
        its manifest names the modules."""
        modules: dict[str, str] = {}
        packages = find_packages(ctx.worktree, ctx.lang.exclude)
        for package in packages:
            modules.update(rustports.c_modules(_text(ctx.worktree / package / "Cargo.toml") or ""))
        if not packages or (not modules and not (ctx.worktree / rustports.C_CMAKE).is_file()):
            return None
        return modules

    def _c_env(self, ctx: AdapterContext) -> dict[str, str]:
        """What Cargo needs to link the C part: where CMake put the library, and the
        sanitizers' runtime, since the C is built with them (D-75)."""
        if self._c_part(ctx) is None:
            return {}
        return {
            rustports.LIB_DIR_ENV: f"{ctx.sandbox(ctx.build_dir)}/lib",
            "RUSTFLAGS": f"-C linker=clang -C link-arg={rustports.SANITIZER_FLAGS}",
        }

    def _c_configure(self, ctx: AdapterContext) -> list[Diagnostic]:
        """CMake configures the C part in the build directory (the C adapter, the port's
        target, builds the same tree), and bindgen writes each façade's ``sys`` module from
        its module's header."""
        modules = self._c_part(ctx)
        if modules is None:
            return []
        problems: list[Diagnostic] = []
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            rustports.configure_argv(ctx.sandbox(ctx.worktree), ctx.sandbox(ctx.build_dir)),
            cwd=ctx.sandbox(ctx.worktree),
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "c-configure.log",
        )
        if not result.ok:
            problems.append(self._c_failure("CMake couldn't configure the C under c/", result))
            return problems
        include = ctx.sandbox(ctx.worktree / rustports.C_DIR / "include")
        for module_file, header in sorted(modules.items()):
            if not (ctx.worktree / header).is_file():
                problems.append(
                    Diagnostic(
                        file_path=header,
                        severity=Severity.ERROR,
                        message=f"the header of the façade {module_file} is missing",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                )
                continue
            out = rustports.sys_path(module_file)
            (ctx.worktree / out).parent.mkdir(parents=True, exist_ok=True)
            ran = ctx.executor.run(
                rustports.bindgen_argv(
                    ctx.sandbox(ctx.worktree / header), ctx.sandbox(ctx.worktree / out), include
                ),
                cwd=ctx.sandbox(ctx.worktree),
                timeout_s=600,
                log_path=ctx.log_dir / f"bindgen-{_slug(out)}.log",
            )
            if not ran.ok:
                problems.append(self._c_failure(f"bindgen couldn't read {header}", ran, out))
        return problems

    def _c_build(self, ctx: AdapterContext) -> list[Diagnostic]:
        """CMake builds the C library before Cargo links it (D-75)."""
        if self._c_part(ctx) is None:
            return []
        name = rustports.linked_library(_text(ctx.worktree / rustports.CARGO_LINK) or "")
        if name is None:
            return [
                Diagnostic(
                    file_path=rustports.CARGO_LINK,
                    severity=Severity.ERROR,
                    message="c/cargo-link names no static C library (a static=<name> line): "
                    "the Rust can't link the C",
                    origin=DiagnosticOrigin.BUILD_SYSTEM,
                )
            ]
        if not (ctx.build_dir / "CMakeCache.txt").is_file():
            problems = self._c_configure(ctx)
            if problems:
                return problems
        result = ctx.executor.run(
            ["cmake", "--build", ctx.sandbox(ctx.build_dir), "--target", name],
            cwd=ctx.sandbox(ctx.worktree),
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "c-build.log",
        )
        if result.ok:
            return []
        return [self._c_failure("the C under c/ doesn't build", result)]

    def _c_failure(self, what: str, result: ExecResult, path: str | None = None) -> Diagnostic:
        stdout, stderr = full_output(result)
        tail = "\n".join((stdout + "\n" + stderr).strip().splitlines()[-30:])
        return Diagnostic(
            file_path=path or rustports.C_CMAKE,
            severity=Severity.ERROR,
            message=f"{what}:\n{tail}",
            origin=DiagnosticOrigin.BUILD_SYSTEM,
        )

    def _roots(self, ctx: AdapterContext) -> list[str]:
        return [ctx.sandbox(ctx.worktree), str(ctx.worktree)]

    def _check(self, ctx: AdapterContext, subcommand: str, *, analyzer: bool) -> BuildResult:
        started = time.monotonic()
        diagnostics: list[Diagnostic] = self._c_build(ctx)
        success = not diagnostics
        log_path = ctx.log_dir / f"cargo-{subcommand}.log"
        for crate in self.crates(ctx) if success else []:
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
        # A C port's sys modules (D-68) and a module port's bridge headers come first: the
        # crates use the former, and the C++ build includes the latter (D-58).
        _, diagnostics = self._sys_modules(ctx)
        diagnostics += self._bridge_headers(ctx)
        # A crate being ported to C (D-75): CMake configures its C, bindgen writes the façades'
        # sys modules.
        diagnostics += self._c_configure(ctx)
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
        the symbol map and a unit's context. A port's source (``ctx.lang.port_target``) also
        gets the references between its files from rust-analyzer's SCIP output, its targets
        and its tests, which order the port's units (D-75)."""
        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.cache_dir / INDEX_FILE_NAME
        rows: list[FileRow] = []
        symbols: list[SymbolRow] = []
        seen: set[str] = set()
        port_source = ctx.lang.port_target is not None
        extra = source.SourceIndex()
        problems: list[str] = []
        packages = find_packages(ctx.worktree, ctx.lang.exclude)
        names: dict[str, str] = {}
        for package in packages:
            manifest = _text(ctx.worktree / package / "Cargo.toml") or ""
            names[package] = _package_name(manifest) or posixpath.basename(package)
        known: dict[str, str] = {}
        if port_source:
            # A workspace's own manifest and lock are the build too (the packages' come below).
            for root in find_crates(ctx.worktree, ctx.lang.exclude):
                if root in names:
                    continue
                for build_file in ("Cargo.toml", "Cargo.lock"):
                    path_ = ctx.worktree / root / build_file
                    if path_.is_file():
                        rel = path_.relative_to(ctx.worktree).as_posix()
                        digest = hashlib.sha256(path_.read_bytes()).hexdigest()
                        rows.append((rel, "toml", digest, "build", 0, 0, "utf-8", "lf"))
        excluded = PathMatcher(ctx.lang.exclude)
        for package in packages:
            base = ctx.worktree / package
            sources: dict[str, tuple[str, str]] = {}
            # The package's own files: not an excluded path's, nor those of a package below it
            # (the root package of shlex holds fuzz/, a cargo-fuzz workspace of its own).
            nested = [f"{d}/" for d in nested_manifest_dirs(ctx.worktree, package)]
            for file in sorted(base.rglob("*.rs")):
                rel = file.relative_to(ctx.worktree).as_posix()
                if rel in seen or _skipped(PurePosixPath(rel)) or excluded.matches(rel):
                    continue
                if any(rel.startswith(d) for d in nested):
                    continue
                seen.add(rel)
                digest = hashlib.sha256(file.read_bytes()).hexdigest()
                inner = file.relative_to(base).as_posix()
                role = source.file_role(inner).value if port_source else "source"
                rows.append((rel, "rust", digest, role, 0, 0, "utf-8", "lf"))
                text = _text(file)
                if text is not None:
                    sources[inner] = (rel, text)
            manifest = _text(base / "Cargo.toml") or ""
            name = names[package]
            if port_source:
                # The manifest and its lock are the build: a port's units may change them, and
                # its retirement deletes them.
                for build_file in ("Cargo.toml", "Cargo.lock"):
                    if (base / build_file).is_file():
                        rel = (base / build_file).relative_to(ctx.worktree).as_posix()
                        digest = hashlib.sha256((base / build_file).read_bytes()).hexdigest()
                        rows.append((rel, "toml", digest, "build", 0, 0, "utf-8", "lf"))
            for item in scan_crate(name.replace("-", "_"), sources):
                known[item.path] = item.file
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
                        # A port's source keeps its public items behind façades: I-6 checks
                        # that none goes (D-75).
                        "c_abi"
                        if item.c_abi
                        else "public_api"
                        if port_source and item.public
                        else "none",
                    )
                )
            if port_source:
                texts = {inner: text for inner, (_, text) in sources.items()}
                source.add_targets_and_tests(
                    extra,
                    package=package,
                    name=name,
                    manifest=manifest,
                    files=texts,
                    local=set(names.values()),
                )
        if port_source:
            # Once every package's items are known: a program's use of its workspace's library
            # ties its files to the library's.
            crates = {name: name.replace("-", "_") for name in names.values()}
            for package in packages:
                documents = self._scip(ctx, package, problems)
                if documents is not None:
                    source.add_references(
                        extra,
                        documents,
                        package=package,
                        crate=crates[names[package]],
                        crates=crates,
                        known=known,
                    )
        symbols += extra.symbols
        meta = IndexMeta(
            adapter="rust",
            adapter_version=self.version,
            stats={
                "files": len(rows),
                "symbols": len(symbols),
                **({"references": len(extra.references)} if port_source else {}),
            },
        )
        with IndexStore.open(path, reset=True) as store:
            store.write(files=rows, symbols=symbols, references=extra.references)
            if port_source:
                store.set_build_targets(extra.targets)
                store.set_test_entries(extra.tests)
            store.set_meta(meta)
        return SymbolIndex(path=path, meta=meta, problems=problems)

    def _scip(
        self, ctx: AdapterContext, package: str, problems: list[str]
    ) -> list[Document] | None:
        """rust-analyzer's SCIP index of one package (D-75), or None, with the reason in
        ``problems``: the planner then orders the modules by path alone."""
        out = ctx.cache_dir / "scip" / f"{_slug(package)}.scip"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            ["rust-analyzer", "scip", ".", "--output", ctx.sandbox(out)],
            cwd=ctx.sandbox(ctx.worktree / package),
            env={**CARGO_ENV, "CARGO_TARGET_DIR": ctx.sandbox(ctx.cache_dir / TARGET_DIR)},
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / f"rust-analyzer-scip-{_slug(package)}.log",
        )
        if not result.ok or not out.is_file():
            stdout, stderr = full_output(result)
            tail = "\n".join((stdout + stderr).strip().splitlines()[-5:])
            problems.append(f"rust-analyzer couldn't index {package}: {tail or 'no output'}")
            return None
        try:
            return read_index(out.read_bytes())
        except ScipError as exc:
            problems.append(f"rust-analyzer's index of {package} is unreadable: {exc}")
            return None

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
        if spec.options.get("language") == "c":
            return self._c_module_scaffold(ctx, spec, files)
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

    # ------------------------------------------------------------------ C ports (D-68)

    def _c_module_scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> tuple[list[CodemodChange], list[Diagnostic]]:
        """``rust-module`` for a C translation unit (``options.language: c``, D-68): the
        workspace with its release profile, the library's crate (its archive and kept headers
        in the manifest), the unit's module and bridge module, the symbol map, the lockfile,
        the Make integration and the ``sys`` module. ``options``: ``crate``, ``module``,
        ``build_target``, ``archive`` and ``headers``, from the unit (``PortUnit``)."""
        crate = spec.options["crate"]
        module = spec.options["module"]
        library = spec.options["build_target"]
        archive = spec.options.get("archive", "")
        headers = [h for h in spec.options.get("headers", "").split(",") if h]
        if not archive:
            raise ConfigError(
                "the rust-module codemod needs the C library's static archive",
                reason=f"the build gives library {library} no .a archive",
                hint="A C port folds each crate into its library's archive (D-68).",
            )
        year = self._edition_year(ctx, "rust-module")
        workspace = cports.WORKSPACE_DIR
        package = f"{workspace}/{crate}"
        sources = sorted(f for f in files if f.lower().endswith((".c", ".h")))
        changes: list[CodemodChange] = []
        write = self._writer(ctx, changes)

        def current(rel: str) -> str | None:
            return _text(ctx.worktree / rel)

        manifest = current(f"{workspace}/Cargo.toml")
        write(
            f"{workspace}/Cargo.toml",
            cports.workspace_manifest([crate])
            if manifest is None
            else cports.with_release_profile(ports.with_member(manifest, crate)),
        )
        if current(f"{workspace}/.gitignore") is None:
            write(f"{workspace}/.gitignore", "/target/\n")  # where the Make build puts it
        package_toml = current(f"{package}/Cargo.toml")
        if package_toml is None:
            write(
                f"{package}/Cargo.toml",
                cports.package_manifest(crate, year, library, archive, headers),
            )
            write(f"{package}/src/lib.rs", cports.lib_rs(library))
            write(f"{package}/src/bridge/mod.rs", cports.bridge_mod_rs())
        else:
            write(f"{package}/Cargo.toml", cports.with_headers(package_toml, headers))
        if current(f"{package}/{cports.SYS_FILE}") is None:
            write(f"{package}/{cports.SYS_FILE}", cports.placeholder_sys())
        if current(f"{package}/src/{module}.rs") is None:
            write(f"{package}/src/{module}.rs", cports.module_rs(sources))
        lib = current(f"{package}/src/lib.rs") or cports.lib_rs(library)
        write(f"{package}/src/lib.rs", ports.with_module(lib, module))
        if current(f"{package}/src/bridge/{module}.rs") is None:
            write(f"{package}/src/bridge/{module}.rs", cports.bridge_module_rs(module, sources))
        bridge = current(f"{package}/src/bridge/mod.rs") or cports.bridge_mod_rs()
        write(f"{package}/src/bridge/mod.rs", ports.with_module(bridge, module, public=False))
        if current(ports.SYMBOL_MAP) is None:
            write(ports.SYMBOL_MAP, cports.SYMBOL_MAP_TEXT)
        problems = self._lock_workspace(ctx, workspace, changes)
        problems += self._make_integration(ctx, write)
        written, sys_problems = self._sys_modules(ctx)
        changes += [CodemodChange(file=f, replacements=1) for f in written]
        once = {c.file: c for c in changes}  # a file written twice is one change
        return list(once.values()), problems + sys_problems

    def _c_bin_scaffold(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str]
    ) -> tuple[list[CodemodChange], list[Diagnostic]]:
        """``rust-bin`` for a C program (``options.language: c``, D-68): a package whose
        ``bin`` keeps the program's name, with the library crates as dependencies, and the
        program's Make rule copying it to the program's path, so ``make test`` and scripts run
        Rust. The C sources stay for the agent to port, then delete. ``options``: ``crate``,
        ``bin``, ``build_target``, ``artifact``, ``dependencies``."""
        crate = spec.options["crate"]
        name = spec.options["bin"]
        artifact = spec.options.get("artifact", "")
        if not artifact:
            raise ConfigError(
                "the rust-bin codemod needs the C program's path",
                reason=f"the build gives program {name} no artifact",
                hint="A C port copies the Rust program to the program's path (D-68).",
            )
        year = self._edition_year(ctx, "rust-bin")
        dependencies = [d for d in spec.options.get("dependencies", "").split(",") if d]
        workspace = cports.WORKSPACE_DIR
        package = f"{workspace}/{crate}"
        sources = sorted(f for f in files if f.lower().endswith((".c", ".h")))
        changes: list[CodemodChange] = []
        write = self._writer(ctx, changes)
        manifest = _text(ctx.worktree / workspace / "Cargo.toml")
        write(
            f"{workspace}/Cargo.toml",
            cports.workspace_manifest([crate])
            if manifest is None
            else cports.with_release_profile(ports.with_member(manifest, crate)),
        )
        if _text(ctx.worktree / package / "Cargo.toml") is None:
            write(
                f"{package}/Cargo.toml",
                cports.bin_manifest(crate, year, name, artifact, dependencies),
            )
        if _text(ctx.worktree / package / "src" / "main.rs") is None:
            write(f"{package}/src/main.rs", cports.main_rs(sources))
        if _text(ctx.worktree / ports.TEST_MAP) is None:
            write(ports.TEST_MAP, cports.TEST_MAP_TEXT)
        problems = self._lock_workspace(ctx, workspace, changes)
        problems += self._make_integration(ctx, write)
        once = {c.file: c for c in changes}
        return list(once.values()), problems

    def _c_crates(self, ctx: AdapterContext) -> list[tuple[str, str]]:
        """The workspace's C port crates (D-68): ``(package, manifest text)``."""
        found: list[tuple[str, str]] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            manifest = _text(ctx.worktree / package / "Cargo.toml") or ""
            if cports.manifest_archive(manifest) is not None:
                found.append((package, manifest))
        return found

    def _make_integration(
        self, ctx: AdapterContext, write: Callable[[str, str], None]
    ) -> list[Diagnostic]:
        """Each C port crate in the Makefile that makes its library's archive (the archive's
        rule folds it in, the links get Rust's native libraries) and each C program's package
        in the Makefile that makes the program (its rule copies the Rust program), with the
        marked block that builds the workspace (D-68)."""
        crates: dict[str, list[cports.Crate]] = {}
        programs: dict[str, list[cports.Program]] = {}
        am_libraries: dict[str, list[cports.AmLibrary]] = {}
        am_programs: dict[str, list[cports.AmProgram]] = {}
        problems: list[Diagnostic] = []
        release = f"{cports.WORKSPACE_DIR}/target/release"

        def missing(package: str, path: str, what: str) -> Diagnostic:
            return Diagnostic(
                file_path=f"{package}/Cargo.toml",
                severity=Severity.ERROR,
                message=f"no Makefile has a rule that makes {path}, so {what}; the rule must be "
                "in a Makefile (or the Makefile.in that configure writes it from) beside or "
                "above it, or a Makefile.am there must list it",
                origin=DiagnosticOrigin.BUILD_SYSTEM,
            )

        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            manifest = _text(ctx.worktree / package / "Cargo.toml") or ""
            archive = cports.manifest_archive(manifest)
            program = cports.manifest_program(manifest)
            name = (_package_name(manifest) or posixpath.basename(package)).replace("-", "_")
            if archive is not None and (am := self._automake_for(ctx, archive, False)):
                makefile, canonical, libtool = am
                am_libraries.setdefault(makefile, []).append(
                    cports.AmLibrary(
                        name=name,
                        canonical=canonical,
                        libtool=libtool,
                        staticlib=f"{release}/lib{name}.a",
                        archive=posixpath.basename(archive),
                    )
                )
                continue
            if program is not None and (am := self._automake_for(ctx, program[1], True)):
                makefile, canonical, _ = am
                am_programs.setdefault(makefile, []).append(
                    cports.AmProgram(
                        name=program[0], canonical=canonical, binary=f"{release}/{program[0]}"
                    )
                )
                continue
            if archive is not None:
                found = self._makefile_for(ctx, archive)
                if found is None:
                    problems.append(missing(package, archive, "the crate can't be folded in"))
                    continue
                makefile, target = found
                directory = posixpath.dirname(makefile) or "."
                crates.setdefault(makefile, []).append(
                    cports.Crate(
                        name=name,
                        library=cports.manifest_library(manifest) or "",
                        archive=posixpath.relpath(archive, directory),
                        staticlib=posixpath.relpath(f"{release}/lib{name}.a", directory),
                        target=target,
                    )
                )
            elif program is not None:
                bin_name, artifact = program
                found = self._makefile_for(ctx, artifact)
                if found is None:
                    problems.append(missing(package, artifact, "the Rust program can't take over"))
                    continue
                makefile, target = found
                directory = posixpath.dirname(makefile) or "."
                programs.setdefault(makefile, []).append(
                    cports.Program(
                        name=bin_name,
                        binary=posixpath.relpath(f"{release}/{bin_name}", directory),
                        target=target,
                    )
                )
        makefiles = sorted({*crates, *programs})
        if not makefiles and not am_libraries and not am_programs:
            return problems
        libs: list[str] = []
        if crates or am_libraries:
            found_libs = self._native_libs(ctx)
            if isinstance(found_libs, Diagnostic):
                return [*problems, found_libs]
            libs = found_libs
        for makefile in sorted({*am_libraries, *am_programs}):
            original = _text(ctx.worktree / makefile) or ""
            text = original
            here = am_libraries.get(makefile, [])
            for am_program in am_programs.get(makefile, []):
                text = cports.with_am_program(text, am_program.canonical)
            text = cports.with_am_native_libs(
                text, {library.archive for library in here if not library.libtool}
            )
            block = cports.am_block(
                here,
                am_programs.get(makefile, []),
                libs,
                f"{cports.WORKSPACE_DIR}/Cargo.toml",
                cports.am_defined(text),
            )
            text = cports.with_make_block(text, block)
            if text != original:
                write(makefile, text)
        for makefile in makefiles:
            original = _text(ctx.worktree / makefile) or ""
            text = original
            here = crates.get(makefile, [])
            for crate in here:
                text = cports.fold_archive(text, crate)
            text = cports.with_native_libs(text, here)
            for program in programs.get(makefile, []):
                text = cports.with_rust_program(text, program)
            manifest = posixpath.relpath(
                f"{cports.WORKSPACE_DIR}/Cargo.toml", posixpath.dirname(makefile) or "."
            )
            block = cports.make_block(here, manifest, libs, programs.get(makefile, []))
            text = cports.with_make_block(text, block)
            if text != original:
                write(makefile, text)
        return problems

    def _makefile_for(self, ctx: AdapterContext, archive: str) -> tuple[str, str] | None:
        """``(Makefile, target)`` for the rule that makes ``archive`` (or a program): in its
        directory or one above it, the configure template (``Makefile.in``) before the
        Makefile it writes, then make's own order. Names compare exactly, also where the file
        system ignores case (Windows), since make's don't."""
        directory = posixpath.dirname(archive)
        while True:
            try:
                present = {p.name for p in (ctx.worktree / directory).iterdir() if p.is_file()}
            except OSError:
                present = set()
            for name in ("Makefile.in", *cports.MAKEFILES):
                if name not in present or (name == "Makefile.in" and cports.AUTOMAKE in present):
                    continue  # automake writes that Makefile.in: _automake_for edits its input
                rel = posixpath.join(directory, name) if directory else name
                text = _text(ctx.worktree / rel)
                if text is None:
                    continue
                target = cports.rule_making(text, posixpath.relpath(archive, directory or "."))
                if target is not None:
                    return rel, target
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def _automake_for(
        self, ctx: AdapterContext, path: str, program: bool
    ) -> tuple[str, str, bool] | None:
        """``(Makefile.am, canonical name, libtool)`` for the automake file, in ``path``'s
        directory or one above it, that lists the library whose archive is ``path`` (libtool's
        ``<dir>/.libs/libfoo.a`` is ``<dir>/libfoo.la``) or, with ``program``, the program
        (D-70). None for a project without automake."""
        directory, name = posixpath.split(path)
        libtool = False
        if program and posixpath.basename(directory) == ".libs":  # libtool's program, behind
            directory, name = posixpath.dirname(directory), name.removeprefix("lt-")  # a wrapper
        elif not program and posixpath.basename(directory) == ".libs" and name.endswith(".a"):
            directory, name, libtool = posixpath.dirname(directory), f"{name[:-2]}.la", True
        primaries = ("PROGRAMS",) if program else ("LTLIBRARIES",) if libtool else ("LIBRARIES",)
        start = directory
        while True:
            try:
                present = {p.name for p in (ctx.worktree / directory).iterdir() if p.is_file()}
            except OSError:
                present = set()
            if cports.AUTOMAKE in present:
                rel = posixpath.join(directory, cports.AUTOMAKE) if directory else cports.AUTOMAKE
                listed = posixpath.relpath(posixpath.join(start, name), directory or ".")
                canonical = cports.am_target(_text(ctx.worktree / rel) or "", listed, primaries)
                if canonical is not None:
                    return rel, canonical, libtool
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def _native_libs(self, ctx: AdapterContext) -> list[str] | Diagnostic:
        """The libraries a staticlib needs on this target, as rustc lists them for an empty
        crate (the standard library's)."""
        work = ctx.cache_dir / "native-libs"
        work.mkdir(parents=True, exist_ok=True)
        probe = work / "probe.rs"
        probe.write_text("", encoding="utf-8")
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            [
                "rustc",
                "--crate-type",
                "staticlib",
                "--crate-name",
                "probe",
                "--print",
                "native-static-libs",
                "-o",
                ctx.sandbox(work / "libprobe.a"),
                ctx.sandbox(probe),
            ],
            cwd=ctx.sandbox(work),
            timeout_s=300,
            log_path=ctx.log_dir / "rustc-native-libs.log",
        )
        stdout, stderr = full_output(result)
        libs = cports.native_libs(stdout + stderr) if result.ok else None
        if libs is None:
            tail = "\n".join((stdout + stderr).strip().splitlines()[-10:])
            return Diagnostic(
                file_path=f"{cports.WORKSPACE_DIR}/Cargo.toml",
                severity=Severity.ERROR,
                message=f"rustc didn't list the native libraries a staticlib needs:\n{tail}",
                origin=DiagnosticOrigin.BUILD_SYSTEM,
            )
        return libs

    def _sys_modules(self, ctx: AdapterContext) -> tuple[list[str], list[Diagnostic]]:
        """bindgen writes each C port crate's ``sys`` module (D-68) from the headers its
        manifest keeps, with the include directories and macros of the C build's compile
        database (in the build directory), the crate's own exports left out. A crate whose
        headers are all gone, or with no C build left to read them with, keeps the module it
        has. Returns the files it changed."""
        written: list[str] = []
        problems: list[Diagnostic] = []
        database = ctx.build_dir / "compile_commands.json"
        try:
            entries = cast(
                list[dict[str, object]], json.loads(database.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            entries = []
        clang = cports.clang_args(entries)
        for package, manifest in self._c_crates(ctx):
            headers = [h for h in cports.manifest_headers(manifest) if (ctx.worktree / h).is_file()]
            if not headers:
                continue
            base = ctx.worktree / package
            out = base / cports.SYS_FILE
            before = out.read_bytes() if out.is_file() else None
            placeholder = cports.placeholder_sys().encode("utf-8")
            if not entries and before is not None and before.strip() != placeholder.strip():
                continue  # no C build to read the headers with (it was retired): keep it
            name = (_package_name(manifest) or posixpath.basename(package)).replace("-", "_")
            sources: dict[str, tuple[str, str]] = {}
            for file in sorted(base.rglob("*.rs")):
                inner = file.relative_to(base).as_posix()
                rel = file.relative_to(ctx.worktree).as_posix()
                if inner == cports.SYS_FILE or _skipped(PurePosixPath(rel)):
                    continue
                text = _text(file)
                if text is not None:
                    sources[inner] = (rel, text)
            exports = {i.path.rsplit("::", 1)[-1] for i in scan_crate(name, sources) if i.c_abi}
            work = ctx.cache_dir / "bindgen"
            work.mkdir(parents=True, exist_ok=True)
            wrapper = work / f"{_slug(package)}.h"
            wrapper.write_text(
                cports.wrapper_header([ctx.sandbox(ctx.worktree / h) for h in headers]),
                encoding="utf-8",
            )
            argv = cports.bindgen_argv(
                ctx.sandbox(wrapper), ctx.sandbox(out), ctx.sandbox(ctx.worktree), exports, clang
            )
            ctx.log_dir.mkdir(parents=True, exist_ok=True)
            result = ctx.executor.run(
                argv,
                cwd=ctx.sandbox(base),
                timeout_s=600,
                log_path=ctx.log_dir / f"bindgen-{_slug(package)}.log",
            )
            if not result.ok:
                stdout, stderr = full_output(result)
                tail = "\n".join((stdout + stderr).strip().splitlines()[-15:])
                problems.append(
                    Diagnostic(
                        file_path=f"{package}/{cports.SYS_FILE}",
                        severity=Severity.ERROR,
                        message=f"bindgen couldn't read the kept headers "
                        f"({', '.join(headers)}):\n{tail}",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                )
                continue
            if out.is_file() and out.read_bytes() != before:
                written.append(f"{package}/{cports.SYS_FILE}")
        return written, problems

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
        if spec.options.get("language") == "c":
            return self._c_bin_scaffold(ctx, spec, files)
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
        c_port = spec.options.get("language") == "c"  # D-68
        sources = sorted(f for f in files if f.lower().endswith(ports.CPP_EXTENSIONS))
        test_file = f"{package}/tests/{module}.rs"
        if _text(ctx.worktree / test_file) is None:
            template = cports.test_rs if c_port else ports.test_rs
            write(test_file, template(", ".join(sources) or module))
        dependencies = [
            d for d in spec.options.get("dependencies", "").split(",") if d and d != crate
        ]
        if dependencies:
            write(f"{package}/Cargo.toml", ports.with_dev_dependencies(manifest, dependencies))
        test_map = _text(ctx.worktree / ports.TEST_MAP) or (
            cports.TEST_MAP_TEXT if c_port else ports.TEST_MAP_TEXT
        )
        wrappers = [w for w in spec.options.get("wrappers", "").split(",") if w]
        write(
            ports.TEST_MAP,
            ports.with_test_entries(
                test_map, [(w, ports.WRAPPER_NOTE) for w in wrappers], "c" if c_port else "cpp"
            ),
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
        (the C++ and CMake files left, or a C port's C and Make files, D-68) and, unless
        ``keep`` is ``true`` (the kept API needs it), each crate's bridge: ``src/bridge/`` (with a
        C port's ``sys``), ``include/``, ``cbindgen.toml``, the ``staticlib`` crate type and the
        build's metadata, with ``#![forbid(unsafe_code)]`` at the root. With ``keep``, a C port's
        crates stop naming the headers it deleted (D-72)."""
        changes: list[CodemodChange] = []
        workspace = ports.WORKSPACE_DIR
        for rel in sorted(files):
            path = ctx.worktree / rel
            if rel.startswith(f"{workspace}/") or not path.is_file():
                continue
            path.unlink()
            changes.append(CodemodChange(file=rel, replacements=1))
        write = self._writer(ctx, changes)
        if spec.options.get("keep") == "true":
            gone = [c.file for c in changes]
            for package, manifest in self._c_crates(ctx):
                write(f"{package}/Cargo.toml", cports.without_headers(manifest, gone))
            return list({c.file: c for c in changes}.values())
        root = _text(ctx.worktree / workspace / "Cargo.toml")
        if root is not None:
            write(
                f"{workspace}/Cargo.toml", cports.retired_workspace(ports.retired_workspace(root))
            )
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            if not package.startswith(f"{workspace}/"):
                continue
            base = ctx.worktree / package
            manifest = _text(base / "Cargo.toml") or ""
            name = (_package_name(manifest) or posixpath.basename(package)).replace("-", "_")
            target = ports.cmake_target(manifest)
            library = cports.manifest_library(manifest)
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
                retired = cports.retired_lib_rs(lib, library)
                write(f"{package}/src/lib.rs", ports.retired_lib_rs(retired, name, target))
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

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        """A port from Rust's detectors (D-75): ``rust_modules``, each module file of a
        library (one site, the module it is); ``rust_programs``, each program's root file;
        ``test_cases``, each test, by its ``nextest:`` ID. Read from the files and manifests;
        no command runs."""
        if name not in ("rust_modules", "rust_programs", "test_cases"):
            return super().query(ctx, name, files)
        wanted = set(files)
        sites: list[RuleSite] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            base = ctx.worktree / package
            manifest = _text(base / "Cargo.toml") or ""
            package_name = _package_name(manifest) or posixpath.basename(package)
            crate = package_name.replace("-", "_")
            for file in sorted(base.rglob("*.rs")):
                rel = file.relative_to(ctx.worktree).as_posix()
                if rel not in wanted or _skipped(PurePosixPath(rel)):
                    continue
                inner = file.relative_to(base).as_posix()
                if name == "rust_modules":
                    module = module_path(crate, inner)
                    if module is not None:
                        text = f"{module}: port it, keeping the module as a façade"
                        sites.append(RuleSite(file=rel, line=1, text=text))
                    continue
                binary = test_binary(package_name, manifest, inner)
                if name == "rust_programs":
                    if binary is not None and "::bin/" in binary[0]:
                        sites.append(RuleSite(file=rel, line=1, text=binary[0]))
                    continue
                text = _text(file)
                if binary is None or text is None:
                    continue
                for test in scan_tests(text):
                    test_name = test_id(binary[0], binary[1], test.path)
                    sites.append(RuleSite(file=rel, line=test.line, text=test_name))
        return sites

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
        """clippy over every crate: compiler and clippy diagnostics, by file; and in a C port's
        crate, a ``#[repr(C)]`` copy of a kept header's type (D-68)."""
        return self.clippy(ctx).diagnostics + self._c_type_copies(ctx)

    def _c_type_copies(self, ctx: AdapterContext) -> list[Diagnostic]:
        found: list[Diagnostic] = []
        for package, _ in self._c_crates(ctx):
            base = ctx.worktree / package
            sys_text = _text(base / cports.SYS_FILE) or ""
            files: dict[str, str] = {}
            for file in sorted((base / "src").rglob("*.rs")):
                if file.relative_to(base).as_posix() == cports.SYS_FILE:
                    continue
                text = _text(file)
                if text is not None:
                    files[file.relative_to(ctx.worktree).as_posix()] = mask(text)
            for path, line, name, original in cports.copied_c_types(sys_text, files):
                found.append(
                    Diagnostic(
                        file_path=path,
                        line=line,
                        severity=Severity.ERROR,
                        code="codecoremorph::c-type-copy",
                        message=f"`{name}` copies the C type `sys::{original}` of the kept "
                        f"headers; use `sys::{original}`, so its layout is C's",
                        origin=DiagnosticOrigin.ANALYZER,
                    )
                )
        return found

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
        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        headers: list[Path] = []
        for crate in crates:
            # A C port's header includes the kept headers, whose types it uses (D-68).
            kept = [
                ctx.sandbox(ctx.worktree / h)
                for h in cports.manifest_headers(_text(ctx.worktree / crate / "Cargo.toml") or "")
                if (ctx.worktree / h).is_file()
            ]
            config = ctx.cache_dir / f"cbindgen-{_slug(crate)}.toml"
            config.write_text(
                cports.cbindgen_config(kept) if kept else cbindgen_config(), encoding="utf-8"
            )
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
