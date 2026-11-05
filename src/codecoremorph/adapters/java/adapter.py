"""The Java adapter: Maven builds and Surefire tests on the JDK of the build's standard, Spoon
indexing, and OpenRewrite codemods (SPEC §7.1, §11.2, D-63).

Maven runs in a mirror of the worktree in the adapter's cache (it writes ``target/`` beside
every POM, which the worktree must never hold), always offline but in the dependency step,
which is the adapter's configure. Every command goes through ``ctx.executor``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import shutil
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.java import mirror, openrewrite
from codecoremorph.adapters.java.detect import detect as detect_project
from codecoremorph.adapters.java.diagnostics import PathMap, build_failure, parse_maven_output
from codecoremorph.adapters.java.indexing import project_layout, run_index
from codecoremorph.adapters.java.junit import (
    assertions,
    disabled_markers,
    find_cases,
)
from codecoremorph.adapters.java.maven import (
    INSTALL_HINT,
    JavaToolchain,
    maven_command,
    surefire_providers,
)
from codecoremorph.adapters.java.pom import (
    POM,
    STANDARDS,
    bump_java_level,
    extensions,
    lowest_standard,
)
from codecoremorph.adapters.java.surefire import read_reports, selection_argument
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
    LanguageConfig,
    ProjectDetection,
    ReferenceKind,
    RuleSite,
    Severity,
    SkippedReplacement,
    SymbolIndex,
    SymbolKind,
    TestAssertions,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    ToolchainSpec,
    ToolRequirement,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

log = logging.getLogger(__name__)

MAVEN_DIR = "maven"  # in ctx.cache_dir: the mirror, the run's repository, the step's records
MVN_SETTINGS_FILES = (".mvn/extensions.xml", ".mvn/jvm.config", ".mvn/maven.config")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.codecoremorph-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class JavaAdapter(LanguageAdapter):
    """Java projects built with Maven."""

    api_version = "1"
    language = "java"
    version = "1"

    # ------------------------------------------------------------------ description

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            extract_assertions=True,
            detect_disabled_tests=True,
            codemod_tools=["openrewrite", "maven-java-version"],
            queries=["test_cases", "record_candidates"],
            standards=list(STANDARDS),
        )

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(
            language="java",
            image="codecoremorph-java",
            tools=[
                ToolRequirement(
                    name="java", min_version="17", version_args=["-version"], purpose="Java"
                ),
                ToolRequirement(name="mvn", min_version="3.9", purpose="build system"),
            ],
            install_hint=INSTALL_HINT,
        )

    # ------------------------------------------------------------------ helpers

    def _tools(self, ctx: AdapterContext) -> JavaToolchain:
        windows = ctx.executor.kind == "native" and sys.platform == "win32"
        return JavaToolchain.from_env(ctx.executor.toolchain_env, windows=windows)

    @staticmethod
    def _root(ctx: AdapterContext) -> Path:
        return ctx.cache_dir / MAVEN_DIR

    def _mirror(self, ctx: AdapterContext) -> Path:
        return self._root(ctx) / "project"

    def _repository(self, ctx: AdapterContext) -> Path:
        return self._root(ctx) / "repository"

    def _paths(self, ctx: AdapterContext) -> PathMap:
        folds = ctx.executor.kind == "native" and sys.platform in ("win32", "darwin")
        mirror_dir = self._mirror(ctx)
        return PathMap([ctx.sandbox(mirror_dir), str(mirror_dir)], case_insensitive=folds)

    def _maven(
        self,
        ctx: AdapterContext,
        args: list[str],
        log_name: str,
        *,
        standard: str | None = None,
        timeout_s: float | None = None,
        project: Path | None = None,
        pom: bool = True,
    ) -> tuple[Any, str]:
        tools = self._tools(ctx)
        directory = project or self._mirror(ctx)
        directory.mkdir(parents=True, exist_ok=True)
        self._repository(ctx).mkdir(parents=True, exist_ok=True)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        argv, env = maven_command(
            tools,
            standard or ctx.lang.standard,
            project=ctx.sandbox(directory),
            repository=ctx.sandbox(self._repository(ctx)),
            args=[*args, *(ctx.lang.configure_args if pom else [])],
            pom=pom,
        )
        result = ctx.executor.run(
            argv,
            cwd=ctx.sandbox(directory),
            env=env,
            timeout_s=timeout_s or ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / log_name,
        )
        stdout, stderr = full_output(result)
        return result, stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr

    def _sync(self, ctx: AdapterContext) -> list[str]:
        return mirror.sync(ctx.worktree, self._mirror(ctx))

    def _poms(self, ctx: AdapterContext) -> list[str]:
        poms, _, _ = project_layout(ctx.worktree, PathMatcher(ctx.lang.exclude))
        return poms

    # ------------------------------------------------------------------ build

    def configure(self, ctx: AdapterContext) -> BuildResult:
        """The dependency step (``fetch_only``, D-63): ``dependency:go-offline`` with the
        network on, then the Surefire providers it leaves out; skipped while the POMs and
        ``.mvn/`` are as they were at the last fetch. Builds then run offline."""
        started = time.monotonic()
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        self._sync(ctx)
        root = self._root(ctx)
        fingerprint = self._fingerprint(ctx)
        fetched = root / "fetched"
        log_path = ctx.log_dir / "maven-fetch.log"
        if fetched.is_file() and fetched.read_text(encoding="utf-8").strip() == fingerprint:
            return BuildResult(success=True, duration_s=0.0, log_path=self._note(log_path))
        refused = self._new_extensions(ctx)
        if refused:
            return BuildResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        file_path=POM,
                        severity=Severity.ERROR,
                        message="The POMs load build extensions or Maven settings the project "
                        "didn't have when the run began: " + "; ".join(refused) + ". Maven runs "
                        "their code while it reads the POMs, with the network on in the "
                        "dependency step, so CodeCoreMorph doesn't fetch for them. Remove them; "
                        "a migration doesn't need one.",
                        origin=DiagnosticOrigin.BUILD_SYSTEM,
                    )
                ],
                duration_s=time.monotonic() - started,
                log_path=self._note(log_path),
            )
        tools = self._tools(ctx)
        ctx.executor.set_network(True)
        try:
            result, output = self._maven(
                ctx, [f"{tools.dependency_plugin}:go-offline"], "maven-fetch.log"
            )
        finally:
            ctx.executor.set_network(False)
        if not result.ok:
            return self._failed(result, output, ctx, "dependency step")
        work = root / "fetch"
        for artifact in surefire_providers([self._repository(ctx)]):
            ctx.executor.set_network(True)
            try:
                result, output = self._maven(
                    ctx,
                    [f"{tools.dependency_plugin}:get", f"-Dartifact={artifact}"],
                    f"maven-fetch-{artifact.split(':')[1]}.log",
                    project=work,
                    pom=False,
                )
            finally:
                ctx.executor.set_network(False)
            if not result.ok:
                return self._failed(result, output, ctx, f"dependency step ({artifact})")
        fetched.write_text(fingerprint, encoding="utf-8")
        return BuildResult(
            success=True, duration_s=time.monotonic() - started, log_path=result.log_path
        )

    def _note(self, path: Path) -> Path:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("nothing to fetch: the POMs are as they were\n", encoding="utf-8")
        return path

    def _fingerprint(self, ctx: AdapterContext) -> str:
        digest = hashlib.sha256()
        for rel in [*self._poms(ctx), *MVN_SETTINGS_FILES, "|", *ctx.lang.configure_args]:
            digest.update(rel.encode("utf-8") + b"\0")
            path = ctx.worktree / rel
            if path.is_file():
                digest.update(path.read_bytes() + b"\0")
        return digest.hexdigest()

    def _extensions(self, ctx: AdapterContext) -> list[str]:
        found: list[str] = []
        for rel in self._poms(ctx):
            text = _read_text(ctx.worktree / rel) or ""
            found += [f"{e} in {rel}" for e in extensions(text)]
        for rel in MVN_SETTINGS_FILES:
            text = _read_text(ctx.worktree / rel)
            if text is not None and text.strip():
                found.append(f"{rel}: {' '.join(text.split())[:200]}")
        return sorted(set(found))

    def _new_extensions(self, ctx: AdapterContext) -> list[str]:
        """Extensions and ``.mvn/`` settings the run's first configure didn't see, which that
        configure records."""
        record = self._root(ctx) / "extensions.json"
        current = self._extensions(ctx)
        if not record.is_file():
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return []
        first = set(json.loads(record.read_text(encoding="utf-8")))
        return [e for e in current if e not in first]

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        """``mvn -o test-compile`` from nothing (the outputs are removed first, so a class
        whose source went can't linger)."""
        self._sync(ctx)
        mirror.clean(self._mirror(ctx))
        result, output = self._maven(ctx, ["--offline", "test-compile"], "maven-build.log")
        diagnostics = parse_maven_output(output, self._paths(ctx))
        if not result.ok and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(build_failure(output, "build", str(result.log_path)))
        return BuildResult(
            success=result.ok,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def _failed(self, result: Any, output: str, ctx: AdapterContext, step: str) -> BuildResult:
        diagnostics = [
            d for d in parse_maven_output(output, self._paths(ctx)) if d.severity is Severity.ERROR
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
        return (ctx.worktree / POM).is_file()

    def clean_build(self, ctx: AdapterContext) -> None:
        """Remove the mirror; the run's repository keeps what the dependency step fetched."""
        shutil.rmtree(self._mirror(ctx), ignore_errors=True)

    def project_standard(self, ctx: AdapterContext) -> str | None:
        texts = [t for rel in self._poms(ctx) if (t := _read_text(ctx.worktree / rel))]
        return lowest_standard(texts)

    # ------------------------------------------------------------------ tests

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self._sync(ctx)
        project = self._mirror(ctx)
        for directory in mirror.output_dirs(project):
            shutil.rmtree(directory / "surefire-reports", ignore_errors=True)
        args = ["--offline", "test", "-Dmaven.test.failure.ignore=true", "-DtrimStackTrace=true"]
        if selection is not None and selection.test_ids is not None:
            chosen = selection_argument(selection.test_ids)
            if chosen is None:
                return TestReport(
                    cases=[], duration_s=0.0, log_path=self._note(ctx.log_dir / "maven-test.log")
                )
            args += [f"-Dtest={chosen}", "-Dsurefire.failIfNoSpecifiedTests=false"]
            args.append("-DfailIfNoTests=false")
        timeout = ctx.lang.test_timeout_s
        result, output = self._maven(
            ctx, args, "maven-test.log", timeout_s=timeout + ctx.lang.build_timeout_s
        )
        reports = [d / "surefire-reports" for d in mirror.output_dirs(project)]
        cases, problems = read_reports(reports)
        extra: list[TestCaseResult] = [_error_case("junit:(report)", p) for p in problems[:1]]
        if result.timed_out:
            extra.append(
                TestCaseResult(
                    test_id="junit:(timed out)",
                    status=TestStatus.TIMEOUT,
                    duration_s=result.duration_s,
                    message=f"the test run exceeded {int(timeout)} s",
                )
            )
        elif not result.ok:
            failure = build_failure(output, "test run", str(result.log_path))
            compiled = parse_maven_output(output, self._paths(ctx))
            errors = [d for d in compiled if d.severity is Severity.ERROR][:5]
            message = failure.message
            if errors:
                message += "\n" + "\n".join(f"{d.file_path}:{d.line}: {d.message}" for d in errors)
            extra.append(_error_case("junit:(run)", message))
        return TestReport(
            cases=[*cases, *extra], duration_s=result.duration_s, log_path=result.log_path
        )

    # ------------------------------------------------------------------ index

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        """Maven, the Java level the POMs set and the layout (FR-1.4)."""
        return detect_project(root, files)

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        return run_index(ctx, self._tools(ctx), adapter_version=self.version)

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
            if spec.tool == "maven-java-version":
                changes = self._bump_java(ctx, files)
            elif spec.tool == "openrewrite":
                changes, extra = self._openrewrite(ctx, spec, files, number)
                skipped += extra.skipped
                diagnostics += extra.diagnostics
                logs += extra.log_paths
            else:
                raise ConfigError(
                    f"the Java adapter has no codemod tool {spec.tool!r}",
                    hint="Available: openrewrite, maven-java-version.",
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

    def _bump_java(self, ctx: AdapterContext, files: list[str]) -> dict[str, int]:
        target = ctx.lang.target_standard or ctx.lang.standard
        changes: dict[str, int] = {}
        for rel in files:
            if PurePosixPath(rel).name != POM:
                continue
            path = ctx.worktree / rel
            data = path.read_bytes()
            decoded = TextFile.decode(data)
            edit = bump_java_level(decoded.text, target, top_level=rel == POM)
            if edit.replacements:
                _write_atomic(path, decoded.encode(edit.text))
                changes[rel] = edit.replacements
        return changes

    def _openrewrite(
        self, ctx: AdapterContext, spec: CodemodSpec, files: list[str], number: int
    ) -> tuple[dict[str, int], CodemodResult]:
        target = ctx.lang.target_standard or ctx.lang.standard
        names = openrewrite.recipes(spec, target)
        if not names:
            return {}, CodemodResult(duration_s=0)
        tools = self._tools(ctx)
        self._sync(ctx)
        root = self._root(ctx)
        config = root / f"rewrite-{number}.yml"
        config.write_text(openrewrite.config(names), encoding="utf-8")
        cache = root / "rewrite-cache"
        cache.mkdir(parents=True, exist_ok=True)
        # Maven runs offline (the plugin and recipes come from the tools repository), but the
        # recipes resolve the dependencies they add over the network themselves (offline,
        # UpgradeToJava21 added no JAXB dependency), so the run has the network, like the
        # dependency step and behind the same guard (D-64).
        refused = self._new_extensions(ctx)
        if refused:
            return {}, CodemodResult(
                diagnostics=[
                    Diagnostic(
                        file_path=POM,
                        severity=Severity.ERROR,
                        message="OpenRewrite didn't run: the POMs load build extensions the "
                        "project didn't have when the run began: " + "; ".join(refused),
                        origin=DiagnosticOrigin.ANALYZER,
                    )
                ],
                duration_s=0,
            )
        # compile first, in the same invocation: the recipes resolve each module's
        # dependencies, and Maven finds a sibling module's classes only once it has compiled
        # them (task-planner's app couldn't resolve core). The tree a unit starts from builds.
        args = [
            "--offline",
            "compile",
            *openrewrite.run_arguments(
                tools.rewrite_plugin, tools.rewrite_recipes, ctx.sandbox(config), ctx.sandbox(cache)
            ),
        ]
        ctx.executor.set_network(True)
        try:
            result, output = self._maven(ctx, args, f"codemod-{number}-openrewrite.log")
        finally:
            ctx.executor.set_network(False)
        if not result.ok:
            self._sync(ctx)  # undo whatever it wrote
            failure = build_failure(
                output, f"OpenRewrite run ({', '.join(names)})", str(result.log_path)
            )
            failure = failure.model_copy(update={"origin": DiagnosticOrigin.ANALYZER})
            return {}, CodemodResult(
                diagnostics=[failure], duration_s=result.duration_s, log_paths=[result.log_path]
            )
        wanted = set(files)
        changes: dict[str, int] = {}
        skipped: list[SkippedReplacement] = []
        rewritten = self._mirror(ctx)
        for rel, copy in mirror.project_files(rewritten).items():
            source = ctx.worktree / rel
            new = copy.read_bytes()
            old = source.read_bytes() if source.is_file() else None
            if old == new:
                continue
            if rel not in wanted or old is None:
                skipped.append(
                    SkippedReplacement(
                        file=rel, check="openrewrite", reason="outside the unit's files"
                    )
                )
                continue
            data = _keep_style(old, new)
            _write_atomic(source, data)
            changes[rel] = max(1, len(_changed_ranges(old, data)))
        self._sync(ctx)
        return changes, CodemodResult(
            skipped=skipped, duration_s=result.duration_s, log_paths=[result.log_path]
        )

    # ------------------------------------------------------------------ tests' sources

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        found: list[str] = []
        for path in files:
            if path.endswith(".java") and (text := _read_text(ctx.worktree / path)) is not None:
                found += disabled_markers(path, text)
        return found

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        found: list[TestAssertions] = []
        for path in files:
            if not path.endswith(".java"):
                continue
            text = _read_text(ctx.worktree / path)
            if text is None:
                continue
            for block in find_cases(text):
                found.append(
                    TestAssertions(
                        file=path,
                        test=f"{block.class_name}#{block.method}",
                        test_id=block.test_id,
                        line=block.line,
                        assertions=assertions(text, block),
                    )
                )
        return found

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        if name == "test_cases":
            return [
                RuleSite(file=case.file, line=case.line, text=case.test_id)
                for case in self.extract_assertions(ctx, files)
            ]
        if name == "record_candidates":
            index_path = ctx.cache_dir / INDEX_FILE_NAME
            if not index_path.is_file():
                raise ConfigError("the record_candidates query needs an index", hint="Index first.")
            return record_candidates(index_path, files)
        return super().query(ctx, name, files)


def record_candidates(index_path: Path, files: list[str]) -> list[RuleSite]:
    """Classes in ``files`` that look like values (``use_records``): no superclass and no
    subclass in the project, every instance field private and final, and ``equals`` and
    ``hashCode`` of their own."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        classes = [c for c in store.symbols(kind=SymbolKind.CLASS) if c.file in wanted]
        sites: list[RuleSite] = []
        for cls in classes:
            signature = cls.signature or ""
            if " extends " in signature or "abstract " in signature:
                continue
            members = [
                s
                for s in store.symbols(file=cls.file)
                if s.id.startswith(cls.id + "#") and "#" not in s.id[len(cls.id) + 1 :]
            ]
            fields = [
                s
                for s in members
                if s.kind is SymbolKind.FIELD and "static " not in (s.signature or "")
            ]
            if not fields or not all(
                "private " in (f.signature or "") and "final " in (f.signature or "")
                for f in fields
            ):
                continue
            methods = {s.id.removeprefix(cls.id + "#") for s in members}
            if "equals(java.lang.Object)" not in methods or "hashCode()" not in methods:
                continue
            if any(r.kind is ReferenceKind.INHERIT for r in store.references_to(cls.id)):
                continue
            sites.append(
                RuleSite(
                    file=cls.file,
                    line=cls.range.start_line,
                    column=cls.range.start_column,
                    text=f"{cls.qualified_name}: {len(fields)} final field(s), equals and hashCode",
                )
            )
    return sites


def _keep_style(old: bytes, new: bytes) -> bytes:
    """``new`` in ``old``'s encoding and line endings (OpenRewrite may write LF into a CRLF
    file)."""
    try:
        before = TextFile.decode(old)
        after = TextFile.decode(new)
    except BinaryFileError:
        return new
    text = after.text
    if "\r\n" in before.text and "\r\n" not in text:
        text = text.replace("\n", "\r\n")
    return before.encode(text) if before.can_encode(text) else new


def _changed_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    try:
        old = TextFile.decode(before).text.splitlines()
        new = TextFile.decode(after).text.splitlines()
    except BinaryFileError:
        return []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(j1 + 1, j2) for tag, _, _, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _error_case(test_id: str, message: str) -> TestCaseResult:
    return TestCaseResult(test_id=test_id, status=TestStatus.ERROR, duration_s=0, message=message)


def _read_text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, ValueError):
        return None
