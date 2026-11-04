"""A run's environment: the manifest and its plugins, the run workspace, the executor and the
adapter context (SPEC §4.4: validate config, prepare workspace, provision toolchain).

``codecoremorph debug``, ``plan``, ``unit`` and (from Phase 5) ``run`` open one of these.
Closing it stops the executor and removes the worktree; the run branch stays unless
``delete_branch`` is set. A run that continues in a later command (a host agent's unit, D-41)
sets ``park`` instead: the worktree, build and lock stay for the command that resumes it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.config import is_port, language_config, target_language_config
from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.config import (
    ConfigIssue,
    ConfigValidationError,
    IssueSeverity,
    LoadedConfig,
    load_config,
    locate_issues,
)
from codecoremorph.config.models import Loc, MigrationConfig, SandboxMode
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import ToolchainSpec
from codecoremorph.core.paths import HomeLayout
from codecoremorph.rules.applicability import StandardOrder, check_migration
from codecoremorph.rules.registry import RuleRegistry
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.selection import choose_executor
from codecoremorph.store.layout import RunArtifacts, StateDir
from codecoremorph.vcs.workspace import RunWorkspace

log = logging.getLogger(__name__)

WarningSink = Callable[[str], None]


def project_root(config_path: Path, config: MigrationConfig) -> Path:
    return (config_path.parent / config.project.root).resolve()


def migration_issues(
    path: Path,
    loaded: LoadedConfig,
    adapters: AdapterRegistry | None = None,
    rules: RuleRegistry | None = None,
) -> list[ConfigIssue]:
    """The manifest's standards and rules, checked against the installed plugins (D-38)."""
    cfg = loaded.config
    if rules is None:
        root = project_root(path, cfg)
        rules = RuleRegistry.discover([root / p for p in cfg.migration.rules_paths])
    adapters = adapters or AdapterRegistry.discover()

    def standards_of(language: str) -> list[str] | None:
        info = adapters.adapters.get(language)
        return info.adapter().capabilities().standards if info is not None else None

    migration = cfg.migration
    target_standards = standards_of(migration.target.language) if is_port(cfg) else None
    found: list[tuple[Loc, str, IssueSeverity]] = [
        (p.loc, p.message, IssueSeverity(p.severity))
        for p in check_migration(
            cfg, standards_of(migration.source.language), rules, target_standards
        )
    ]
    found += [((), f"plugin: {p}", IssueSeverity.WARNING) for p in adapters.problems]
    found += [((), f"rule pack: {p}", IssueSeverity.WARNING) for p in rules.problems]
    text = path.read_bytes().decode("utf-8-sig")
    return locate_issues(text, found)


def toolchain_spec(
    config: MigrationConfig, adapter: LanguageAdapter, target: LanguageAdapter | None = None
) -> ToolchainSpec:
    """The toolchain a run needs. A port's is the target language's (its image and native
    toolchain hold the source's too, for the mixed build), with both adapters' tools (D-56)."""
    spec = adapter.toolchain_requirements(language_config(config))
    if target is None:
        return spec
    other = target.toolchain_requirements(target_language_config(config))
    names = {t.name for t in other.tools}
    return other.model_copy(
        update={"tools": [*[t for t in spec.tools if t.name not in names], *other.tools]}
    )


def start_executor(
    config: MigrationConfig,
    mode: SandboxMode,
    adapter: LanguageAdapter,
    workspace: RunWorkspace,
    log_dir: Path,
    on_warning: WarningSink,
    target: LanguageAdapter | None = None,
) -> Executor:
    """Choose, provision and start the executor (FR-1.2, FR-1.3). A port (``target``) runs
    with the target language's toolchain."""
    from codecoremorph.sandbox.native import NativeExecutor
    from codecoremorph.sandbox.toolchains import require_tools

    spec = toolchain_spec(config, adapter, target)
    choice = choose_executor(mode)
    if choice.kind == "native" and mode is not SandboxMode.NATIVE:
        # The native executor itself warns about reduced isolation when it starts.
        on_warning(f"{choice.reason}, so commands run natively")
    if choice.kind == "native":
        native = NativeExecutor(
            log_dir=log_dir / "exec",
            toolchain_env=HomeLayout.resolve().toolchain_env(spec.language),
        )
        native.start()
        try:
            require_tools(native, spec, cwd=str(workspace.worktree.path))
        except BaseException:
            native.stop()
            raise
        return native

    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    sandbox = config.sandbox
    docker = DockerExecutor(
        DockerSettings(
            image=resolve_image(spec, sandbox.image),
            run_id=workspace.run_id,
            worktree=workspace.worktree.path,
            build_dir=workspace.build_dir,
            cache_dir=workspace.cache_dir,
            log_dir=log_dir / "exec",
            network=sandbox.network,
            cpus=sandbox.cpus,
            memory=sandbox.memory,
            pids_limit=sandbox.pids_limit,
            toolchain_env=f"/opt/codecoremorph/toolchains/{spec.language}.env",
        )
    )
    docker.start()
    return docker


def build_standard(adapter: LanguageAdapter, ctx: AdapterContext, config: MigrationConfig) -> str:
    """The standard to build the base commit with: the manifest's source standard, or the
    newer one the project's build files already set there (D-42).

    A unit started from a branch that has the standard bump must build at the new standard:
    forcing the source standard on it would verify its changes against the old one."""
    source = config.migration.source
    detected = adapter.project_standard(ctx)
    order = StandardOrder(source.language, tuple(adapter.capabilities().standards))
    if detected is None or not order.known(detected) or not order.newer(detected, source.standard):
        return source.standard
    log.info(
        "the build files already set %s, newer than the manifest's source %s: building at %s",
        detected,
        source.standard,
        detected,
    )
    return detected


def _stop(executor: Executor, *, keep_build: bool) -> None:
    """Stop the executor; a parked Docker run keeps its build volume for the next command."""
    if keep_build and executor.kind == "docker":
        from codecoremorph.sandbox.docker.executor import DockerExecutor

        if isinstance(executor, DockerExecutor):
            executor.stop(remove_volumes=False)
            return
    executor.stop()


def _log_warning(message: str) -> None:
    log.warning("%s", message)


@dataclass
class RunSession:
    config_path: Path
    loaded: LoadedConfig
    adapter: LanguageAdapter
    rules: RuleRegistry
    workspace: RunWorkspace
    artifacts: RunArtifacts
    executor: Executor
    ctx: AdapterContext
    warnings: list[ConfigIssue]
    # How the session ends; a command may change these before it returns.
    park: bool = False
    delete_branch: bool = True
    keep_build: bool = False
    # A port's target adapter, on the same worktree and executor, with its own cache (D-56).
    target_adapter: LanguageAdapter | None = None
    target_ctx: AdapterContext | None = None

    @property
    def config(self) -> MigrationConfig:
        return self.loaded.config

    @property
    def run_id(self) -> str:
        return self.workspace.run_id


@contextmanager
def open_session(
    config_path: Path,
    *,
    base: str | None = None,
    mode: SandboxMode | None = None,
    log_subdir: str = "logs",
    check: bool = True,
    keep_build: bool = False,
    delete_branch: bool = True,
    on_warning: WarningSink | None = None,
    resume: tuple[str, str] | None = None,
) -> Generator[RunSession]:
    """Validate the manifest, create the run workspace and start the executor.

    ``check`` repeats ``codecoremorph validate``'s standard and rule checks (D-38): errors raise
    :class:`ConfigValidationError`; warnings go to ``on_warning`` and ``RunSession.warnings``.
    ``resume`` (run ID, base commit) reattaches to a parked run instead of creating one; the
    session then parks again on exit unless the command clears ``park``.
    """
    warn = on_warning or _log_warning
    loaded = load_config(config_path)
    config = loaded.config
    root = project_root(config_path, config)
    adapters = AdapterRegistry.discover()
    rules = RuleRegistry.discover([root / p for p in config.migration.rules_paths])
    issues: list[ConfigIssue] = list(loaded.warnings)
    if check:
        issues += migration_issues(config_path, loaded, adapters, rules)
        if any(i.severity is IssueSeverity.ERROR for i in issues):
            raise ConfigValidationError(loaded.source, issues)
        for issue in issues:
            warn(issue.render(loaded.source))
    adapter = adapters.get(config.migration.source.language)
    target = adapters.get(config.migration.target.language) if is_port(config) else None
    if resume is None:
        run_id = new_run_id()
        workspace = RunWorkspace.create(root, run_id, base=base or config.migration.base)
    else:
        run_id, base_commit = resume
        workspace = RunWorkspace.resume(root, run_id, base_commit=base_commit)
    executor: Executor | None = None
    session: RunSession | None = None
    try:
        artifacts = StateDir.for_repo(workspace.repository.root).run(run_id).ensure()
        log_dir = artifacts.root / log_subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        executor = start_executor(
            config, mode or config.sandbox.mode, adapter, workspace, log_dir, warn, target
        )
        ctx = AdapterContext(
            worktree=workspace.worktree.path,
            build_dir=workspace.build_dir,
            cache_dir=workspace.cache_dir,
            log_dir=log_dir,
            executor=executor,
            lang=language_config(config),
        )
        standard = build_standard(adapter, ctx, config)
        if standard != ctx.lang.standard:
            ctx = replace(ctx, lang=language_config(config, standard=standard))
        target_ctx = (
            None
            if target is None
            else replace(
                ctx,
                cache_dir=workspace.cache_dir / target.language,
                log_dir=log_dir / target.language,
                lang=target_language_config(config),
            )
        )
        session = RunSession(
            config_path=config_path,
            loaded=loaded,
            adapter=adapter,
            rules=rules,
            workspace=workspace,
            artifacts=artifacts,
            executor=executor,
            ctx=ctx,
            warnings=issues,
            park=resume is not None,
            delete_branch=delete_branch,
            keep_build=keep_build,
            target_adapter=target,
            target_ctx=target_ctx,
        )
        yield session
    finally:
        if executor is not None:
            parking = session.park if session is not None else resume is not None
            _stop(executor, keep_build=parking)
        if session is not None and session.park:
            workspace.park()
        elif session is None and resume is not None:
            workspace.park()  # the resumed run failed to start; leave it as it was
        else:
            workspace.cleanup(
                delete_branch=session.delete_branch if session else delete_branch,
                keep_build=session.keep_build if session else keep_build,
            )
