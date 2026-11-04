import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, keys, useRun } from "../api";
import { DecisionNote } from "../components/DecisionForm";
import { ErrorBox, Loading, Meter, Stat, StatusBadge } from "../components/ui";
import { useRunEvents } from "../events";
import {
  describeEvent,
  formatClock,
  formatNumber,
  formatTime,
  formatUsd,
  shortCommit,
  statusLabel,
  timeAgo,
} from "../format";
import { Link, paths, type RunTab } from "../router";
import type { Theme } from "../theme";
import { type RunDetail, TERMINAL_RUN_STATUSES } from "../types";
import { GraphPanel } from "./GraphPanel";
import { PlanPanel } from "./PlanPanel";
import { ReportPanel } from "./ReportPanel";

const TAB_LABELS: Record<RunTab, string> = {
  overview: "Overview",
  plan: "Plan",
  graph: "Dependency graph",
  report: "Report",
};

export function RunPage({ runId, tab, theme }: { runId: string; tab: RunTab; theme: Theme }) {
  const run = useRun(runId, 10_000);
  const terminal = run.data ? TERMINAL_RUN_STATUSES.has(run.data.status) : false;
  const live = useRunEvents(runId, run.data?.last_event_id, Boolean(run.data) && !terminal);

  if (run.isPending) return <Loading what="Loading the run" />;
  if (run.isError) return <ErrorBox error={run.error} title={`Can't show run ${runId}`} />;
  const detail = run.data;

  return (
    <>
      <div className="crumbs">
        <Link to={paths.runs()}>Runs</Link> / <span className="mono">{runId}</span>
      </div>
      <div className="page-head">
        <h1 className="mono">{detail.id}</h1>
        <StatusBadge status={detail.status} />
        <span className="row meta" title={live ? "live updates" : "updates every 10 s"}>
          <span className={detail.engine_alive ? "dot live" : "dot"} />
          {detail.engine_alive ? "engine running" : terminal ? "finished" : "no engine running"}
          {!terminal && live ? " · live" : null}
        </span>
      </div>
      <nav className="tabs" aria-label="Run views">
        {(Object.keys(TAB_LABELS) as RunTab[]).map((id) => (
          <Link key={id} to={paths.run(runId, id)} className={id === tab ? "active" : undefined}>
            {TAB_LABELS[id]}
          </Link>
        ))}
      </nav>
      {tab === "overview" ? <Overview run={detail} /> : null}
      {tab === "plan" ? <PlanPanel run={detail} /> : null}
      {tab === "graph" ? <GraphPanel run={detail} theme={theme} /> : null}
      {tab === "report" ? <ReportPanel run={detail} /> : null}
    </>
  );
}

function Waiting({ run }: { run: RunDetail }) {
  const terminal = TERMINAL_RUN_STATUSES.has(run.status);
  const agent = run.agent_waiting;
  const resume = !run.engine_alive && !terminal;
  return (
    <>
      {run.waiting.includes("plan") ? (
        <div className="callout wait">
          <strong>The plan waits for your approval.</strong>
          <p>
            Review its units, then approve or reject it on the{" "}
            <Link to={paths.run(run.id, "plan")}>Plan</Link> tab.
          </p>
        </div>
      ) : null}
      {run.waiting
        .filter((w) => w !== "plan")
        .map((unitId) => (
          <div className="callout wait" key={unitId}>
            <strong>Unit {unitId} waits for your approval.</strong>
            <p>
              Its commit is on the branch; keep it or roll it back from the{" "}
              <Link to={paths.unit(run.id, unitId)}>unit&apos;s page</Link>.
            </p>
          </div>
        ))}
      {agent ? (
        <div className="callout">
          <strong>
            {agent.kind === "characterize" ? "Characterization job" : "Unit"} {String(agent.unit)}{" "}
            waits for the coding agent&apos;s {agent.kind === "characterize" ? "tests" : "edits"}
          </strong>
          <p>
            Attempt {String(agent.attempt)} of {String(agent.attempts_allowed)}. The agent answers with{" "}
            <code>codecoremorph unit submit</code> or <code>unit abort</code>.
          </p>
        </div>
      ) : null}
      {resume && (run.waiting.length > 0 || agent) ? <DecisionNote runId={run.id} /> : null}
    </>
  );
}

function Overview({ run }: { run: RunDetail }) {
  const terminal = TERMINAL_RUN_STATUSES.has(run.status);
  const counts = Object.entries(run.unit_counts);
  const events = [...run.events].reverse();
  return (
    <>
      <Waiting run={run} />
      <div className="grid">
        <div className="card">
          <Stat
            label="Units done"
            value={run.units_total ? `${run.units_done} / ${run.units_total}` : "not planned yet"}
          >
            {run.units_total ? (
              <Meter value={run.units_done} max={run.units_total} label="units done" progress />
            ) : null}
          </Stat>
          <div className="row" style={{ marginTop: 10 }}>
            {counts.map(([status, n]) => (
              <span key={status} className="chip">
                {n} {statusLabel(status)}
              </span>
            ))}
          </div>
        </div>
        <div className="card">
          <Stat
            label="Model tokens"
            value={formatNumber(run.budget.tokens)}
          >
            {run.budget.max_tokens ? (
              <>
                <Meter value={run.budget.tokens} max={run.budget.max_tokens} label="tokens used" />
                <span className="muted">of {formatNumber(run.budget.max_tokens)}</span>
              </>
            ) : (
              <span className="muted">
                {run.agent === "host" ? "a coding agent makes the edits" : "no token cap"}
              </span>
            )}
          </Stat>
        </div>
        <div className="card">
          <Stat label="Cost" value={formatUsd(run.budget.cost_usd)}>
            {run.budget.max_cost_usd ? (
              <>
                <Meter value={run.budget.cost_usd} max={run.budget.max_cost_usd} label="cost" />
                <span className="muted">of {formatUsd(run.budget.max_cost_usd)} allowed</span>
              </>
            ) : (
              <span className="muted">no cost cap</span>
            )}
          </Stat>
        </div>
        <div className="card">
          <Stat
            label={run.engine_alive ? "Now" : "Last step"}
            value={<span style={{ fontSize: 15 }}>{run.activity?.message ?? "—"}</span>}
          >
            {run.activity ? (
              <span className="muted" title={formatTime(run.activity.ts)}>
                {timeAgo(run.activity.ts)}
                {run.current_unit ? (
                  <>
                    {" · "}
                    <Link to={paths.unit(run.id, run.current_unit)}>{run.current_unit}</Link>
                  </>
                ) : null}
              </span>
            ) : null}
          </Stat>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Run</h2>
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <th>Migration</th>
                <td>
                  {run.language ?? "—"} {run.source_standard ?? ""} → {run.target_standard ?? ""}
                </td>
              </tr>
              <tr>
                <th>Branch</th>
                <td className="mono">{run.branch}</td>
              </tr>
              <tr>
                <th>Base commit</th>
                <td className="mono" title={run.base_commit ?? ""}>
                  {shortCommit(run.base_commit)}
                </td>
              </tr>
              <tr>
                <th>Editor</th>
                <td>{run.agent === "host" ? "coding agent (host)" : "CodeCoreMorph's model loop"}</td>
              </tr>
              <tr>
                <th>Sandbox</th>
                <td>{run.sandbox ?? "—"}</td>
              </tr>
              <tr>
                <th>Started</th>
                <td>{formatTime(run.created_at)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Activity</h2>
        {events.length === 0 ? (
          <p className="muted">No events yet.</p>
        ) : (
          <ul className="events" aria-label="Latest events">
            {events.map((event) => (
              <li key={event.id}>
                <span className="muted" title={formatTime(event.ts)}>
                  {formatClock(event.ts)}
                </span>
                <span className="type">{event.type}</span>
                <span className="unit">
                  {event.unit_id ? (
                    <Link to={paths.unit(run.id, event.unit_id)}>{event.unit_id}</Link>
                  ) : null}
                </span>
                <span>{describeEvent(event.type, event.data)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {!terminal ? <AbortCard runId={run.id} /> : null}
    </>
  );
}

function AbortCard({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const abort = useMutation({
    mutationFn: () => api.abort(runId, reason.trim() || null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
      void queryClient.invalidateQueries({ queryKey: keys.runs });
      setAsking(false);
    },
  });
  return (
    <div className="card">
      <h2>Stop the run</h2>
      {abort.data ? <div className="callout ok">{abort.data.message}</div> : null}
      {abort.error ? <ErrorBox error={abort.error} title="The run wasn't aborted" /> : null}
      {asking ? (
        <div className="gate-form">
          <p>
            Aborting ends the run: the unit in progress rolls back, the branch keeps the units
            already committed, and the report is written.
          </p>
          <textarea
            aria-label="Why the run is aborted"
            placeholder="Reason (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="row">
            <button className="danger" disabled={abort.isPending} onClick={() => abort.mutate()}>
              Abort run {runId}
            </button>
            <button onClick={() => setAsking(false)} disabled={abort.isPending}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button className="danger" onClick={() => setAsking(true)}>
          Abort…
        </button>
      )}
    </div>
  );
}
