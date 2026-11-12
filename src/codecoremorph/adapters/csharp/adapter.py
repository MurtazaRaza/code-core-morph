"""The C# adapter: MSBuild builds and VSTest tests with the .NET SDK, Roslyn indexing, and
``dotnet format`` codemods (SPEC §7.1, §11.3, D-65).

``dotnet`` runs in a mirror of the worktree in the adapter's cache (it writes ``bin/`` and
``obj/`` beside every project, which the worktree must never hold), always offline but in the
restore step, which is the adapter's configure. Every command goes through ``ctx.executor``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.cpp import csharpports
from codecoremorph.adapters.cpp.diagnostics import parse_compiler_output
from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.adapters.csharp import formatting, mirror
from codecoremorph.adapters.csharp.detect import detect as detect_project
from codecoremorph.adapters.csharp.diagnostics import PathMap, build_failure, parse_dotnet_output
from codecoremorph.adapters.csharp.dotnet import (
    INSTALL_HINT,
    MSBUILD_SWITCHES,
    DotnetToolchain,
    run_environment,
)
from codecoremorph.adapters.csharp.indexing import run_index
from codecoremorph.adapters.csharp.msbuild import (
    PROPS_FILES,
    STANDARDS,
    Layout,
    bump_project,
    code_constructs,
    entry_point,
    inherited_properties,
    is_project,
    layout,
    lowest_standard,
    uncommented,
)
from codecoremorph.adapters.csharp.testcode import assertions, disabled_markers, find_cases
from codecoremorph.adapters.csharp.trx import read_reports, run_errors, selection_filter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.adapters.rust import csharpports as rustports
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    FileRole,
    LanguageConfig,
    ProjectDetection,
    ReferenceKind,
    RuleSite,
    Severity,
    SkippedReplacement,
    SymbolIndex,
    SymbolKind,
    TargetKind,
    TestAssertions,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    ToolchainSpec,
    ToolRequirement,
)
from codecoremorph.patching.textfile import TextFile
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

log = logging.getLogger(__name__)

DOTNET_DIR = "dotnet"  # in ctx.cache_dir: the mirror, NuGet's folders, the step's records
GENERATED_SOLUTION = "codecoremorph.slnx"
LAST_BUILD = "last-build-warnings.json"
MAX_FORMAT_PASSES = 4
_NULLABLE_CODES = re.compile(r"^CS8(?:6\d\d|7\d\d)$")  # CS8600-CS8799: nullable warnings
_NULLABLE_ENABLED = re.compile(r"^\s*#nullable\s+enable\b", re.MULTILINE)
_PROJECT_NULLABLE = re.compile(r"<Nullable>\s*(?:enable|warnings|annotations)\s*</Nullable>", re.I)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.codecoremorph-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _read_text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, ValueError):
        return None


class CSharpAdapter(LanguageAdapter):
    """C# projects built with the .NET SDK (MSBuild)."""

    api_version = "1"
    language = "csharp"
    version = "1"

    # ------------------------------------------------------------------ description

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            extract_assertions=True,
            detect_disabled_tests=True,
            codemod_tools=["dotnet-format", "msbuild-lang-version"],
            queries=[
                "test_cases",
                "record_candidates",
                "nullable_disabled",
                "csharp_types",
                "csharp_programs",
            ],
            standards=list(STANDARDS),
        )

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(
            language="csharp",
            image="codecoremorph-csharp",
            tools=[
                ToolRequirement(
                    name="dotnet",
                    min_version="10.0",
                    version_args=["--version"],
                    purpose=".NET SDK",
                ),
            ],
            install_hint=INSTALL_HINT,
        )

    # ------------------------------------------------------------------ helpers

    def _tools(self, ctx: AdapterContext) -> DotnetToolchain:
        windows = ctx.executor.kind == "native" and sys.platform == "win32"
        return DotnetToolchain.from_env(ctx.executor.toolchain_env, windows=windows)

    @staticmethod
    def _root(ctx: AdapterContext) -> Path:
        return ctx.cache_dir / DOTNET_DIR

    def _mirror(self, ctx: AdapterContext) -> Path:
        return self._root(ctx) / "project"

    def _paths(self, ctx: AdapterContext) -> PathMap:
        folds = ctx.executor.kind == "native" and sys.platform in ("win32", "darwin")
        mirror_dir = self._mirror(ctx)
        return PathMap([ctx.sandbox(mirror_dir), str(mirror_dir)], case_insensitive=folds)

    def _layout(self, ctx: AdapterContext) -> Layout:
        return layout(ctx.worktree, PathMatcher(ctx.lang.exclude))

    def _entry(self, ctx: AdapterContext, lay: Layout) -> str:
        """The solution or project to build, as the sandbox sees it: the project's own, or a
        solution in the cache listing every project."""
        own = entry_point(ctx.worktree, lay)
        if own is not None:
            return ctx.sandbox(self._mirror(ctx) / own)
        path = self._root(ctx) / GENERATED_SOLUTION
        lines = ["<Solution>"]
        lines += [f'  <Project Path="project/{p}" />' for p in lay.projects]
        lines.append("</Solution>")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ctx.sandbox(path)

    def _env(self, ctx: AdapterContext) -> dict[str, str]:
        root = self._root(ctx)
        for name in ("home", "packages", "http-cache", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return run_environment(
            ctx.sandbox(root / "home"),
            ctx.sandbox(root / "packages"),
            ctx.sandbox(root / "http-cache"),
            ctx.sandbox(root / "tmp"),
        )

    def _dotnet(
        self,
        ctx: AdapterContext,
        args: list[str],
        log_name: str,
        *,
        timeout_s: float | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[Any, str]:
        tools = self._tools(ctx)
        directory = self._mirror(ctx)
        directory.mkdir(parents=True, exist_ok=True)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            [tools.dotnet, *args],
            cwd=ctx.sandbox(directory),
            env={**self._env(ctx), **(env or {})},
            timeout_s=timeout_s or ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / log_name,
        )
        stdout, stderr = full_output(result)
        return result, stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr

    def _sync(self, ctx: AdapterContext) -> list[str]:
        return mirror.sync(ctx.worktree, self._mirror(ctx))

    def _nuget_config(self, ctx: AdapterContext) -> str:
        """The project's root ``nuget.config`` (in the mirror), else the toolchain's."""
        for candidate in sorted(ctx.worktree.iterdir()) if ctx.worktree.is_dir() else []:
            if candidate.is_file() and candidate.name.lower() == "nuget.config":
                return ctx.sandbox(self._mirror(ctx) / candidate.name)
        return self._tools(ctx).nuget_config

    # ------------------------------------------------------------------ build

    def configure(self, ctx: AdapterContext) -> BuildResult:
        """The restore step (``fetch_only``, D-65): ``dotnet restore`` with the network on;
        skipped while the MSBuild files are as they were at the last restore. Builds then run
        offline."""
        started = time.monotonic()
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        self._sync(ctx)
        lay = self._layout(ctx)
        root = self._root(ctx)
        root.mkdir(parents=True, exist_ok=True)
        fingerprint = self._fingerprint(ctx, lay)
        restored = root / "restored"
        log_path = ctx.log_dir / "dotnet-restore.log"
        if restored.is_file() and restored.read_text(encoding="utf-8").strip() == fingerprint:
            return BuildResult(success=True, duration_s=0.0, log_path=self._note(log_path))
        if not lay.projects:
            return BuildResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        file_path=None,
                        severity=Severity.ERROR,
                        message="No C# project (.csproj) in the worktree, outside the excluded "
                        "paths: CodeCoreMorph builds C# with MSBuild projects.",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                ],
                duration_s=time.monotonic() - started,
                log_path=self._note(log_path),
            )
        refused = self._new_constructs(ctx, lay)
        if refused:
            return BuildResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        file_path=None,
                        severity=Severity.ERROR,
                        message="The MSBuild files have code-running constructs or package "
                        "sources the project didn't have when the run began: "
                        + "; ".join(refused)
                        + ". MSBuild runs them while it reads the projects, with the network "
                        "on in the restore step, so CodeCoreMorph doesn't restore for them. "
                        "Remove them; a migration doesn't need one.",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                ],
                duration_s=time.monotonic() - started,
                log_path=self._note(log_path),
            )
        args = [
            "restore",
            self._entry(ctx, lay),
            "--configfile",
            self._nuget_config(ctx),
            "--disable-build-servers",
            *MSBUILD_SWITCHES,
            *ctx.lang.configure_args,
        ]
        ctx.executor.set_network(True)
        try:
            result, output = self._dotnet(ctx, args, "dotnet-restore.log")
        finally:
            ctx.executor.set_network(False)
        if not result.ok:
            return self._failed(result, output, ctx, "restore")
        restored.write_text(fingerprint, encoding="utf-8")
        return BuildResult(
            success=True, duration_s=time.monotonic() - started, log_path=result.log_path
        )

    def _note(self, path: Path) -> Path:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "nothing to restore: the MSBuild files are as they were\n", encoding="utf-8"
            )
        return path

    def _restore_inputs(self, ctx: AdapterContext, lay: Layout) -> list[str]:
        """The files a restore reads: projects, solutions, ``Directory.*`` files, NuGet's and
        the SDK's settings, and the lock files beside the projects."""
        locks = [
            (PurePosixPath(p).parent / "packages.lock.json").as_posix().removeprefix("./")
            for p in lay.projects
        ]
        return sorted(
            {*lay.projects, *lay.build_files, *(f for f in locks if (ctx.worktree / f).is_file())}
        )

    def _fingerprint(self, ctx: AdapterContext, lay: Layout) -> str:
        digest = hashlib.sha256()
        for rel in [*self._restore_inputs(ctx, lay), "|", *ctx.lang.configure_args]:
            digest.update(rel.encode("utf-8") + b"\0")
            path = ctx.worktree / rel
            if path.is_file():
                digest.update(path.read_bytes() + b"\0")
        return digest.hexdigest()

    def _constructs(self, ctx: AdapterContext, lay: Layout) -> list[str]:
        found: list[str] = []
        for rel in self._restore_inputs(ctx, lay):
            if rel.lower().endswith((".sln", ".slnx", ".json")) and not rel.lower().endswith(
                "global.json"
            ):
                continue
            text = _read_text(ctx.worktree / rel)
            if text is not None:
                found += code_constructs(rel, text)
        return sorted(set(found))

    def _new_constructs(self, ctx: AdapterContext, lay: Layout) -> list[str]:
        """Code-running constructs and package sources the run's first configure didn't see,
        which that configure records."""
        record = self._root(ctx) / "constructs.json"
        current = self._constructs(ctx, lay)
        if not record.is_file():
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return []
        first = set(json.loads(record.read_text(encoding="utf-8")))
        return [c for c in current if c not in first]

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        """``dotnet build --no-restore`` of the entry point, offline; in a port to C++, after
        the bridge library the façades call (D-78)."""
        native = self._native_build(ctx)
        if native is not None and not native.success:
            return native
        self._sync(ctx)
        lay = self._layout(ctx)
        args = [
            "build",
            self._entry(ctx, lay),
            "--no-restore",
            "--disable-build-servers",
            *MSBUILD_SWITCHES,
            *ctx.lang.configure_args,
        ]
        result, output = self._dotnet(ctx, args, "dotnet-build.log")
        diagnostics = parse_dotnet_output(output, self._paths(ctx))
        if not result.ok and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(build_failure(output, "build", str(result.log_path)))
        self._record_warnings(ctx, diagnostics)
        return BuildResult(
            success=result.ok,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def _record_warnings(self, ctx: AdapterContext, diagnostics: list[Diagnostic]) -> None:
        """The last build's nullable warnings, for the ``nullable_disabled`` query."""
        rows = [
            {"file": d.file_path, "line": d.line or 1, "code": d.code, "message": d.message}
            for d in diagnostics
            if d.severity is Severity.WARNING and d.code and _NULLABLE_CODES.match(d.code)
        ]
        path = self._root(ctx) / LAST_BUILD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    def _failed(self, result: Any, output: str, ctx: AdapterContext, step: str) -> BuildResult:
        diagnostics = [
            d for d in parse_dotnet_output(output, self._paths(ctx)) if d.severity is Severity.ERROR
        ]
        if not diagnostics:
            reason = " (timed out)" if result.timed_out else ""
            diagnostics.append(build_failure(output, step + reason, str(result.log_path)))
        return BuildResult(
            success=False,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def builds(self, ctx: AdapterContext) -> bool:
        return bool(self._layout(ctx).projects)

    def clean_build(self, ctx: AdapterContext) -> None:
        """Remove the mirror and the restore's record; NuGet's package folder keeps what the
        restore fetched."""
        shutil.rmtree(self._mirror(ctx), ignore_errors=True)
        (self._root(ctx) / "restored").unlink(missing_ok=True)

    def project_standard(self, ctx: AdapterContext) -> str | None:
        return lowest_standard(ctx.worktree, self._layout(ctx))

    # ------------------------------------------------------------------ tests

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self._sync(ctx)
        lay = self._layout(ctx)
        results = self._root(ctx) / "test-results"
        shutil.rmtree(results, ignore_errors=True)
        results.mkdir(parents=True, exist_ok=True)
        args = [
            "test",
            self._entry(ctx, lay),
            "--no-build",
            "--logger",
            "trx",
            "--results-directory",
            ctx.sandbox(results),
            *ctx.lang.configure_args,
        ]
        if selection is not None and selection.test_ids is not None:
            chosen = selection_filter(selection.test_ids)
            if chosen is None:
                return TestReport(
                    cases=[], duration_s=0.0, log_path=self._note(ctx.log_dir / "dotnet-test.log")
                )
            args += ["--filter", chosen]
        timeout = ctx.lang.test_timeout_s
        # A test project runs on its framework's runtime, or the next one up (D-65).
        result, output = self._dotnet(
            ctx,
            args,
            "dotnet-test.log",
            timeout_s=timeout + ctx.lang.build_timeout_s,
            env={"DOTNET_ROLL_FORWARD": "Major", **self._native_test_env(ctx)},
        )
        cases, problems = read_reports(results.rglob("*.trx"))
        extra: list[TestCaseResult] = [_error_case("dotnet:(report)", p) for p in problems[:1]]
        if result.timed_out:
            extra.append(
                TestCaseResult(
                    test_id="dotnet:(timed out)",
                    status=TestStatus.TIMEOUT,
                    duration_s=result.duration_s,
                    message=f"the test run exceeded {int(timeout)} s",
                )
            )
        elif not result.ok and not any(c.status is not TestStatus.PASSED for c in cases):
            # It failed, yet no test did: a test host that didn't start, a build output missing.
            failure = build_failure(output, "test run", str(result.log_path))
            errors = [
                d
                for d in parse_dotnet_output(output, self._paths(ctx))
                if d.severity is Severity.ERROR
            ][:5]
            message = failure.message
            if errors:
                message += "\n" + "\n".join(f"{d.file_path}:{d.line}: {d.message}" for d in errors)
            # A test host that crashed (a sanitizer's report from native code, D-78) says why
            # in the TRX, ahead of everything else.
            crashed = run_errors(results.rglob("*.trx"))
            if crashed:
                message = crashed[0]
            extra.append(_error_case("dotnet:(run)", message))
        return TestReport(
            cases=[*cases, *extra], duration_s=result.duration_s, log_path=result.log_path
        )

    # ------------------------------------------------------------------ a port to C++ or Rust

    def _native_port(self, ctx: AdapterContext) -> bool:
        return ctx.lang.port_target == "cpp" and csharpports.is_port(ctx.worktree)

    def _rust_port(self, ctx: AdapterContext) -> bool:
        return ctx.lang.port_target == "rust" and rustports.is_port(ctx.worktree)

    @staticmethod
    def _rust_ctx(ctx: AdapterContext) -> AdapterContext:
        """The Rust adapter's view of the port (D-84): its cache, so Cargo's target directory is
        the one its own builds use."""
        return replace(ctx, cache_dir=ctx.cache_dir / "rust", log_dir=ctx.log_dir / "rust")

    def _native_build(self, ctx: AdapterContext) -> BuildResult | None:
        """In a port to C++ (D-78): CMake builds the bridge library the façades call, in the
        build directory the C++ adapter configured (it configures first), so the C# tests run
        the C++ just built. In a port to Rust (D-84): Cargo builds the crates' libraries, the
        ``cdylib`` the façades load among them. None when there is no C++ or Rust yet."""
        if self._rust_port(ctx):
            return RustAdapter().build_libraries(self._rust_ctx(ctx))
        if not self._native_port(ctx):
            return None
        library = csharpports.native_name(_read_text(ctx.worktree / csharpports.CPP_CMAKE) or "")
        if library is None:
            return None
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = ctx.executor.run(
            ["cmake", "--build", ctx.sandbox(ctx.build_dir), "--target", library],
            cwd=ctx.sandbox(ctx.worktree),
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "native-build.log",
        )
        stdout, stderr = full_output(result)
        folds = ctx.executor.kind == "native" and sys.platform in ("win32", "darwin")
        mapper = PathMapper(
            ctx.sandbox(ctx.worktree), ctx.sandbox(ctx.build_dir), case_insensitive=folds
        )
        diagnostics = parse_compiler_output(stdout + "\n" + stderr, mapper)
        if not result.ok and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(
                build_failure(stdout + "\n" + stderr, f"build of {library}", str(result.log_path))
            )
        return BuildResult(
            success=result.ok,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def _native_test_env(self, ctx: AdapterContext) -> dict[str, str]:
        """What ``dotnet test`` needs in a port to C++: the bridge library's directory on the
        loader's path, and the shared ASan runtime preloaded, the C++ being built with the
        sanitizers (D-78)."""
        if self._rust_port(ctx):
            target = rustports.cargo_target_dir(ctx.cache_dir)
            return rustports.test_environment(f"{ctx.sandbox(target)}/debug")
        if not self._native_port(ctx):
            return {}
        return csharpports.test_environment(
            f"{ctx.sandbox(ctx.build_dir)}/lib", self._asan_runtime(ctx)
        )

    def _asan_runtime(self, ctx: AdapterContext) -> str | None:
        """The shared AddressSanitizer runtime of the toolchain's clang, asked once per run."""
        record = self._root(ctx) / "asan-runtime"
        if record.is_file():
            return record.read_text(encoding="utf-8").strip() or None
        found: str | None = None
        for name in ("libclang_rt.asan.so", "libclang_rt.asan-x86_64.so"):
            result = ctx.executor.run(
                ["clang", f"-print-file-name={name}"],
                cwd=ctx.sandbox(ctx.worktree),
                timeout_s=60,
                log_path=ctx.log_dir / "asan-runtime.log",
            )
            path = full_output(result)[0].strip()
            if result.ok and path != name and PurePosixPath(path).is_absolute():
                found = path
                break
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(found or "", encoding="utf-8")
        return found

    # ------------------------------------------------------------------ index

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        """MSBuild projects, the C# standard they set and the layout (FR-1.4)."""
        return detect_project(root, files)

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        """The Roslyn index of the projects' compiled files. A port's source
        (``ctx.lang.port_target``) also records its types, generics in use, refusals, thrown
        exceptions and test methods (D-78)."""
        return run_index(
            ctx,
            self._tools(ctx),
            self._layout(ctx),
            mirror=self._mirror(ctx),
            adapter_version=self.version,
            port=ctx.lang.port_target is not None,
        )

    # ------------------------------------------------------------------ codemods

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        started = time.monotonic()
        totals: dict[str, int] = {}
        skipped: list[SkippedReplacement] = []
        diagnostics: list[Diagnostic] = []
        logs: list[Path] = []
        for number, spec in enumerate(codemods, start=1):
            if spec.tool == "msbuild-lang-version":
                changes = self._bump(ctx, files)
            elif spec.tool == "dotnet-format":
                changes, extra = self._dotnet_format(ctx, spec, files, number)
                skipped += extra.skipped
                diagnostics += extra.diagnostics
                logs += extra.log_paths
            else:
                raise ConfigError(
                    f"the C# adapter has no codemod tool {spec.tool!r}",
                    hint="Available: dotnet-format, msbuild-lang-version.",
                )
            for path, count in changes.items():
                totals[path] = totals.get(path, 0) + count
        return CodemodResult(
            changes=[CodemodChange(file=f, replacements=n) for f, n in sorted(totals.items())],
            skipped=skipped,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
            log_paths=logs,
        )

    def _bump(self, ctx: AdapterContext, files: list[str]) -> dict[str, int]:
        target = ctx.lang.target_standard or ctx.lang.standard
        changes: dict[str, int] = {}
        # Directory.Build.props first: a project then sees the language version it passes down.
        props = [f for f in files if PurePosixPath(f).name == PROPS_FILES[0]]
        projects = [f for f in files if is_project(PurePosixPath(f).name)]
        for rel in [*props, *projects]:
            path = ctx.worktree / rel
            if not path.is_file():
                continue
            data = path.read_bytes()
            decoded = TextFile.decode(data)
            inherited = (
                inherited_properties(ctx.worktree, rel).get("LangVersion")
                if is_project(PurePosixPath(rel).name)
                else None
            )
            edit = bump_project(decoded.text, target, inherited)
            if edit.replacements:
                _write_atomic(path, decoded.encode(edit.text))
                changes[rel] = edit.replacements
        return changes

    def _dotnet_format(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str], number: int
    ) -> tuple[dict[str, int], CodemodResult]:
        try:
            checks = formatting.check_ids(spec.checks)
        except ValueError as exc:
            raise ConfigError(
                f"the dotnet-format codemod takes diagnostic IDs as its checks: {exc}",
                hint="List IDs such as IDE0161 under the codemod's checks.",
            ) from exc
        wanted = [f for f in files if f.lower().endswith(".cs") and (ctx.worktree / f).is_file()]
        if not checks or not wanted:
            return {}, CodemodResult(duration_s=0)
        self._sync(ctx)
        lay = self._layout(ctx)
        mirror_dir = self._mirror(ctx)
        formatting.write_editorconfig(mirror_dir, dict(spec.options), checks)
        args = formatting.format_arguments(self._entry(ctx, lay), wanted, checks)
        # One fix can expose the next ([.. ids] for ids.ToArray() lets new(keys) become
        # [.. keys]), so dotnet format runs again while a pass changes the unit's files.
        duration = 0.0
        logs: list[Path] = []
        for attempt in range(1, MAX_FORMAT_PASSES + 1):
            before = {
                f: (mirror_dir / f).read_bytes() for f in wanted if (mirror_dir / f).is_file()
            }
            suffix = "" if attempt == 1 else f"-{attempt}"
            result, output = self._dotnet(ctx, args, f"codemod-{number}-dotnet-format{suffix}.log")
            duration += result.duration_s
            logs.append(result.log_path)
            if not result.ok:
                self._sync(ctx)  # undo whatever it wrote
                failure = build_failure(
                    output, f"dotnet format ({', '.join(checks)})", str(result.log_path)
                )
                failure = failure.model_copy(update={"origin": DiagnosticOrigin.ANALYZER})
                return {}, CodemodResult(diagnostics=[failure], duration_s=duration, log_paths=logs)
            after = {f: (mirror_dir / f).read_bytes() for f in wanted if (mirror_dir / f).is_file()}
            if after == before:
                break
        unit = set(wanted)
        changes: dict[str, int] = {}
        skipped: list[SkippedReplacement] = []
        for rel, copy in mirror.project_files(mirror_dir).items():
            if rel == formatting.EDITORCONFIG:
                continue
            source = ctx.worktree / rel
            new = copy.read_bytes()
            old = source.read_bytes() if source.is_file() else None
            if old is None or mirror.mirrored(rel, old) == new:
                continue
            if rel not in unit:
                skipped.append(
                    SkippedReplacement(
                        file=rel, check="dotnet-format", reason="outside the unit's files"
                    )
                )
                continue
            data = formatting.keep_style(old, new)
            if data == old:
                continue
            _write_atomic(source, data)
            changes[rel] = max(1, len(formatting.changed_ranges(old, data)))
        self._sync(ctx)
        return changes, CodemodResult(skipped=skipped, duration_s=duration, log_paths=logs)

    # ------------------------------------------------------------------ tests' sources

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        found: list[str] = []
        for path in files:
            if (
                path.lower().endswith(".cs")
                and (text := _read_text(ctx.worktree / path)) is not None
            ):
                found += disabled_markers(path, text)
        return found

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        """Each test method's assertions, by its test ID; a parameterized method's for each of
        its cases, by the case's ID, where a port's index names them exactly (D-78): what the
        test map pairs, and the test runs report."""
        found: list[TestAssertions] = []
        cases: dict[str, list[str]] | None = None
        for path in files:
            if not path.lower().endswith(".cs"):
                continue
            text = _read_text(ctx.worktree / path)
            if text is None:
                continue
            for block in find_cases(text):
                if cases is None:
                    cases = _parameterized_cases(ctx.cache_dir / INDEX_FILE_NAME)
                checks = assertions(text, block)
                for test_id in cases.get(block.test_id) or [block.test_id]:
                    found.append(
                        TestAssertions(
                            file=path,
                            test=f"{block.class_name}#{block.method}",
                            test_id=test_id,
                            line=block.line,
                            assertions=checks,
                        )
                    )
        return found

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        if name == "test_cases":
            return [
                RuleSite(file=case.file, line=case.line, text=case.test_id)
                for case in self.extract_assertions(ctx, files)
            ]
        queries = {
            "record_candidates": record_candidates,
            "csharp_types": port_type_sites,
            "csharp_programs": program_sites,
        }
        if name in queries:
            index_path = ctx.cache_dir / INDEX_FILE_NAME
            if not index_path.is_file():
                raise ConfigError(f"the {name} query needs an index", hint="Index first.")
            return queries[name](index_path, files)
        if name == "nullable_disabled":
            return self._nullable_sites(ctx, files)
        return super().query(ctx, name, files)

    def _nullable_sites(self, ctx: AdapterContext, files: list[str]) -> list[RuleSite]:
        """The unit's C# files without a nullable context (no ``#nullable enable``, in a
        project that doesn't enable it), and the nullable warnings of the last build in them.
        Test files have none: their nulls are often what a test checks."""
        lay = self._layout(ctx)
        tests = PathMatcher(ctx.lang.test_dirs)
        files = [f for f in files if not tests.matches(f)]
        enabled_projects = {
            p
            for p in lay.projects
            if _PROJECT_NULLABLE.search(uncommented(_read_text(ctx.worktree / p) or ""))
        }
        enabled_files = {f for p in enabled_projects for f in lay.sources.get(p, [])}
        wanted = [f for f in files if f.lower().endswith(".cs")]
        sites: list[RuleSite] = []
        for rel in wanted:
            text = _read_text(ctx.worktree / rel)
            if text is None:
                continue
            if rel not in enabled_files and not _NULLABLE_ENABLED.search(text):
                sites.append(
                    RuleSite(file=rel, line=1, text="no nullable context (#nullable enable)")
                )
        record = self._root(ctx) / LAST_BUILD
        if record.is_file():
            unit = set(wanted)
            for row in json.loads(record.read_text(encoding="utf-8")):
                if row.get("file") in unit:
                    sites.append(
                        RuleSite(
                            file=row["file"],
                            line=max(int(row.get("line") or 1), 1),
                            text=f"{row.get('code')}: {row.get('message')}",
                        )
                    )
        return sites


def _parameterized_cases(index_path: Path) -> dict[str, list[str]]:
    """``dotnet:Ns.Class#Method`` → its cases' IDs, for each parameterized method whose cases a
    port's index names exactly (D-78); empty without such an index (an upgrade's has no test
    methods), where a method's assertions stay the method's."""
    if not index_path.is_file():
        return {}
    with IndexStore.open(index_path) as store:
        methods = store.test_methods()
    found: dict[str, list[str]] = {}
    for method in methods:
        stem = f"dotnet:{method.class_name}#{method.method}"
        if method.exact and method.cases and method.cases != [stem]:
            found[stem] = sorted(method.cases)
    return found


def record_candidates(index_path: Path, files: list[str]) -> list[RuleSite]:
    """Classes in ``files`` that look like values (``use_records``): no base class and no
    subclass in the project, not abstract or static, every instance field read-only and no
    property with a setter, and ``Equals(object)`` and ``GetHashCode`` of their own."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        classes = [c for c in store.symbols(kind=SymbolKind.CLASS) if c.file in wanted]
        kinds = {s.id: s.kind for s in store.symbols(kind=SymbolKind.CLASS)}
        sites: list[RuleSite] = []
        for cls in classes:
            signature = cls.signature or ""
            if any(word in signature.split() for word in ("abstract", "static")):
                continue
            members = [
                s
                for s in store.symbols(file=cls.file)
                if s.id.startswith(cls.id + ".")
                and "." not in s.id[len(cls.id) + 1 :].split("(")[0]
            ]
            fields = [
                s
                for s in members
                if s.kind is SymbolKind.FIELD
                and "static" not in (s.signature or "").split()
                and "const" not in (s.signature or "").split()
            ]
            properties = [s for s in members if s.kind is SymbolKind.PROPERTY]
            if not fields and not properties:
                continue
            if any("readonly" not in (f.signature or "").split() for f in fields):
                continue
            if any(re.search(r"\bset;", p.signature or "") for p in properties):
                continue
            names = {s.id.removeprefix(cls.id + ".") for s in members}
            if "Equals(System.Object)" not in names or "GetHashCode" not in names:
                continue
            outgoing = [
                r
                for r in store.references_from(cls.file)
                if r.from_symbol == cls.id
                and r.kind is ReferenceKind.INHERIT
                and kinds.get(r.to_symbol) is SymbolKind.CLASS
            ]
            if outgoing or any(
                r.kind is ReferenceKind.INHERIT for r in store.references_to(cls.id)
            ):
                continue
            sites.append(
                RuleSite(
                    file=cls.file,
                    line=cls.range.start_line,
                    column=cls.range.start_column,
                    text=f"{cls.qualified_name}: {len(fields)} read-only field(s), "
                    f"{len(properties)} property(ies), Equals and GetHashCode",
                )
            )
    return sites


def port_type_sites(index_path: Path, files: list[str]) -> list[RuleSite]:
    """A port's types (``csharp_types``, D-79): each type a library project declares in
    ``files`` and isn't nested in another, at its declaration in each of its files (a partial
    type has several). The planner cuts the units."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        libraries = {t.name for t in store.build_targets() if t.kind is TargetKind.LIBRARY}
        sources = {f.path for f in store.files(FileRole.SOURCE)}
        sites: list[RuleSite] = []
        for entry in store.port_types():
            if entry.outer is not None or entry.project not in libraries:
                continue
            if any(f not in sources for f in entry.files):
                continue
            lines = {s.file: s.range.start_line for s in store.symbols(symbol_id=entry.id)}
            text = f"{entry.name}: port it, keeping its C# API as a façade"
            sites += [
                RuleSite(file=f, line=lines.get(f, 1), text=text)
                for f in entry.files
                if f in wanted
            ]
    return sorted(sites, key=lambda s: (s.file, s.line, s.text))


def program_sites(index_path: Path, files: list[str]) -> list[RuleSite]:
    """A port's programs (``csharp_programs``, D-79): each program project with a file in
    ``files``, at its ``Main`` (or its first file, for top-level statements)."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        sites: list[RuleSite] = []
        for target in store.build_targets():
            sources = [f for f in target.files if f in wanted]
            if target.kind is not TargetKind.EXECUTABLE or not sources:
                continue
            mains = [
                s
                for f in sources
                for s in store.symbols(file=f, name="Main")
                if s.kind is SymbolKind.METHOD
            ]
            file, line = (mains[0].file, mains[0].range.start_line) if mains else (sources[0], 1)
            sites.append(RuleSite(file=file, line=line, text=f"{target.name}: port the program"))
    return sites


def _error_case(test_id: str, message: str) -> TestCaseResult:
    return TestCaseResult(test_id=test_id, status=TestStatus.ERROR, duration_s=0, message=message)
