"""The Rust adapter (SPEC §11.5, D-56): Cargo builds and clippy with JSON diagnostics, nextest
tests, the ``rust-crate`` scaffold, rustfmt, and the C header a crate exports (cbindgen).

In a port run it is the *target* adapter of a migration pair: the C++ adapter builds and
tests the project through CMake, which builds the crates with Corrosion; this adapter adds
what only Cargo knows. Every command goes through ``ctx.executor``.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import time
from pathlib import Path, PurePath, PurePosixPath

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.rust.cargo import (
    EDITIONS,
    NEXTEST_PROFILE,
    cbindgen_config,
    crate_files,
    edition,
    fetched_packages,
    find_crates,
    library_compiling,
    nextest_config,
    parse_cargo_messages,
    parse_nextest_junit,
    valid_crate_name,
    with_corrosion_block,
)
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
from codecoremorph.store.index import INDEX_FILE_NAME, FileRow, IndexStore

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
            codemod_tools=["rust-crate", "rustfmt"],
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
        diagnostics: list[Diagnostic] = []
        log_path = self._note(ctx, "cargo-configure.log")
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
        """The crates' Rust files (Phase 12 indexes no Rust symbols)."""
        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.cache_dir / INDEX_FILE_NAME
        rows: list[FileRow] = []
        for crate in self.crates(ctx):
            for file in sorted((ctx.worktree / crate).rglob("*.rs")):
                rel = file.relative_to(ctx.worktree).as_posix()
                if _skipped(PurePosixPath(rel)):
                    continue
                digest = hashlib.sha256(file.read_bytes()).hexdigest()
                rows.append((rel, "rust", digest, "source", 0, 0, "utf-8", "lf"))
        meta = IndexMeta(adapter="rust", adapter_version=self.version, stats={"files": len(rows)})
        with IndexStore.open(path, reset=True) as store:
            store.write(files=rows)
            store.set_meta(meta)
        return SymbolIndex(path=path, meta=meta)

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        started = time.monotonic()
        changes: list[CodemodChange] = []
        skipped: list[SkippedReplacement] = []
        for spec in codemods:
            if spec.tool == "rust-crate":
                changes += self._scaffold(ctx, spec, files)
            elif spec.tool == "rustfmt":
                changes += self._rustfmt(ctx)
            else:
                skipped.append(
                    SkippedReplacement(file="-", check=spec.tool, reason="not a Rust codemod")
                )
        return CodemodResult(
            changes=changes, skipped=skipped, duration_s=time.monotonic() - started
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
            c for c in self.crates(ctx) if any(c == "." or f.startswith(f"{c}/") for f in files)
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


def _project_name(worktree: Path) -> str | None:
    """The CMake project's name, lowercased, for crate names (``textkit``)."""
    text = _text(worktree / "CMakeLists.txt")
    match = re.search(r"^\s*project\s*\(\s*([A-Za-z0-9_]+)", text or "", re.I | re.M)
    return match.group(1).lower() if match else None
