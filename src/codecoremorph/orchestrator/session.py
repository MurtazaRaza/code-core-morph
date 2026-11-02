"""A run's environment: the manifest and its plugins, the run workspace, the executor and the
adapter context (SPEC §4.4: validate config, prepare workspace, provision toolchain).

``codecoremorph debug``, ``plan`` and (from Phase 5) ``run`` open one of these. Closing it stops
the executor and removes the worktree; the run branch stays unless ``delete_branch`` is set.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.config import language_config
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
from codecoremorph.core.paths import HomeLayout
from codecoremorph.rules.applicability import check_migration
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
    info = adapters.adapters.get(cfg.migration.source.language)
    standards = info.adapter().capabilities().standards if info is not None else None
    found: list[tuple[Loc, str, IssueSeverity]] = [
        (p.loc, p.message, IssueSeverity(p.severity))
        for p in check_migration(cfg, standards, rules)
    ]
    found += [((), f"plugin: {p}", IssueSeverity.WARNING) for p in adapters.problems]
    found += [((), f"rule pack: {p}", IssueSeverity.WARNING) for p in rules.problems]
    text = path.read_bytes().decode("utf-8-sig")
    return locate_issues(text, found)


def start_executor(
    config: MigrationConfig,
    mode: SandboxMode,
    adapter: LanguageAdapter,
    workspace: RunWorkspace,
    log_dir: Path,
    on_warning: WarningSink,
) -> Executor:
    """Choose, provision and start the executor (FR-1.2, FR-1.3)."""
    from codecoremorph.sandbox.native import NativeExecutor
    from codecoremorph.sandbox.toolchains import require_tools

    lang = language_config(config)
    spec = adapter.toolchain_requirements(lang)
    choice = choose_executor(mode)
    if choice.kind == "native" and mode is not SandboxMode.NATIVE:
        # The native executor itself warns about reduced isolation when it starts.
        on_warning(f"{choice.reason}, so commands run natively")
    if choice.kind == "native":
        native = NativeExecutor(
            log_dir=log_dir / "exec",
            toolchain_env=HomeLayout.resolve().toolchain_env(lang.language),
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
            toolchain_env=f"/opt/codecoremorph/toolchains/{lang.language}.env",
        )
    )
    docker.start()
    return docker


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
) -> Generator[RunSession]:
    """Validate the manifest, create the run workspace and start the executor.

    ``check`` repeats ``codecoremorph validate``'s standard and rule checks (D-38): errors raise
    :class:`ConfigValidationError`; warnings go to ``on_warning`` and ``RunSession.warnings``.
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
    run_id = new_run_id()
    workspace = RunWorkspace.create(root, run_id, base=base or config.migration.base)
    executor: Executor | None = None
    try:
        artifacts = StateDir.for_repo(workspace.repository.root).run(run_id).ensure()
        log_dir = artifacts.root / log_subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        executor = start_executor(
            config, mode or config.sandbox.mode, adapter, workspace, log_dir, warn
        )
        ctx = AdapterContext(
            worktree=workspace.worktree.path,
            build_dir=workspace.build_dir,
            cache_dir=workspace.cache_dir,
            log_dir=log_dir,
            executor=executor,
            lang=language_config(config),
        )
        yield RunSession(
            config_path=config_path,
            loaded=loaded,
            adapter=adapter,
            rules=rules,
            workspace=workspace,
            artifacts=artifacts,
            executor=executor,
            ctx=ctx,
            warnings=issues,
        )
    finally:
        if executor is not None:
            executor.stop()
        workspace.cleanup(delete_branch=delete_branch, keep_build=keep_build)
