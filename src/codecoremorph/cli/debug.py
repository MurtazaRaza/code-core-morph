"""``codecoremorph debug build|test|index|codemod``: run one adapter step in a throwaway run
workspace (SPEC §7.14). The user's working tree is never touched."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stderr, stdout
from codecoremorph.config import DEFAULT_CONFIG_NAME
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import ExitCode
from codecoremorph.core.models import BuildResult, Diagnostic, Severity
from codecoremorph.orchestrator.session import RunSession, open_session

log = logging.getLogger(__name__)

debug_app = typer.Typer(help="Run one adapter step in a throwaway workspace.", no_args_is_help=True)

ConfigArg = Annotated[Path, typer.Argument(help="Migration manifest.")]
BaseOpt = Annotated[str | None, typer.Option("--base", help="Commit to start from.")]
KeepOpt = Annotated[
    bool, typer.Option("--keep", help="Keep the worktree, build dir and run branch afterwards.")
]
SandboxOpt = Annotated[SandboxMode | None, typer.Option("--sandbox", help="Override sandbox.mode.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print the result as JSON.")]


def _warn(message: str) -> None:
    stderr().print(Text(f"warning: {message}", style="yellow"))


@contextmanager
def session(
    config_path: Path, *, base: str | None, keep: bool, mode: SandboxMode | None
) -> Iterator[RunSession]:
    """A throwaway run workspace; ``keep`` keeps its branch, worktree build and caches."""
    with open_session(
        config_path,
        base=base,
        mode=mode,
        log_subdir="debug",
        check=False,
        keep_build=keep,
        delete_branch=not keep,
        on_warning=_warn,
    ) as s:
        stderr().print(
            Text(f"run {s.run_id}: worktree {s.workspace.worktree.path} ({s.executor.kind} mode)")
        )
        yield s
        if keep:
            stderr().print(
                Text(f"kept branch {s.workspace.worktree.branch} and build {s.workspace.build_dir}")
            )


def _print_diagnostics(result: BuildResult, step: str) -> None:
    console = stdout()
    table = Table(box=None, show_header=False, pad_edge=False)
    for diagnostic in result.diagnostics:
        if diagnostic.severity is Severity.NOTE:
            continue
        table.add_row(
            status_label("fail" if diagnostic.severity is Severity.ERROR else "warn"),
            Text(_where(diagnostic)),
            Text(diagnostic.message + (f" [{diagnostic.code}]" if diagnostic.code else "")),
        )
    if result.diagnostics:
        console.print(table)
    console.print(
        Text.assemble(
            status_label("ok" if result.success else "fail"),
            f" {step}: {len(result.errors)} errors, {len(result.warnings)} warnings "
            f"in {result.duration_s:.1f} s (log: {result.log_path})",
        )
    )


def _where(diagnostic: Diagnostic) -> str:
    if diagnostic.file_path is None:
        return diagnostic.origin.value
    return f"{diagnostic.file_path}:{diagnostic.line or '?'}"


def _configure(s: RunSession, as_json: bool) -> bool:
    result = s.adapter.configure(s.ctx)
    if not result.success or not as_json:
        _print_diagnostics(result, "configure")
    return result.success


@debug_app.command("build")
@handle_errors
def debug_build(
    config: ConfigArg = Path(DEFAULT_CONFIG_NAME),
    base: BaseOpt = None,
    keep: KeepOpt = False,
    sandbox: SandboxOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Configure and build; print the structured diagnostics."""
    with session(config, base=base, keep=keep, mode=sandbox) as s:
        if not _configure(s, as_json):
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))
        result = s.adapter.build(s.ctx)
        if as_json:
            typer.echo(result.model_dump_json(indent=2))
        else:
            _print_diagnostics(result, "build")
        if not result.success:
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))


@debug_app.command("test")
@handle_errors
def debug_test(
    config: ConfigArg = Path(DEFAULT_CONFIG_NAME),
    base: BaseOpt = None,
    keep: KeepOpt = False,
    sandbox: SandboxOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Configure, build and run the tests; print every test ID and status."""
    with session(config, base=base, keep=keep, mode=sandbox) as s:
        if not _configure(s, as_json):
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))
        built = s.adapter.build(s.ctx)
        if not built.success:
            _print_diagnostics(built, "build")
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))
        report = s.adapter.run_tests(s.ctx)
        if as_json:
            typer.echo(report.model_dump_json(indent=2))
        else:
            table = Table(box=None, show_header=False, pad_edge=False)
            for case in report.cases:
                status = (
                    "ok"
                    if case.status.value == "passed"
                    else ("info" if case.status.value == "skipped" else "fail")
                )
                table.add_row(status_label(status), case.test_id, Text(case.status.value))
            stdout().print(table)
            counts: dict[str, int] = {}
            for case in report.cases:
                counts[case.status.value] = counts.get(case.status.value, 0) + 1
            summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no tests"
            stdout().print(Text(f"{summary} in {report.duration_s:.1f} s (log: {report.log_path})"))
        if any(c.status.value in ("failed", "error", "timeout") for c in report.cases):
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))


@debug_app.command("index")
@handle_errors
def debug_index(
    config: ConfigArg = Path(DEFAULT_CONFIG_NAME),
    base: BaseOpt = None,
    keep: KeepOpt = False,
    sandbox: SandboxOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Configure and index; print the index statistics and timings."""
    with session(config, base=base, keep=keep, mode=sandbox) as s:
        if not _configure(s, as_json):
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))
        index = s.adapter.index(s.ctx)
        if as_json:
            typer.echo(index.model_dump_json(indent=2))
            return
        meta = index.meta
        lines = [f"{key}: {value}" for key, value in sorted(meta.stats.items())]
        lines += [f"{key}: {value:.2f} s" for key, value in sorted(meta.timings.items())]
        lines += [f"{tool}: {version}" for tool, version in sorted(meta.tool_versions.items())]
        stdout().print(Text("\n".join(lines)))
        for problem in index.problems[:20]:
            stdout().print(Text(f"problem: {problem}", style="yellow"))
        if keep:
            stdout().print(Text(f"index: {index.path}"))


@debug_app.command("codemod")
@handle_errors
def debug_codemod(
    rule: Annotated[str, typer.Option("--rule", "-r", help="Rule whose codemods to run.")],
    files: Annotated[
        list[str] | None,
        typer.Argument(
            help="Worktree-relative files (default: every indexed file).", show_default=False
        ),
    ] = None,
    config: Annotated[Path, typer.Option("--config", "-c", help="Migration manifest.")] = Path(
        DEFAULT_CONFIG_NAME
    ),
    base: BaseOpt = None,
    keep: KeepOpt = False,
    sandbox: SandboxOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Run a rule's codemods and print the resulting diff."""
    with session(config, base=base, keep=keep, mode=sandbox) as s:
        loaded = s.rules.get(rule)
        if not _configure(s, as_json):
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))
        targets = list(files or [])
        if not targets:
            from codecoremorph.store.index import IndexStore

            index = s.adapter.index(s.ctx)
            with IndexStore.open(index.path) as store:
                targets = [
                    f.path
                    for f in store.files()
                    if f.role.value not in ("third_party", "generated")
                    and not (f.c_compatible and "c_compatible" in loaded.rule.skip)
                ]
        result = s.adapter.run_codemods(s.ctx, list(loaded.rule.codemods), targets)
        diff = s.workspace.worktree.diff_bytes(s.workspace.base_commit).decode("utf-8", "replace")
        if as_json:
            typer.echo(
                json.dumps({"result": result.model_dump(mode="json"), "diff": diff}, indent=2)
            )
            return
        stdout().print(Text(diff or "(no changes)"))
        stdout().print(
            Text(
                f"{sum(c.replacements for c in result.changes)} replacements in "
                f"{len(result.changes)} files; {len(result.skipped)} skipped; "
                f"{len(result.remaining)} sites left for the agent"
            )
        )
        for skipped in result.skipped:
            stdout().print(Text(f"skipped {skipped.file} ({skipped.check}): {skipped.reason}"))
