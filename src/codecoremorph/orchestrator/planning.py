"""``codecoremorph plan``: baseline, index, graph and planner, with no code changes (FR-5.7).

The baseline runs before indexing: configuring the project produces the compile commands the
indexer needs, and a project that doesn't build stops before the cost of indexing it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from codecoremorph.core.models import Baseline, LoadedRule, Plan, Role, RuleSite, SymbolIndex
from codecoremorph.llm.catalog import model_info
from codecoremorph.orchestrator.session import RunSession
from codecoremorph.parity.baseline import capture_baseline
from codecoremorph.planner.estimate import Estimator, estimate_duration_s, unit_token_limit
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import RuleScan, scan_rule
from codecoremorph.rules.applicability import StandardOrder, rule_excludes, standard_bump_rules

log = logging.getLogger(__name__)

StepSink = Callable[[str], None]
BASELINE_FILE = "baseline.json"


@dataclass(frozen=True)
class PlanOutcome:
    plan: Plan
    baseline: Baseline
    index: SymbolIndex
    duration_estimate_s: float
    plan_path: Path
    baseline_path: Path
    timings: dict[str, float] = field(default_factory=dict[str, float])


def migration_rules(session: RunSession, order: StandardOrder) -> list[tuple[LoadedRule, bool]]:
    """The standard bump (when the target is newer), then the manifest's rules, in order.
    Each comes with whether it runs automatically."""
    migration = session.config.migration
    source, target = migration.source.standard, migration.target.standard
    chosen: list[tuple[LoadedRule, bool]] = []
    bumps = standard_bump_rules(session.rules, migration.source.language)
    for rule_id in bumps:
        loaded = session.rules.get(rule_id)
        if rule_excludes(order, loaded.rule, source, target) is None:
            chosen.append((loaded, True))
    for rule_id in migration.target.rules:
        if rule_id not in bumps:  # a listed bump rule already runs (validate warns)
            chosen.append((session.rules.get(rule_id), False))
    return chosen


def _log_step(message: str) -> None:
    log.info("%s", message)


def _guidance_chars(loaded: LoadedRule) -> int:
    path = loaded.guidance_path()
    try:
        return len(path.read_text(encoding="utf-8")) if path is not None else 0
    except OSError:
        return 0


def _write_json(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes((text + "\n").encode("utf-8"))
    tmp.replace(path)


def make_plan(session: RunSession, *, on_step: StepSink | None = None) -> PlanOutcome:
    step = on_step or _log_step
    config = session.config
    migration = config.migration
    ctx = session.ctx
    adapter = session.adapter
    timings: dict[str, float] = {}

    started = time.monotonic()
    baseline = capture_baseline(
        adapter,
        ctx,
        base_commit=session.workspace.base_commit,
        runs=config.parity.baseline_runs,
        require_tests=config.guardrails.require_passing_tests,
        on_step=lambda name: step(f"baseline: {name}"),
    )
    timings["baseline_s"] = time.monotonic() - started
    baseline_path = session.artifacts.root / BASELINE_FILE
    _write_json(baseline_path, baseline.model_dump_json(indent=2))

    step("index")
    started = time.monotonic()
    index = adapter.index(ctx)
    timings["index_s"] = time.monotonic() - started

    step("graph")
    started = time.monotonic()
    project = ProjectModel.load(
        index.path,
        ctx.worktree,
        source_dirs=config.project.source_dirs,
        test_dirs=config.project.test_dirs,
    )
    timings["graph_s"] = time.monotonic() - started

    step("plan")
    started = time.monotonic()
    order = StandardOrder(migration.source.language, tuple(adapter.capabilities().standards))
    source, target = migration.source.standard, migration.target.standard

    def query(name: str, files: list[str]) -> list[RuleSite]:
        return adapter.query(ctx, name, files)

    scans: list[RuleScan] = [
        scan_rule(
            loaded,
            project,
            order=order,
            source=source,
            target=target,
            query=query,
            automatic=automatic,
        )
        for loaded, automatic in migration_rules(session, order)
    ]
    editor = model_info(config.llm.for_role(Role.EDITOR), config.llm)
    estimator = Estimator(
        prices=editor.prices,
        guidance_chars={scan.rule_id: _guidance_chars(scan.loaded) for scan in scans},
    )
    warnings: list[str] = []
    if index.problems:
        shown = "; ".join(index.problems[:3])
        more = f" (and {len(index.problems) - 3} more)" if len(index.problems) > 3 else ""
        warnings.append(f"the indexer reported {len(index.problems)} problems: {shown}{more}")
    plan = build_plan(
        project,
        scans,
        estimator=estimator,
        editor=editor,
        settings=PlannerSettings(token_limit=unit_token_limit(editor.context_window)),
        language=migration.source.language,
        source=source,
        target=target,
        base_commit=session.workspace.base_commit,
        warnings=warnings,
    )
    timings["plan_s"] = time.monotonic() - started
    plan_path = session.artifacts.plan_json
    _write_json(plan_path, plan.model_dump_json(indent=2))
    return PlanOutcome(
        plan=plan,
        baseline=baseline,
        index=index,
        duration_estimate_s=estimate_duration_s(plan, baseline),
        plan_path=plan_path,
        baseline_path=baseline_path,
        timings={k: round(v, 3) for k, v in timings.items()},
    )
