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
from codecoremorph.core.models import BuildResult, Diagnostic, Role, Severity
from codecoremorph.orchestrator.session import RunSession, open_session
from codecoremorph.orchestrator.unit import SingleUnitResult

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


@debug_app.command("unit")
@handle_errors
def debug_unit(
    rule: Annotated[
        list[str], typer.Option("--rule", "-r", help="Rule of the unit (repeat for several).")
    ],
    files: Annotated[
        list[str] | None,
        typer.Argument(
            help="The unit's files, worktree-relative (default for project rules: the build "
            "files).",
            show_default=False,
        ),
    ] = None,
    config: Annotated[Path, typer.Option("--config", "-c", help="Migration manifest.")] = Path(
        DEFAULT_CONFIG_NAME
    ),
    base: BaseOpt = None,
    keep: Annotated[
        bool,
        typer.Option(
            "--keep",
            help="Commit a successful unit to its run branch and keep the branch and build.",
        ),
    ] = False,
    sandbox: SandboxOpt = None,
    record: Annotated[
        Path | None,
        typer.Option("--record", help="Record every model call to this cassette file."),
    ] = None,
    replay: Annotated[
        Path | None,
        typer.Option("--replay", help="Replay model calls from this cassette; no live calls."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Migrate one work unit end to end: codemods, agent, build, tests, feedback, retries.

    The unit's changes are shown as a diff; transcripts, diffs and logs of every attempt are
    kept under .codecoremorph/runs/<run-id>/attempts/.
    """
    from codecoremorph.core.errors import UsageError
    from codecoremorph.llm.base import ProviderRegistry
    from codecoremorph.llm.ledger import UsageLedger
    from codecoremorph.llm.replay import cassette_providers
    from codecoremorph.llm.service import LLMService
    from codecoremorph.orchestrator.unit import run_single_unit

    if record is not None and replay is not None:
        raise UsageError("use --record or --replay, not both")
    with open_session(
        config,
        base=base,
        mode=sandbox,
        log_subdir="logs",
        keep_build=keep,
        delete_branch=not keep,
        on_warning=_warn,
    ) as s:
        guardrails = s.config.guardrails
        ledger = UsageLedger(
            max_cost_usd=guardrails.max_run_cost_usd, max_tokens=guardrails.max_run_tokens
        )
        registry = ProviderRegistry.discover()
        providers = None
        cassette = record or replay
        if cassette is not None:
            llm_config = s.config.llm
            names = sorted({c.provider for c in [llm_config.for_role(r) for r in Role]})
            providers = cassette_providers(
                names,
                cassette,
                record=record is not None,
                create=lambda name: registry.create(name, llm_config),
            )
        service = LLMService(s.config.llm, ledger=ledger, registry=registry, providers=providers)
        stderr().print(
            Text(f"run {s.run_id}: worktree {s.workspace.worktree.path} ({s.executor.kind} mode)")
        )
        try:
            result = run_single_unit(
                s,
                rule_ids=list(rule),
                files=list(files or []),
                llm=service,
                on_step=lambda message: stderr().print(Text(f"... {message}", style="dim")),
            )
        finally:
            service.close()
        outcome = result.outcome
        if keep and outcome.succeeded and outcome.changed_files:
            s.workspace.worktree.commit(
                f"codecoremorph(debug unit): {', '.join(rule)}\n\n{outcome.summary or ''}",
                author=s.config.git.author,
            )
        totals = ledger.total
        if as_json:
            payload = {
                "run_id": s.run_id,
                "unit": result.unit.model_dump(mode="json"),
                "succeeded": outcome.succeeded,
                "agent_skipped": outcome.agent_skipped,
                "reason": outcome.reason,
                "summary": outcome.summary,
                "review_notes": outcome.review_notes,
                "warnings": outcome.warnings,
                "changed_files": outcome.changed_files,
                "attempts": [a.model_dump(mode="json") for a in outcome.attempts],
                "usage": totals.usage.model_dump(),
                "cost_usd": totals.cost_usd,
                "diff": outcome.diff,
            }
            typer.echo(json.dumps(payload, indent=2))
        else:
            _print_unit(result, totals.usage.total, totals.cost_usd, s.artifacts.root)
        if not outcome.succeeded:
            raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))


def _print_unit(result: SingleUnitResult, tokens: int, cost: float, run_dir: Path) -> None:
    console = stdout()
    outcome = result.outcome
    console.print(Text(outcome.diff or "(no changes)"))
    table = Table(box=None, pad_edge=False)
    for column in ("attempt", "role", "model", "outcome", "files"):
        table.add_column(column)
    for attempt in outcome.attempts:
        table.add_row(
            str(attempt.number),
            attempt.role.value,
            attempt.model or "-",
            (attempt.outcome.value if attempt.outcome else "-"),
            Text(", ".join(attempt.changed_files) or "-"),
        )
    if outcome.attempts:
        console.print(table)
    if outcome.agent_skipped:
        console.print(Text("codemods alone completed the unit; the agent was skipped"))
    for note in outcome.review_notes:
        console.print(Text(f"review: {note}", style="cyan"))
    for warning in outcome.warnings:
        console.print(Text(f"warning: {warning}", style="yellow"))
    status = "ok" if outcome.succeeded else "fail"
    summary = outcome.summary if outcome.succeeded else outcome.reason
    console.print(
        Text.assemble(
            status_label(status),
            f" {result.unit.id}: {summary or ('done' if outcome.succeeded else 'failed')}",
        )
    )
    console.print(Text(f"{tokens:,} tokens, ${cost:.2f}; attempts: {run_dir / 'attempts'}"))
