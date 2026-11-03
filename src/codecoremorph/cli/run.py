"""Whole runs: ``codecoremorph run|resume|status|approve|reject|abort|report|clean`` (FR-11,
SPEC §7.14).

``run`` drives a migration until it ends or parks. At a gate it asks on an interactive
terminal; otherwise (or after ``later``) it waits for ``codecoremorph approve|reject`` from
another terminal or the dashboard (FR-11.4). Ctrl-C parks the run (``resume`` continues it), a
second Ctrl-C stops at once, and ``abort`` ends it with a report.

``run --host-agent`` (D-44) is for a coding agent that runs CodeCoreMorph as a tool: the run
parks, exit code 0, whenever it needs the agent's edits (``codecoremorph unit submit``) or a
decision (``approve|reject``, then ``resume``), and the output says which.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stderr, stdout
from codecoremorph.config import DEFAULT_CONFIG_NAME
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    Plan,
    RunReport,
    RunStatus,
    UnitStatus,
)
from codecoremorph.orchestrator.lifecycle import (
    RunPlace,
    abort_run,
    clean_runs,
    overview,
    record_decision,
    resume_run,
    start_run,
)
from codecoremorph.orchestrator.run import AGENT_GATE, RunOptions, RunResult
from codecoremorph.report.build import load_unit_result
from codecoremorph.report.render import render_markdown

ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", help="Migration manifest.", show_default=True)
]
RunArg = Annotated[
    str | None, typer.Argument(help="The run's ID (default: the latest run).", show_default=False)
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON.")]
DEFAULT_CONFIG = Path(DEFAULT_CONFIG_NAME)


def _step(message: str) -> None:
    stderr().print(Text(f"... {message}", style="dim"))


def _warn(message: str) -> None:
    stderr().print(Text(f"warning: {message}", style="yellow"))


def _stop_requested() -> None:
    stderr().print(
        Text(
            "stopping at the next safe point (the unit in progress rolls back); "
            "press Ctrl-C again to stop at once",
            style="yellow",
        )
    )


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# --------------------------------------------------------------------------- gate prompts


def _ask(place: RunPlace) -> Any:
    """The interactive gate prompt; None means "later": the run parks at the gate."""

    def ask(request: dict[str, Any]) -> ApprovalRecord | None:
        run_id = str(request["run_id"])
        gate = ApprovalGate(str(request["gate"]))
        console = stdout()
        if gate is ApprovalGate.PLAN:
            plan_path = place.state_dir.run(run_id).plan_json
            if plan_path.is_file():
                _print_plan(Plan.model_validate_json(plan_path.read_bytes()))
            console.print(Text(f"plan: {plan_path}"))
            question = "Approve the plan? [y]es, [n]o (reject), [e]xclude units, [l]ater"
        else:
            unit = str(request.get("unit"))
            console.print(Text(f"Unit {unit} needs your approval:", style="bold"))
            for reason in request.get("reasons") or []:
                console.print(Text(f"  - {reason}"))
            commit = request.get("commit")
            if commit:
                console.print(
                    Text(f"  commit {str(commit)[:12]} (see `git show {str(commit)[:12]}`)")
                )
            question = "Keep the unit? [y]es, [n]o (roll it back), [l]ater"
        while True:
            answer = typer.prompt(question, default="l").strip().lower()
            exclude: list[str] = []
            if answer.startswith("e") and gate is ApprovalGate.PLAN:
                exclude = (
                    typer.prompt("Units to exclude (e.g. U-0003,U-0007)")
                    .replace(" ", "")
                    .split(",")
                )
                answer = "y"
            if answer.startswith("y"):
                decision = ApprovalDecision.APPROVE
            elif answer.startswith("n"):
                decision = ApprovalDecision.REJECT
            elif answer.startswith("l"):
                stderr().print(
                    Text(
                        f"The run waits: answer with `codecoremorph approve {run_id}` (or reject), "
                        f"then `codecoremorph resume {run_id}`."
                    )
                )
                return None
            else:
                continue
            reason = typer.prompt("Reason (optional)", default="", show_default=False) or None
            return ApprovalRecord(
                run_id=run_id,
                gate=gate,
                unit_id=str(request["unit"]) if gate is ApprovalGate.UNIT else None,
                decision=decision,
                reason=reason,
                exclude=[e.upper() for e in exclude if e],
                source="cli",
                created_at=datetime.now(UTC),
            )

    return ask


def _plan_lines(plan: Plan, *, cost: bool = False) -> list[str]:
    lines = [
        f"Plan: {plan.language} {plan.source_standard} -> {plan.target_standard}, "
        f"{len(plan.units)} units"
    ]
    for unit in plan.units:
        flags = f" [{', '.join(f.value for f in unit.flags)}]" if unit.flags else ""
        status = "" if unit.status is UnitStatus.PENDING else f" ({unit.status.value})"
        files = ", ".join(unit.files[:3]) + (
            f" (+{len(unit.files) - 3})" if len(unit.files) > 3 else ""
        )
        lines.append(f"  {unit.id}{status}  {', '.join(unit.rules)}: {files}{flags}")
        if unit.status is not UnitStatus.PENDING and unit.note:
            lines.append(f"      {unit.note}")
    if cost and plan.estimate is not None and plan.estimate.cost_usd is not None:
        lines.append(f"  estimated cost: about ${plan.estimate.cost_usd:,.2f}")
    return lines


def _print_plan(plan: Plan) -> None:
    console = stdout()
    first, *rest = _plan_lines(plan, cost=True)
    console.print(Text(first, style="bold"))
    for line in rest:
        console.print(Text(line))


def _options(place: RunPlace, *, yes: bool, host: bool = False) -> RunOptions:
    return RunOptions(
        yes=yes,
        ask=_ask(place) if _interactive() and not host else None,
        on_step=_step,
        on_warning=_warn,
        on_stop_requested=_stop_requested,
        host=host,
    )


def host_options() -> RunOptions:
    """Options for a host agent's command: no prompts; a gate without a decision parks."""
    return RunOptions(on_step=_step, on_warning=_warn, on_stop_requested=_stop_requested, host=True)


def _echo(text: str) -> None:
    """Plain text on stdout, in UTF-8: a host agent copies `search` texts from feedback."""
    stream = sys.stdout
    if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower().replace("-", "") != "utf8":
        stream.reconfigure(encoding="utf-8", errors="replace")
    typer.echo(text)


def _awaiting(place: RunPlace, result: RunResult) -> list[str]:
    """What a host-agent run waits for, and the commands that answer it (D-44)."""
    waiting = result.waiting or {}
    run_id = result.run_id
    gate = waiting.get("gate")
    if gate == AGENT_GATE:
        rules = ", ".join(waiting.get("rules") or [])
        return [
            f"Unit {waiting.get('unit')} ({rules}) waits for your edits: attempt "
            f"{waiting.get('attempt')} of {waiting.get('attempts_allowed')}.",
            f"Read the context document {waiting.get('context_file')}, write the edits to "
            f"{waiting.get('edits_file')}, then run `codecoremorph unit submit`. To give the "
            'unit up, run `codecoremorph unit abort --reason "..."`; the run goes on with the '
            "next unit.",
        ]
    if gate == ApprovalGate.PLAN.value:
        lines = ["The plan waits for approval."]
        plan_path = place.state_dir.run(run_id).plan_json
        if plan_path.is_file():
            lines[0] = f"The plan waits for approval: {plan_path}"
            lines += _plan_lines(Plan.model_validate_json(plan_path.read_bytes()))
        lines.append(
            f"Approve it with `codecoremorph approve {run_id}` (add `--exclude U-0003,...` to "
            f"leave units out) or reject it with `codecoremorph reject {run_id}`, then run "
            f"`codecoremorph resume {run_id}`."
        )
        return lines
    if gate == ApprovalGate.UNIT.value:
        unit = waiting.get("unit")
        commit = str(waiting.get("commit") or "")[:12]
        lines = [f"Unit {unit} waits for approval (commit {commit}):"]
        lines += [f"  - {reason}" for reason in waiting.get("reasons") or []]
        lines.append(
            f"Keep it with `codecoremorph approve {run_id} --unit {unit}` or take it off the "
            f"branch with `codecoremorph reject {run_id} --unit {unit}`, then run "
            f"`codecoremorph resume {run_id}`."
        )
        return lines
    return [f"Run {run_id} is parked. Continue it with `codecoremorph resume {run_id}`."]


def finish_run(result: RunResult, as_json: bool, place: RunPlace) -> None:
    """Print a run command's result and exit with the run's exit code (SPEC §7.14)."""
    awaiting = _awaiting(place, result) if result.parked and result.awaiting else []
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "status": result.status.value,
                    "reason": result.reason,
                    "parked": result.parked,
                    "awaiting": result.awaiting,
                    "waiting": result.waiting,
                    "notes": list(result.notes),
                    "next": "\n".join(awaiting) or None,
                    "branch": result.branch,
                    "head": result.head,
                    "report": str(result.report_md) if result.report_md else None,
                    "exit_code": int(result.exit_code),
                },
                indent=2,
            )
        )
        raise typer.Exit(int(result.exit_code))
    for note in result.notes:
        _echo(note)
    if awaiting:
        _echo("\n".join(awaiting))
        raise typer.Exit(int(result.exit_code))
    _finish_text(result)
    raise typer.Exit(int(result.exit_code))


def _finish_text(result: RunResult) -> None:
    console = stdout()
    if result.parked:
        where = f" at the {result.waiting['gate']} gate" if result.waiting else ""
        console.print(
            Text.assemble(
                status_label("info"),
                f" run {result.run_id} is parked{where}. Continue it with "
                f"`codecoremorph resume {result.run_id}`, or end it with "
                f"`codecoremorph abort {result.run_id}`.",
            )
        )
        return
    label = status_label(
        "ok"
        if result.status is RunStatus.COMPLETED
        else "warn"
        if result.status is RunStatus.COMPLETED_WITH_FAILURES
        else "fail"
    )
    why = f" ({result.reason})" if result.reason else ""
    console.print(Text.assemble(label, f" run {result.run_id}: {result.status.value}{why}"))
    if result.head:
        console.print(Text(f"branch: {result.branch} at {result.head[:12]}"))
    if result.report_md:
        console.print(Text(f"report: {result.report_md}"))


# --------------------------------------------------------------------------- commands


@handle_errors
def run_command(
    config: Annotated[Path, typer.Argument(help="Migration manifest.")] = DEFAULT_CONFIG,
    base: Annotated[str | None, typer.Option("--base", help="Commit to start from.")] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Approve the plan without asking.")
    ] = False,
    sandbox: Annotated[
        SandboxMode | None, typer.Option("--sandbox", help="Override sandbox.mode.")
    ] = None,
    host_agent: Annotated[
        bool,
        typer.Option(
            "--host-agent",
            help="Your coding agent (Claude Code with the codecoremorph skill) writes the "
            "edits; no model is called. The run parks whenever it needs the agent or a "
            "decision.",
        ),
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Migrate the project: plan, then every unit on the run branch, verified and reported.

    Every commit on codecoremorph/<run-id> builds and passes the baseline's tests. Ctrl-C parks
    the run for `codecoremorph resume`. With --host-agent, each unit that needs edits waits for
    `codecoremorph unit submit` (D-44).
    """
    place = RunPlace.for_config(config)
    options = _options(place, yes=yes, host=host_agent)
    result = start_run(config, base=base, mode=sandbox, options=options, on_warning=_warn)
    finish_run(result, as_json, place)


@handle_errors
def resume_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Approve the plan without asking.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Continue a parked or crashed run from its last checkpoint (FR-14.3)."""
    place = RunPlace.for_config(config)
    result = resume_run(config, run_id, options=_options(place, yes=yes), on_warning=_warn)
    finish_run(result, as_json, place)


def _decide(
    decision: ApprovalDecision,
    run_id: str | None,
    config: Path,
    unit: str | None,
    exclude: str | None,
    reason: str | None,
) -> None:
    excluded = [e for e in (exclude or "").replace(" ", "").split(",") if e]
    outcome = record_decision(
        config, run_id, decision=decision, unit_id=unit, exclude=excluded, reason=reason
    )
    approval = outcome.approval
    what = f"unit {approval.unit_id}" if approval.unit_id else "the plan"
    verb = "approved" if decision is ApprovalDecision.APPROVE else "rejected"
    stdout().print(Text.assemble(status_label("ok"), f" {verb} {what} of run {approval.run_id}"))
    if approval.exclude:
        stdout().print(Text(f"excluded: {', '.join(approval.exclude)}"))
    if not outcome.engine_alive:
        stdout().print(
            Text(f"The run is parked: continue it with `codecoremorph resume {approval.run_id}`.")
        )


@handle_errors
def approve_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    unit: Annotated[str | None, typer.Option("--unit", help="Approve this unit's gate.")] = None,
    exclude: Annotated[
        str | None,
        typer.Option("--exclude", help="At the plan gate: units to leave out, e.g. U-0003,U-0007."),
    ] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Why, for the record.")] = None,
) -> None:
    """Approve the plan (optionally excluding units) or a unit waiting at its gate."""
    _decide(ApprovalDecision.APPROVE, run_id, config, unit, exclude, reason)


@handle_errors
def reject_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    unit: Annotated[str | None, typer.Option("--unit", help="Reject this unit's gate.")] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Why, for the record.")] = None,
) -> None:
    """Reject the plan (the run ends) or a unit (its commit is taken off the branch)."""
    _decide(ApprovalDecision.REJECT, run_id, config, unit, None, reason)


@handle_errors
def abort_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    reason: Annotated[str | None, typer.Option("--reason", help="Why, for the report.")] = None,
) -> None:
    """End a run: the unit in progress rolls back, the report is written (FR-14.4)."""
    outcome = abort_run(config, run_id, reason=reason)
    console = stdout()
    if outcome.engine_alive:
        console.print(
            Text(
                f"Run {outcome.run_id} stops at its next safe point and writes its report; "
                f"`codecoremorph status {outcome.run_id}` shows when it has."
            )
        )
        return
    console.print(Text.assemble(status_label("ok"), f" run {outcome.run_id} was aborted"))
    for note in outcome.notes:
        console.print(Text(f"note: {note}", style="yellow"))
    if outcome.branch_kept:
        console.print(Text("Its branch keeps the units committed before the abort."))
    if outcome.report_md:
        console.print(Text(f"report: {outcome.report_md}"))


@handle_errors
def status_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    every: Annotated[bool, typer.Option("--all", help="List every run.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """A run's progress: status, units, the gate it waits at, model usage (FR-11.3)."""
    place = RunPlace.for_config(config)
    with place.store() as store:
        if every:
            records = store.list_runs()
            if as_json:
                typer.echo(json.dumps([r.model_dump(mode="json") for r in records], indent=2))
                return
            table = Table(box=None, pad_edge=False)
            for column in ("run", "status", "updated", "base"):
                table.add_column(column)
            for r in records:
                table.add_row(
                    r.id,
                    r.status.value,
                    f"{r.updated_at:%Y-%m-%d %H:%M}",
                    (r.base_commit or "")[:12],
                )
            stdout().print(table)
            return
        record = place.record(store, run_id)
        view = overview(place, store, record)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "run": view.record.model_dump(mode="json"),
                    "units": view.unit_counts,
                    "current_unit": view.current,
                    "waiting": view.waiting,
                    "agent": view.agent,
                    "tokens": view.tokens,
                    "cost_usd": view.cost_usd,
                    "engine_running": view.engine_alive,
                    "report": str(view.report_md) if view.report_md else None,
                    "events": [e.model_dump(mode="json") for e in view.events],
                },
                indent=2,
            )
        )
        return
    console = stdout()
    r = view.record
    running = "the engine is running" if view.engine_alive else "no engine is running"
    console.print(Text(f"Run {r.id}: {r.status.value} ({running})", style="bold"))
    console.print(
        Text(f"base {(r.base_commit or '')[:12]}, updated {r.updated_at:%Y-%m-%d %H:%M:%S} UTC")
    )
    if view.unit_counts:
        console.print(Text("units: " + ", ".join(f"{n} {s}" for s, n in view.unit_counts.items())))
    if view.current:
        console.print(Text(f"in progress: {view.current}"))
    if view.agent and not view.engine_alive:
        agent = view.agent
        console.print(
            Text(
                f"waiting for the agent's edits: {agent['unit']}, attempt {agent['attempt']} of "
                f"{agent['attempts_allowed']} (context: {agent['context_file']}; then "
                "`codecoremorph unit submit`)",
                style="yellow",
            )
        )
    if view.waiting:
        console.print(
            Text(
                f"waiting for approval: {', '.join(view.waiting)} "
                f"(`codecoremorph approve {r.id}` or `reject`)",
                style="yellow",
            )
        )
    console.print(Text(f"model usage: {view.tokens:,} tokens, ${view.cost_usd:,.2f}"))
    for event in view.events:
        detail = ", ".join(f"{k}={v}" for k, v in event.data.items())
        unit = f" {event.unit_id}" if event.unit_id else ""
        console.print(Text(f"  {event.ts:%H:%M:%S} {event.type.value}{unit} {detail}", style="dim"))
    if view.report_md:
        console.print(Text(f"report: {view.report_md}"))
    elif not r.status.is_terminal and not view.engine_alive and not view.agent:
        console.print(Text(f"Continue it with `codecoremorph resume {r.id}`."))


@handle_errors
def report_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    fmt: Annotated[str, typer.Option("--format", help="md or json.")] = "md",
) -> None:
    """Print a run's report (FR-13.2). A run still in progress gets one built from its records."""
    if fmt not in ("md", "json"):
        raise UsageError("--format must be md or json")
    place = RunPlace.for_config(config)
    with place.store() as store:
        record = place.record(store, run_id)
        artifacts = place.state_dir.run(record.id)
        path = artifacts.report_json if fmt == "json" else artifacts.report_md
        if path.is_file():
            typer.echo(path.read_text(encoding="utf-8"), nl=False)
            return
        from codecoremorph.config import load_config
        from codecoremorph.core.ids import run_branch
        from codecoremorph.report.build import build_report

        report = build_report(
            store,
            artifacts,
            record.id,
            project=load_config(config).config.project.name,
            branch=run_branch(record.id),
            head=None,
            status=record.status,
            reason="in progress" if not record.status.is_terminal else None,
            finished_at=None,
        )
    typer.echo(
        report.model_dump_json(indent=2) if fmt == "json" else render_markdown(report), nl=False
    )


@handle_errors
def clean_command(
    run_id: RunArg = None,
    config: ConfigOpt = DEFAULT_CONFIG,
    every: Annotated[bool, typer.Option("--all", help="Every finished run.")] = False,
    branches: Annotated[
        bool, typer.Option("--branches", help="Also delete the runs' codecoremorph/* branches.")
    ] = False,
    older_than: Annotated[
        float | None, typer.Option("--older-than", help="Only runs older than this many days.")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Don't ask before deleting branches.")
    ] = False,
) -> None:
    """Remove finished runs' worktrees, build trees, caches and containers (FR-6.5).

    Branches go only with --branches, after you confirm. Active and parked runs are left alone.
    """
    place = RunPlace.for_config(config)
    if branches and not yes:
        if not _interactive():
            raise UsageError("deleting branches needs confirmation", hint="Add --yes.")
        if not typer.confirm("Delete the codecoremorph/* branches of the runs cleaned?"):
            branches = False
    outcome = clean_runs(
        place, run_id=run_id, every=every, branches=branches, older_than_days=older_than
    )
    console = stdout()
    for rid in outcome.cleaned:
        console.print(Text.assemble(status_label("ok"), f" cleaned {rid}"))
    for branch in outcome.branches_deleted:
        console.print(Text(f"deleted branch {branch}"))
    for rid, why in outcome.skipped.items():
        console.print(Text.assemble(status_label("info"), f" kept {rid}: {why}"))
    if not outcome.cleaned and not outcome.skipped:
        console.print(Text("nothing to clean"))


def unit_detail(place: RunPlace, run_id: str, unit_id: str) -> dict[str, Any]:
    """A unit's result, for scripts (used by the dashboard later)."""
    result = load_unit_result(place.state_dir.run(run_id), unit_id)
    return result.model_dump(mode="json") if result else {}


__all__ = [
    "RunReport",
    "abort_command",
    "approve_command",
    "clean_command",
    "finish_run",
    "host_options",
    "reject_command",
    "report_command",
    "resume_command",
    "run_command",
    "status_command",
]
