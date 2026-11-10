"""``codecoremorph plan``: validate, prepare the workspace, provision, baseline, index and plan,
without changing code (FR-5.7, SPEC §7.14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from codecoremorph.cli.output import handle_errors, status_label, stderr, stdout
from codecoremorph.config import DEFAULT_CONFIG_NAME
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.models import Plan, UnitStatus, WorkUnit
from codecoremorph.orchestrator.planning import PlanOutcome, make_plan
from codecoremorph.orchestrator.session import open_session


def _warn(message: str) -> None:
    stderr().print(Text(f"warning: {message}", style="yellow"))


def _step(message: str) -> None:
    stderr().print(Text(f"... {message}", style="dim"))


def _file_list(unit: WorkUnit, shown: int = 4) -> str:
    more = len(unit.files) - shown
    return ", ".join(unit.files[:shown]) + (f" (+{more} more)" if more > 0 else "")


def _unit_line(unit: WorkUnit) -> Text:
    sites = sum(unit.rule_sites.values())
    parts = [f"{len(unit.files)} files" if len(unit.files) != 1 else "1 file"]
    if sites:
        parts.append(f"{sites} site" + ("s" if sites != 1 else ""))
    if unit.estimate is not None:
        parts.append(f"~{unit.estimate.context_tokens:,} tokens")
        if unit.estimate.cost_usd is not None:
            parts.append(_money(unit.estimate.cost_usd))
    line = Text.assemble(
        (f"{unit.id}  ", "bold"),
        _status(unit),
        f"  {', '.join(unit.rules)}: {', '.join(parts)}",
    )
    if unit.flags:
        line.append(f"  [{', '.join(f.value for f in unit.flags)}]", style="cyan")
    return line


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def _status(unit: WorkUnit) -> Text:
    if unit.status is UnitStatus.PENDING:
        return Text("pending ")
    style = "yellow" if unit.status is UnitStatus.SKIPPED else "red"
    return Text(unit.status.value, style=style)


def render_plan(outcome: PlanOutcome, run_id: str, project: str) -> None:
    plan: Plan = outcome.plan
    baseline = outcome.baseline
    console = stdout()
    console.print(
        Text(
            f"Plan for {project}: {plan.migration} at {(plan.base_commit or '?')[:12]} "
            f"(run {run_id})",
            style="bold",
        )
    )
    tests = baseline.tests
    console.print(
        Text(
            f"baseline: {len(tests.passing)} tests pass in all {tests.runs} runs, "
            f"{len(tests.failing)} fail, {len(tests.flaky)} flaky, {len(tests.skipped)} skipped "
            f"(only passing tests count for parity); build {baseline.build_s:.1f} s, "
            f"tests {baseline.test_s:.1f} s"
        )
    )
    for test_id in tests.flaky[:5]:
        console.print(Text(f"  flaky: {test_id}", style="yellow"))
    if plan.graph is not None:
        g = plan.graph
        console.print(
            Text(
                f"graph: {g.files} files, {g.edges} edges, {g.clusters} clusters "
                f"({g.cyclic_clusters} from cycles; the largest has {g.largest_cluster} files)"
            )
        )

    rules = Table(title="Rules, in order", title_justify="left", box=None, pad_edge=False)
    for column in ("rule", "kind", "sites", "files", "skipped", "units"):
        rules.add_column(
            column,
            justify="left" if column in ("rule", "kind") else "right",
            overflow="fold",
        )
    for rule in plan.rules:
        name = rule.id + (" (automatic)" if rule.automatic else "")
        rules.add_row(
            Text(name),
            rule.kind.value,
            "-" if rule.sites is None else str(rule.sites),
            str(rule.files),
            str(rule.skipped_files),
            str(rule.units),
        )
    console.print(rules)

    console.print(Text("Work units, in order", style="italic"))
    for unit in plan.units:
        console.print(_unit_line(unit))
        console.print(Text(f"        {_file_list(unit)}", style="dim"))
        if unit.note:
            console.print(Text(f"        {unit.note}", style="dim"))

    if plan.dependencies:
        # What the source depends on outside its own code, before any unit runs (D-82).
        deps = Table(
            title="What the port depends on", title_justify="left", box=None, pad_edge=False
        )
        for column in ("dependency", "kind", "carried over", "what the port does"):
            deps.add_column(column, overflow="fold")
        for dependency in plan.dependencies:
            name = dependency.name + (f" {dependency.version}" if dependency.version else "")
            deps.add_row(
                Text(name),
                dependency.kind.value.replace("_", " "),
                Text(
                    "yes" if dependency.ported else "no",
                    style="green" if dependency.ported else "yellow",
                ),
                Text(dependency.summary),
            )
        console.print(deps)

    counts = {s: len(plan.units_with_status(s)) for s in UnitStatus}
    summary = (
        f"{counts[UnitStatus.PENDING]} units to run, {counts[UnitStatus.TOO_LARGE]} too large, "
        f"{counts[UnitStatus.SKIPPED]} skipped"
    )
    if plan.estimate is not None:
        e = plan.estimate
        cost = "no cost (local model)" if e.cost_usd is None else f"about {_money(e.cost_usd)}"
        summary += (
            f"; about {e.tokens:,} tokens, {cost} with {e.model}, and "
            f"{outcome.duration_estimate_s / 60:.0f} min"
        )
    console.print(Text.assemble(status_label("ok"), f" {summary}"))
    for warning in plan.warnings:
        console.print(Text(f"warning: {warning}", style="yellow"))
    console.print(Text(f"plan: {outcome.plan_path}"))


@handle_errors
def plan_command(
    config: Annotated[Path, typer.Argument(help="Migration manifest.")] = Path(DEFAULT_CONFIG_NAME),
    base: Annotated[str | None, typer.Option("--base", help="Commit to start from.")] = None,
    sandbox: Annotated[
        SandboxMode | None, typer.Option("--sandbox", help="Override sandbox.mode.")
    ] = None,
    keep: Annotated[
        bool, typer.Option("--keep", help="Keep the run's build directory and caches afterwards.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the plan as JSON.")] = False,
) -> None:
    """Plan a migration without changing code: baseline, index, dependency graph, work units.

    Writes plan.json and baseline.json under .codecoremorph/runs/<run-id>/.
    """
    with open_session(
        config,
        base=base,
        mode=sandbox,
        keep_build=keep,
        delete_branch=True,
        on_warning=_warn,
    ) as session:
        _step(f"run {session.run_id} ({session.executor.kind} mode)")
        outcome = make_plan(session, on_step=_step)
        if as_json:
            payload = {
                "run_id": session.run_id,
                "plan_path": str(outcome.plan_path),
                "baseline_path": str(outcome.baseline_path),
                "duration_estimate_s": outcome.duration_estimate_s,
                "timings": outcome.timings,
                "plan": outcome.plan.model_dump(mode="json"),
                "baseline": outcome.baseline.model_dump(mode="json"),
            }
            typer.echo(json.dumps(payload, indent=2))
        else:
            render_plan(outcome, session.run_id, session.config.project.name)
