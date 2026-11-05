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
import time
from collections.abc import Collection
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
            codemod_tools=["rust-crate", "rust-module", "rustfmt"],
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
                [subcommand, "--locked", "--message-format=json"],
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
        return self._check(ctx, "build", analyzer=False)

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
        and no file but the root's ``mod bridge`` declaration may allow unsafe code."""
        sites: list[RuleSite] = []
        for package in find_packages(ctx.worktree, ctx.lang.exclude):
            src = ctx.worktree / package / "src"
            lib = src / "lib.rs"
            if not (src / "bridge").is_dir() and not (src / "bridge.rs").is_file():
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
