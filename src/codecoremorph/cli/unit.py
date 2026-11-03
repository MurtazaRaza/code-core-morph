"""``codecoremorph unit``: one work unit with a host agent making the edits (D-41, SPEC §7.6.1).

The host agent (Claude Code, through the ``codecoremorph`` skill) runs these commands; it reads
the unit's context document, writes its edits to the edits file and submits them. CodeCoreMorph
makes no model calls. The run parks between commands; the repository has one active unit.

  list    the plan's units and how far each got
  start   validate, baseline, index, codemods, verify; then wait for edits (or finish)
  submit  apply the edits file, build, test; commit, or feedback for the next attempt
  abort   discard the unit and its run branch
  status  the active unit, or the last one
  context print the current context document again
  read, ls, grep, symbol, refs, signature   read the run's worktree and symbol index
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from codecoremorph.agent.host import HostFiles, HostStep
from codecoremorph.cli.output import handle_errors, stderr
from codecoremorph.config import DEFAULT_CONFIG_NAME
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import ExitCode
from codecoremorph.core.ids import run_branch
from codecoremorph.core.models import HostStatus, HostUnitState

unit_app = typer.Typer(
    help="Migrate one work unit with your coding agent making the edits (no model calls).",
    no_args_is_help=True,
)

ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", help="Migration manifest.", show_default=True)
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print the result as JSON.")]
DEFAULT_CONFIG = Path(DEFAULT_CONFIG_NAME)


def _echo(text: str) -> None:
    """Print text exactly, in UTF-8: the agent copies `search` texts from this output."""
    stream = sys.stdout
    if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower().replace("-", "") != "utf8":
        stream.reconfigure(encoding="utf-8", errors="replace")
    typer.echo(text)


def _progress(message: str) -> None:
    stderr().print(Text(f"... {message}", style="dim"))


def _warn(message: str) -> None:
    stderr().print(Text(f"warning: {message}", style="yellow"))


def _payload(
    state: HostUnitState, message: str, warnings: list[str], files: HostFiles
) -> dict[str, Any]:
    waiting = state.status is HostStatus.WAITING
    return {
        "run_id": state.run_id,
        "unit": state.unit.model_dump(mode="json"),
        "status": state.status.value,
        "attempt": state.attempt,
        "attempts_allowed": state.attempts_allowed,
        "message": message,
        "warnings": warnings,
        "context_file": str(files.context) if waiting else None,
        "edits_file": str(files.edits) if waiting else None,
        "branch": run_branch(state.run_id) if state.commit else None,
        "commit": state.commit,
        "changed_files": state.changed_files,
        "summary": state.summary,
        "review_notes": state.review_notes,
        "reason": state.reason,
        "attempts": [a.model_dump(mode="json") for a in state.attempts],
        "plan_unit": state.plan_unit,
        "standard": state.configured_standard,
        "excused_tests": state.excused_tests,
        "uncompiled": state.uncompiled,
    }


def _listed(items: list[str], limit: int = 10) -> str:
    more = f" and {len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def _unverified_lines(state: HostUnitState) -> list[str]:
    """What the unit's verification didn't cover, for the agent's report to the user."""
    lines: list[str] = []
    if state.excused_tests:
        lines.append(
            f"excused: {len(state.excused_tests)} test(s) already failed or were flaky before "
            f"the unit, so they don't count: {_listed(state.excused_tests)}"
        )
    if state.uncompiled:
        lines.append(
            f"not compiled: this build never compiles {len(state.uncompiled)} file(s), so it "
            f"can't check them: {_listed(state.uncompiled)}"
        )
    return lines


def _report(step: HostStep, files: HostFiles, as_json: bool) -> None:
    state, message, warnings = step.state, step.message, step.warnings
    if as_json:
        _echo(json.dumps(_payload(state, message, warnings, files), indent=2))
    else:
        for warning in warnings:
            _echo(f"warning: {warning}")
        _echo(message)
        if state.status is HostStatus.SUCCEEDED and state.commit:
            for note in state.review_notes:
                _echo(f"review: {note}")
            for line in _unverified_lines(state):
                _echo(line)
            _echo(
                f"The change is on branch {run_branch(state.run_id)}; the project's own branch "
                f"is unchanged. Review it with `git diff {state.base_commit[:12]} "
                f"{run_branch(state.run_id)}`."
            )
    if state.status is HostStatus.FAILED:
        raise typer.Exit(int(ExitCode.COMPLETED_WITH_FAILURES))


@unit_app.command("start")
@handle_errors
def unit_start(
    rule: Annotated[
        list[str] | None,
        typer.Option("--rule", "-r", help="Rule of the unit (repeat for several)."),
    ] = None,
    files: Annotated[
        list[str] | None,
        typer.Argument(
            help="The unit's files, relative to the repository root (default for project "
            "rules: the build files).",
            show_default=False,
        ),
    ] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    base: Annotated[
        str | None,
        typer.Option("--base", help="Commit to start from, e.g. an earlier unit's branch."),
    ] = None,
    sandbox: Annotated[
        SandboxMode | None, typer.Option("--sandbox", help="Override sandbox.mode.")
    ] = None,
    plan_unit: Annotated[
        str | None,
        typer.Option(
            "--plan-unit",
            help="Run this unit of the latest plan (e.g. U-0002): its rules and files.",
        ),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Start a unit: baseline, index, codemods and checks, then wait for the agent's edits.

    Name the unit with --plan-unit (see `codecoremorph unit list`), or with --rule and files.
    Prints where the context document and the edits file are. If the deterministic tools
    finish the unit alone, it is committed to its run branch right away.
    """
    from codecoremorph.core.errors import UsageError
    from codecoremorph.orchestrator.session import open_session
    from codecoremorph.orchestrator.unit import HostRun, start_host_unit

    planned = None
    if plan_unit is not None:
        if rule or files:
            raise UsageError(
                "--plan-unit names the unit's rules and files; don't add --rule or files",
                hint=f"Run `codecoremorph unit start --plan-unit {plan_unit}` alone.",
            )
        planned = HostRun.for_config(config).plan_unit(plan_unit)
    elif not rule:
        raise UsageError(
            "name the unit: --plan-unit ID, or --rule RULE with its files",
            hint="`codecoremorph unit list` shows the plan's units.",
        )
    else:
        HostRun.for_config(config).refuse_skipped(list(rule), list(files or []))
    with open_session(config, base=base, mode=sandbox, log_subdir="logs", on_warning=_warn) as s:
        _progress(f"run {s.run_id} ({s.executor.kind} mode, {s.ctx.lang.standard})")
        step = start_host_unit(
            s,
            rule_ids=list(rule or []),
            files=list(files or []),
            on_step=_progress,
            plan_unit=planned,
        )
        host_files = HostFiles(s.artifacts.root)
    _report(step, host_files, as_json)


@unit_app.command("list")
@handle_errors
def unit_list(config: ConfigOpt = DEFAULT_CONFIG, as_json: JsonOpt = False) -> None:
    """The latest plan's units, in order, with what happened to each one so far."""
    from codecoremorph.orchestrator.unit import HostRun

    run = HostRun.for_config(config)
    path, plan = run.plan()
    progress = run.plan_progress()
    rows: list[dict[str, Any]] = []
    for unit in plan.units:
        state = progress.get(unit.id)
        rows.append(
            {
                "id": unit.id,
                "rules": unit.rules,
                "files": unit.files,
                "reference_files": unit.reference_files,
                "plan_status": unit.status.value,
                "flags": [f.value for f in unit.flags],
                "note": unit.note,
                "outcome": state.status.value if state else None,
                "run_id": state.run_id if state else None,
                "branch": run_branch(state.run_id) if state and state.commit else None,
                "reason": state.reason if state else None,
            }
        )
    done = [s for s in progress.values() if s.status is HostStatus.SUCCEEDED and s.commit]
    last = max(done, key=lambda s: s.updated_at, default=None)
    if as_json:
        payload = {
            "plan": str(path),
            "units": rows,
            "last_branch": run_branch(last.run_id) if last else None,
        }
        _echo(json.dumps(payload, indent=2))
        return
    _echo(f"plan: {path}")
    for row in rows:
        state_text = row["outcome"] or row["plan_status"]
        if row["branch"]:
            state_text += f" ({row['branch']})"
        flags = f" [{', '.join(row['flags'])}]" if row["flags"] else ""
        _echo(
            f"{row['id']} {state_text}: {', '.join(row['rules'])} on {len(row['files'])} "
            f"file(s){flags}"
        )
        if row["plan_status"] != "pending" and row["note"]:
            _echo(f"    {row['note']}")
        if row["reason"]:
            _echo(f"    reason: {row['reason']}")
    if last is not None:
        _echo(
            f"last successful unit: {last.plan_unit or last.unit.id} on {run_branch(last.run_id)}"
            "; start the next unit with --base on it"
        )


@unit_app.command("submit")
@handle_errors
def unit_submit(config: ConfigOpt = DEFAULT_CONFIG, as_json: JsonOpt = False) -> None:
    """Apply the edits file, build and test. Prints the result or the feedback to fix."""
    from codecoremorph.orchestrator.session import open_session
    from codecoremorph.orchestrator.unit import (
        HostRun,
        end_session,
        resume_host_unit,
        sandbox_mode,
        user_tree_warnings,
    )

    state = HostRun.for_config(config).active()
    with open_session(
        config,
        mode=sandbox_mode(state),
        check=False,
        on_warning=_warn,
        resume=(state.run_id, state.base_commit),
    ) as s:
        host = resume_host_unit(s, state)
        step = host.submit(state)
        step.warnings += user_tree_warnings(s, state)
        end_session(s, step.state)
        host_files = HostFiles(s.artifacts.root)
    _report(step, host_files, as_json)


@unit_app.command("abort")
@handle_errors
def unit_abort(
    config: ConfigOpt = DEFAULT_CONFIG,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why the unit can't be completed.")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Give the unit up: remove its worktree and run branch. Nothing is kept."""
    from codecoremorph.orchestrator.unit import HostRun, abort_host_unit

    run = HostRun.for_config(config)
    step = abort_host_unit(run, reason)
    _report(step, run.files(step.state.run_id), as_json)


@unit_app.command("status")
@handle_errors
def unit_status(config: ConfigOpt = DEFAULT_CONFIG, as_json: JsonOpt = False) -> None:
    """Show the active unit, or the last one."""
    from codecoremorph.core.errors import RunNotActiveError
    from codecoremorph.orchestrator.unit import HostRun

    run = HostRun.for_config(config)
    try:
        state = run.active()
    except RunNotActiveError:
        latest = run.latest()
        if latest is None:
            raise
        state = latest
    files = run.files(state.run_id)
    lines = [
        f"run {state.run_id}, unit {state.unit.id}: {state.status.value}",
        f"rules: {', '.join(state.unit.rules)}",
        f"files: {', '.join(state.unit.files)}",
        f"attempt {state.attempt} of {state.attempts_allowed}",
    ]
    if state.status is HostStatus.WAITING:
        lines += [f"context: {files.context}", f"edits file: {files.edits}"]
    if state.commit:
        lines.append(f"commit {state.commit[:12]} on {run_branch(state.run_id)}")
    if state.reason:
        lines.append(f"reason: {state.reason}")
    lines += _unverified_lines(state)
    message = "\n".join(lines)
    if as_json:
        _echo(json.dumps(_payload(state, message, [], files), indent=2))
    else:
        _echo(message)


@unit_app.command("context")
@handle_errors
def unit_context(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Print the active unit's context document (also in its context.md)."""
    from codecoremorph.orchestrator.unit import HostRun

    run = HostRun.for_config(config)
    state = run.active()
    _echo(run.files(state.run_id).context.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- read-only tools


def _tool(config: Path, name: str, arguments: dict[str, Any]) -> None:
    from codecoremorph.agent.tools import ToolBox
    from codecoremorph.orchestrator.unit import HostRun

    state = HostRun.for_config(config).active()
    index = Path(state.index_path) if state.index_path else None
    output = ToolBox(Path(state.worktree), index).run(name, arguments)
    _echo(output.content)
    if output.is_error:
        raise typer.Exit(int(ExitCode.CONFIG_OR_USAGE))


@unit_app.command("read")
@handle_errors
def unit_read(
    path: Annotated[str, typer.Argument(help="File, relative to the repository root.")],
    start: Annotated[int | None, typer.Option("--from", help="First line (1-based).")] = None,
    end: Annotated[int | None, typer.Option("--to", help="Last line.")] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Print a file of the run's worktree, as the unit sees it now."""
    _tool(config, "read_file", {"path": path, "start_line": start, "end_line": end})


@unit_app.command("ls")
@handle_errors
def unit_ls(
    path: Annotated[str, typer.Argument(help="Directory, relative to the root.")] = ".",
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """List a directory of the run's worktree."""
    _tool(config, "list_dir", {"path": path})


@unit_app.command("grep")
@handle_errors
def unit_grep(
    regex: Annotated[str, typer.Argument(help="Python regular expression, matched per line.")],
    glob: Annotated[str | None, typer.Option("--glob", help="Only paths matching this.")] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Search the run's worktree."""
    _tool(config, "search_code", {"regex": regex, "glob": glob})


@unit_app.command("symbol")
@handle_errors
def unit_symbol(
    name: Annotated[str, typer.Argument(help="Name or qualified name, e.g. geo::area.")],
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Find symbols in the index: their IDs, kinds and locations."""
    _tool(config, "find_symbol", {"name": name})


@unit_app.command("refs")
@handle_errors
def unit_refs(
    symbol_id: Annotated[str, typer.Argument(help="A symbol ID from `unit symbol`.")],
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """List the references to a symbol."""
    _tool(config, "get_references", {"symbol_id": symbol_id})


@unit_app.command("signature")
@handle_errors
def unit_signature(
    symbol_id: Annotated[str, typer.Argument(help="A symbol ID from `unit symbol`.")],
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Print a symbol's declarations and definition."""
    _tool(config, "get_signature", {"symbol_id": symbol_id})
