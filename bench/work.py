"""How long a benchmark run took, against how much work its plan held (M1, NFR-7.2).

Usage: uv run python bench/work.py RESULTS             add or refresh RESULTS' "work" section
       uv run python bench/work.py --run RUN_ID [--config MANIFEST] [--project DIR]   print it

bench/run.py records this as the "work" section of its results. The plan's side comes from the
run's plan.json and baseline.json: units, rule sites, the files the units own and their lines,
the expected agent attempts and the planner's duration estimate. The run's side comes from its
events (how long each phase, unit and agent wait took) and its branch (lines changed). Agent
time runs from a unit's agent wait to the agent's `unit submit|abort`; tool time is the rest of
the migration: codemods, builds, tests and checks.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    Baseline,
    Event,
    EventType,
    Plan,
    RunReport,
    UnitStatus,
    owned_files,
)
from codecoremorph.core.paths import codecoremorph_home
from codecoremorph.orchestrator.lifecycle import RunPlace
from codecoremorph.planner.estimate import LLM_SECONDS_PER_ATTEMPT, estimate_duration_s

BENCH = Path(__file__).resolve().parent
# As bench/measure.py counts lines of code.
CODE_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp", ".rs")
NOT_PLANNED = (UnitStatus.SKIPPED, UnitStatus.TOO_LARGE)
AGENT_GATE = "agent"


def _git(root: Path, *argv: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, input=stdin, capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout  # fmt: skip


def _numstat(root: Path, old: str, new: str) -> dict[str, tuple[int, int]]:
    """Lines added and deleted per text file between two trees."""
    stats: dict[str, tuple[int, int]] = {}
    for record in _git(root, "diff", "--numstat", "-z", "--no-renames", old, new).split("\0"):
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        if added != "-":  # binary
            stats[path] = (int(added), int(deleted))
    return stats


def code_lines(root: Path, commit: str, exclude: list[str]) -> dict[str, int]:
    """Lines of each code file at ``commit``: its diff against the empty tree."""
    empty = _git(root, "hash-object", "-t", "tree", "--stdin", stdin="").strip()
    matcher = PathMatcher(exclude)
    return {
        path: added
        for path, (added, _) in _numstat(root, empty, commit).items()
        if path.endswith(CODE_SUFFIXES) and not matcher.matches(path)
    }


@dataclass
class UnitTime:
    start: datetime | None = None
    end: datetime | None = None
    status: str | None = None
    agent_s: float = 0.0
    agent_waits: int = 0
    gate_s: float = 0.0

    @property
    def span_s(self) -> float:
        return (self.end - self.start).total_seconds() if self.start and self.end else 0.0


@dataclass
class Timeline:
    start: datetime | None = None
    end: datetime | None = None
    phases: dict[str, float] = field(default_factory=dict[str, float])  # seconds per run status
    units: dict[str, UnitTime] = field(default_factory=dict[str, UnitTime])

    @property
    def run_s(self) -> float:
        return (self.end - self.start).total_seconds() if self.start and self.end else 0.0


def timeline(events: list[Event]) -> Timeline:
    """Seconds per run status, and each unit's span, agent waits and gate waits.

    A wait still open when its unit ends (the run was aborted while it waited) ends there;
    one still open when the events end, at the last event."""
    t = Timeline()
    status: tuple[str, datetime] | None = None
    waiting: dict[tuple[str, str | None], datetime] = {}

    def close(key: tuple[str, str | None], at: datetime) -> None:
        opened = waiting.pop(key, None)
        if opened is None:
            return
        gate, unit_id = key
        if unit_id is None:  # the plan gate: its time is the awaiting_plan_approval phase
            return
        unit = t.units.setdefault(unit_id, UnitTime())
        seconds = (at - opened).total_seconds()
        if gate == AGENT_GATE:
            unit.agent_s += seconds
        else:
            unit.gate_s += seconds

    for e in events:
        t.start = t.start or e.ts
        t.end = e.ts
        gate = str(e.data.get("gate", ""))
        if e.type is EventType.RUN_STATUS:
            if status is not None:
                seconds = (e.ts - status[1]).total_seconds()
                t.phases[status[0]] = t.phases.get(status[0], 0.0) + seconds
            status = (str(e.data["status"]), e.ts)
        elif e.type is EventType.UNIT_STATUS and e.unit_id is not None:
            unit = t.units.setdefault(e.unit_id, UnitTime())
            value = str(e.data["status"])
            if value == UnitStatus.IN_PROGRESS.value:
                unit.start = unit.start or e.ts
            else:
                for key in [k for k in waiting if k[1] == e.unit_id]:
                    close(key, e.ts)
                unit.end, unit.status = e.ts, value
        elif e.type is EventType.GATE_WAITING:
            key = (gate, e.unit_id)
            if key not in waiting:  # a unit that starts over waits on from its first wait
                waiting[key] = e.ts
                if gate == AGENT_GATE and e.unit_id is not None:
                    t.units.setdefault(e.unit_id, UnitTime()).agent_waits += 1
        elif e.type is EventType.GATE_DECIDED:
            close((gate, e.unit_id), e.ts)
    if t.end is not None:
        for key in list(waiting):
            close(key, t.end)
    return t


def _ratio(a: float, b: float, digits: int = 2) -> float | None:
    return round(a / b, digits) if b else None


def measure(place: RunPlace, run_id: str, root: Path, exclude: list[str]) -> dict[str, Any]:
    """The run's work: what its plan held, what it did, and how long that took."""
    artifacts = place.state_dir.run(run_id)
    plan = Plan.model_validate_json(artifacts.plan_json.read_bytes())
    baseline = Baseline.model_validate_json((artifacts.root / "baseline.json").read_bytes())
    report = (
        RunReport.model_validate_json(artifacts.report_json.read_bytes())
        if artifacts.report_json.is_file()
        else None
    )
    planned = [u for u in plan.units if u.status not in NOT_PLANNED]
    with place.store() as store:
        events = store.list_events(run_id)
        # The store has every attempt, also those of a unit the run ended in the middle of.
        attempts = {u.id: len(store.attempts(run_id, u.id)) for u in planned}
    base = plan.base_commit or (report.base_commit if report else None)
    if base is None:
        raise SystemExit(f"run {run_id} has no base commit in its plan or report")
    lines = code_lines(root, base, exclude)
    t = timeline(events)
    results = {u.unit_id: u for u in report.units} if report else {}

    verify_s = baseline.build_s + baseline.test_s
    unit_rows: list[dict[str, Any]] = []
    for u in planned:
        files = owned_files(u)
        result = results.get(u.id)
        timing = t.units.get(u.id, UnitTime())
        changed = (
            _numstat(root, f"{result.commit}^", result.commit)
            if result is not None and result.commit
            else {}
        )
        unit_rows.append(
            {
                "id": u.id,
                "rules": u.rules,
                "files": len(files),
                "lines": sum(lines.get(f, 0) for f in files),
                "sites": sum(u.rule_sites.values()),
                "expected_attempts": u.estimate.expected_attempts if u.estimate else None,
                "estimated_s": estimate_duration_s(
                    plan.model_copy(update={"units": [u]}), baseline
                ),
                "status": timing.status or (result.status.value if result else u.status.value),
                "attempts": max(attempts[u.id], result.attempts if result else 0),
                "agent_waits": timing.agent_waits,
                "actual_s": round(timing.span_s, 1),
                "agent_s": round(timing.agent_s, 1),
                "gate_s": round(timing.gate_s, 1),
                "tools_s": round(timing.span_s - timing.agent_s - timing.gate_s, 1),
                "lines_changed": sum(a + d for a, d in changed.values()),
            }
        )

    unit_files = {f for u in planned for f in owned_files(u)}
    sites = {r.id: r.sites for r in plan.rules if r.sites}
    estimated_s = estimate_duration_s(plan, baseline)
    expected_attempts = sum(u.estimate.expected_attempts for u in planned if u.estimate)
    head = report.head if report and report.head else f"codecoremorph/{run_id}"
    branch = _numstat(root, base, head)
    added = sum(a for a, _ in branch.values())
    deleted = sum(d for _, d in branch.values())
    agent_rows = [r for r in unit_rows if r["agent_waits"]]
    agent_attempts = sum(r["attempts"] for r in agent_rows)
    agent_s = sum(r["agent_s"] for r in unit_rows)
    gate_s = sum(r["gate_s"] for r in unit_rows)
    migrating_s = t.phases.get("migrating", 0.0)
    spans = [r["actual_s"] for r in unit_rows if r["actual_s"]]
    project_lines = sum(lines.values())
    return {
        "planned": {
            "project_files": len(lines),
            "project_lines": project_lines,
            "units": len(planned),
            "rule_sites": sites,
            "sites": sum(sites.values()),
            "files": len(unit_files),
            "lines": sum(lines.get(f, 0) for f in unit_files),
            "expected_agent_attempts": round(expected_attempts, 2),
            "verify_s": round(verify_s, 1),
            "estimated_s": estimated_s,
            "planner_llm_s_per_attempt": LLM_SECONDS_PER_ATTEMPT,
        },
        "done": {
            "units": dict(Counter(r["status"] for r in unit_rows)),
            "agent_units": len(agent_rows),
            "agent_attempts": agent_attempts,
            "changed_files": len(branch),
            "lines_added": added,
            "lines_deleted": deleted,
        },
        "time": {
            "run_s": round(t.run_s, 1),
            "phases_s": {k: round(v, 1) for k, v in t.phases.items()},
            "migrating_s": round(migrating_s, 1),
            "agent_s": round(agent_s, 1),
            "unit_gates_s": round(gate_s, 1),
            "tools_s": round(migrating_s - agent_s - gate_s, 1),
            "unit_s": {
                "mean": round(statistics.mean(spans), 1),
                "median": round(statistics.median(spans), 1),
                "max": max(spans),
            }
            if spans
            else None,
        },
        "compared": {
            "migrating_over_estimate": _ratio(migrating_s, estimated_s),
            "agent_attempts_over_expected": _ratio(agent_attempts, expected_attempts),
            "agent_s_per_attempt": _ratio(agent_s, agent_attempts, 1),
            "migrating_s_per_unit": _ratio(migrating_s, len(planned), 1),
            "migrating_s_per_site": _ratio(migrating_s, sum(sites.values()), 1),
            "migrating_s_per_changed_line": _ratio(migrating_s, added + deleted),
            "run_min_per_1k_project_lines": _ratio(t.run_s / 60, project_lines / 1000),
        },
        "units": unit_rows,
    }


def main(argv: list[str]) -> int:
    pin = json.loads((BENCH / "pin.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Measure a benchmark run's work and time.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("results", nargs="?", type=Path, help="a bench/run.py results file")
    which.add_argument("--run", help="print the measure of this run instead")
    parser.add_argument(
        "--project", type=Path, default=codecoremorph_home() / "bench" / pin["project"]
    )
    parser.add_argument("--config", type=Path, help="the run's manifest (default: bench/run.py's)")
    args = parser.parse_args(argv)
    root: Path = args.project.resolve()
    config: Path = args.config or root.parent / f"{pin['project']}-run.yaml"
    place = RunPlace.for_config(config)
    exclude = list(pin["exclude"]) if args.config is None else []
    if args.run:
        print(json.dumps(measure(place, args.run, root, exclude), indent=2))
        return 0
    record: dict[str, Any] = json.loads(args.results.read_text(encoding="utf-8"))
    run_id = record.get("run_id")
    if not run_id:
        print(f"{args.results} names no run", file=sys.stderr)
        return 1
    record["work"] = measure(place, run_id, root, exclude)
    args.results.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record["work"].items() if k != "units"}, indent=2))
    print(f"\nupdated {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
