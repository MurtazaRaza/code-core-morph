"""The Markdown report (FR-13.2)."""

from __future__ import annotations

from codecoremorph.core.models import ReviewKind, RunReport, RunStatus

_HEADLINES = {
    RunStatus.COMPLETED: "completed: every unit committed",
    RunStatus.COMPLETED_WITH_FAILURES: "completed, with units that failed or were left out",
    RunStatus.BUDGET_EXHAUSTED: "stopped: a budget ran out",
    RunStatus.ABORTED: "aborted",
    RunStatus.REJECTED: "rejected at a gate",
    RunStatus.FAILED: "failed",
}

_REVIEW_TITLES = {
    ReviewKind.VERIFICATION: "Final verification",
    ReviewKind.FAILED_UNIT: "Failed units",
    ReviewKind.API_CHANGE: "Interface changes",
    ReviewKind.TEST_EDIT: "Test files changed",
    ReviewKind.SKIPPED_UNIT: "Units left out",
    ReviewKind.AGENT_NOTE: "The agent's notes",
    ReviewKind.WARNING: "Warnings",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _money(value: float) -> str:
    return f"${value:,.2f}"


def render_markdown(report: RunReport) -> str:
    lines: list[str] = []
    out = lines.append
    out(f"# CodeCoreMorph run {report.run_id}: {report.project}")
    out("")
    headline = _HEADLINES.get(report.status, report.status.value)
    out(f"**Status:** {headline}" + (f" ({report.reason})" if report.reason else ""))
    out("")
    target = f"{report.target_language} " if report.target_language else ""
    out(
        f"- Migration: {report.language} {report.source_standard} -> {target}"
        f"{report.target_standard}"
    )
    out(f"- Base commit: `{report.base_commit[:12]}`")
    head = f" (last commit `{report.head[:12]}`)" if report.head else ""
    out(f"- Branch: `{report.branch}`{head}")
    if report.duration_s is not None:
        out(f"- Duration: {report.duration_s / 60:.1f} min")
    counts = ", ".join(f"{n} {status}" for status, n in report.unit_counts.items())
    out(f"- Units: {counts or 'none'}")
    out("")

    out("## Units")
    out("")
    if report.units or report.unit_notes:
        out("| Unit | Status | Attempts | Commit | Files | Summary |")
        out("|---|---|---|---|---|---|")
        for unit in report.units:
            summary = unit.summary if unit.status.value == "committed" else unit.reason
            if unit.agent_skipped:
                summary = "Deterministic tools completed the unit."
            out(
                f"| {unit.unit_id} | {unit.status.value} | {unit.attempts} | "
                f"{('`' + unit.commit[:12] + '`') if unit.commit else '-'} | "
                f"{len(unit.changed_files)} | {_cell(summary or '')} |"
            )
        for unit_id, note in report.unit_notes.items():
            out(f"| {unit_id} | - | 0 | - | 0 | {_cell(note)} |")
    else:
        out("No unit ran.")
    out("")

    if report.rules:
        out("## Rules")
        out("")
        out("| Rule | Units | Committed | Planned sites | Converted |")
        out("|---|---|---|---|---|")
        for rule in report.rules:
            out(
                f"| {rule.id} | {rule.units} | {rule.committed} | {rule.planned_sites} | "
                f"{rule.converted_sites} |"
            )
        out("")

    out("## Parity")
    out("")
    if report.baseline_tests:
        t = report.baseline_tests
        out(
            f"Baseline: {t.get('passing', 0)} tests pass, {t.get('failing', 0)} fail, "
            f"{t.get('flaky', 0)} are flaky and {t.get('skipped', 0)} are skipped. Only passing "
            "tests count for parity."
        )
    if report.excused_tests:
        excused = report.excused_tests
        more = f" and {len(excused) - 20} more" if len(excused) > 20 else ""
        out("")
        out(
            "Not checked, because they failed or were flaky before the run: "
            + ", ".join(f"`{t}`" for t in excused[:20])
            + more
            + "."
        )
    final = report.final
    if final is None or not final.ran:
        out("")
        out(
            "The final verification didn't run"
            + (f": {final.reason}" if final and final.reason else ".")
        )
    else:
        kind = "a clean build" if final.clean else "an incremental build"
        verdict = "passed" if final.passed else "FAILED"
        out("")
        out(
            f"Final verification ({kind} of `{(final.commit or '')[:12]}`, "
            f"{final.tests_run} tests): **{verdict}**."
        )
        for error in final.build_errors[:10]:
            out(f"- build error: {error}")
        for test_id in final.regressions[:20]:
            out(f"- regression: `{test_id}`")
    out("")

    port = report.port
    if port is not None:
        out("## Port")
        out("")
        out(
            f"The symbol map has {port.symbols} entries; the test map pairs {port.tests_mapped} "
            f"tests. Unsafe code: {len(port.unsafe) or 'none'}"
            + (f" ({', '.join(f'`{u}`' for u in port.unsafe[:20])})" if port.unsafe else "")
            + "."
        )
        sections = [
            ("Tests without a counterpart", port.tests_without_counterpart),
            ("Behavior the port couldn't keep, or kept differently", port.deviations),
            ("Source code left", port.source_left),
        ]
        for title, items in sections:
            if items:
                out("")
                out(f"{title}:")
                for item in items[:50]:
                    out(f"- {_cell(item)}")
                if len(items) > 50:
                    out(f"- ... and {len(items) - 50} more")
        out("")

    summary = report.characterization
    if summary is not None:
        out("## Characterization tests")
        out("")
        if not summary.ran:
            out(f"Characterization didn't run: {summary.reason or 'nothing to do'}.")
        else:
            where = f" in commit `{summary.commit[:12]}`" if summary.commit else ""
            removed = ", and removed again at the end (keep: false)" if summary.removed else ""
            out(
                f"{len(summary.tests)} test(s) pin down untested code before the migration"
                f"{where}{removed}. {summary.untested_symbols} symbol(s) had no test "
                f"(scope: {summary.scope})."
            )
            out("")
            out("| Job | Unit | File | Status | Accepted | Dropped | Attempts |")
            out("|---|---|---|---|---|---|---|")
            for job in summary.jobs:
                out(
                    f"| {job.job_id} | {job.plan_unit} | `{job.test_file}` | {job.status.value} | "
                    f"{len(job.tests)} | {len(job.rejected)} | {job.attempts} |"
                )
            dropped = [(job, r) for job in summary.jobs for r in job.rejected]
            if dropped:
                out("")
                out("Dropped:")
                for job, rejected in dropped[:30]:
                    out(f"- `{rejected.test}` ({job.job_id}): {_cell(rejected.reason)}")
            reasons = [j for j in summary.jobs if j.reason and not j.tests]
            for job in reasons:
                out(f"- {job.job_id} kept nothing: {_cell(job.reason or '')}")
        out("")

    out("## Model usage")
    out("")
    if report.usage_total.calls:
        out("| Role | Calls | Tokens | Cost | Model time |")
        out("|---|---|---|---|---|")
        for role, usage in report.usage_by_role.items():
            out(
                f"| {role} | {usage.calls} | {usage.tokens:,} | {_money(usage.cost_usd)} | "
                f"{usage.latency_s:.0f} s |"
            )
        total = report.usage_total
        out(
            f"| total | {total.calls} | {total.tokens:,} | {_money(total.cost_usd)} | "
            f"{total.latency_s:.0f} s |"
        )
        out("")
        out(f"Code was sent to: {', '.join(report.providers)}.")
    else:
        out("CodeCoreMorph made no model calls: no source code left this machine through it.")
    out("")

    out("## Review")
    out("")
    if not report.review and not report.plan_warnings:
        out("Nothing needs review.")
    for kind, title in _REVIEW_TITLES.items():
        items = [r for r in report.review if r.kind is kind]
        if not items:
            continue
        out(f"### {title}")
        out("")
        for item in items:
            prefix = f"{item.unit_id}: " if item.unit_id else ""
            out(f"- {prefix}{item.text}")
        out("")
    if report.plan_warnings:
        out("### Plan warnings")
        out("")
        for warning in report.plan_warnings:
            out(f"- {warning}")
        out("")

    if report.environment:
        out("## Environment")
        out("")
        for key, value in sorted(report.environment.items()):
            out(f"- {key}: `{value}`")
        out("")
    return "\n".join(lines).rstrip() + "\n"
