import { reportUrl, useReport } from "../api";
import { Commit, ErrorBox, Loading, StatusBadge } from "../components/ui";
import { formatDuration, formatNumber, formatTime, formatUsd, statusLabel } from "../format";
import { Link, paths } from "../router";
import type { RunDetail } from "../types";

const REVIEW_LABELS: Record<string, string> = {
  failed_unit: "Failed units",
  skipped_unit: "Units left out",
  api_change: "Interface changes",
  test_edit: "Test edits",
  agent_note: "Notes from the agent",
  warning: "Warnings",
  verification: "Final verification",
};

export function ReportPanel({ run }: { run: RunDetail }) {
  const report = useReport(run.id);
  if (report.isPending) return <Loading what="Loading the report" />;
  if (report.isError) return <ErrorBox error={report.error} title="Can't show the report" />;
  const r = report.data;
  const review = new Map<string, typeof r.review>();
  for (const item of r.review) review.set(item.kind, [...(review.get(item.kind) ?? []), item]);

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="row">
            <h2 style={{ margin: 0 }}>Report</h2>
            <StatusBadge status={r.status} />
            {r.reason ? <span className="muted">{r.reason}</span> : null}
          </div>
          <div className="row">
            <a href={reportUrl(run.id, "md")} download>
              Download Markdown
            </a>
            <a href={reportUrl(run.id, "json")} download>
              Download JSON
            </a>
          </div>
        </div>
        {!run.report_ready ? (
          <p className="muted">The run is still going: this report is built from what it has recorded so far.</p>
        ) : null}
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <tbody>
              <tr>
                <th>Project</th>
                <td>{r.project}</td>
              </tr>
              <tr>
                <th>Migration</th>
                <td>
                  {r.language} {r.source_standard} → {r.target_standard}
                </td>
              </tr>
              <tr>
                <th>Branch</th>
                <td className="mono">
                  {r.branch} {r.head ? <Commit commit={r.head} /> : null}
                </td>
              </tr>
              <tr>
                <th>Time</th>
                <td>
                  {formatTime(r.started_at)}
                  {r.finished_at ? ` to ${formatTime(r.finished_at)}` : ""}
                  {r.duration_s ? ` (${formatDuration(r.duration_s)})` : ""}
                </td>
              </tr>
              <tr>
                <th>Units</th>
                <td>
                  {Object.entries(r.unit_counts)
                    .map(([status, n]) => `${n} ${statusLabel(status)}`)
                    .join(", ") || "none"}
                </td>
              </tr>
              <tr>
                <th>Final verification</th>
                <td>
                  {r.final === null || !r.final.ran ? (
                    <span className="muted">not run{r.final?.reason ? `: ${r.final.reason}` : ""}</span>
                  ) : r.final.passed ? (
                    <span className="badge tone-ok">
                      passed: {r.final.tests_run} tests after a {r.final.clean ? "clean" : "incremental"} build
                    </span>
                  ) : (
                    <span className="badge tone-bad">failed{r.final.reason ? `: ${r.final.reason}` : ""}</span>
                  )}
                </td>
              </tr>
              <tr>
                <th>Model usage</th>
                <td>
                  {formatNumber(r.usage_total.calls)} calls, {formatNumber(r.usage_total.tokens)} tokens
                  {r.usage_total.cost_usd ? `, ${formatUsd(r.usage_total.cost_usd)}` : ""}
                  {r.providers.length ? ` · code was sent to ${r.providers.join(", ")}` : " · no code left the machine"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {r.review.length ? (
        <div className="card">
          <h2>Needs review</h2>
          {[...review.entries()].map(([kind, items]) => (
            <div key={kind} style={{ marginBottom: 10 }}>
              <h3>{REVIEW_LABELS[kind] ?? kind}</h3>
              <ul>
                {items.map((item, i) => (
                  <li key={i}>
                    {item.unit_id ? (
                      <>
                        <Link to={paths.unit(run.id, item.unit_id)} className="mono">
                          {item.unit_id}
                        </Link>
                        {": "}
                      </>
                    ) : null}
                    {item.text}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}

      <div className="card">
        <h2>Rules</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th className="num">Units</th>
                <th className="num">Committed</th>
                <th className="num">Planned sites</th>
                <th className="num">Converted</th>
              </tr>
            </thead>
            <tbody>
              {r.rules.map((rule) => (
                <tr key={rule.id}>
                  <td className="mono">{rule.id}</td>
                  <td className="num">{rule.units}</td>
                  <td className="num">{rule.committed}</td>
                  <td className="num">{rule.planned_sites}</td>
                  <td className="num">{rule.converted_sites}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Units</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Unit</th>
                <th>Status</th>
                <th>Commit</th>
                <th className="num">Attempts</th>
                <th className="num">Time</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {r.units.map((unit) => (
                <tr key={unit.unit_id}>
                  <td>
                    <Link to={paths.unit(run.id, unit.unit_id)} className="mono">
                      {unit.unit_id}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={unit.status} />
                  </td>
                  <td>
                    <Commit commit={unit.commit} />
                  </td>
                  <td className="num">{unit.attempts}</td>
                  <td className="num">{formatDuration(unit.duration_s)}</td>
                  <td>{unit.summary ?? unit.reason ?? ""}</td>
                </tr>
              ))}
              {Object.entries(r.unit_notes).map(([unitId, note]) => (
                <tr key={unitId}>
                  <td className="mono">{unitId}</td>
                  <td colSpan={5} className="muted">
                    {note}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {r.plan_warnings.length || r.excused_tests.length ? (
        <div className="card">
          {r.plan_warnings.length ? (
            <>
              <h2>Plan warnings</h2>
              <ul>
                {r.plan_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </>
          ) : null}
          {r.excused_tests.length ? (
            <>
              <h2>Tests excused from parity</h2>
              <p className="muted">They failed or were flaky before the run.</p>
              <ul className="mono">
                {r.excused_tests.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
