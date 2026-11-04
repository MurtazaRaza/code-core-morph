import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, keys, usePlan } from "../api";
import { DecisionForm, DecisionNote, PendingDecision } from "../components/DecisionForm";
import { ErrorBox, isNotFound, Loading, StatusBadge } from "../components/ui";
import { formatNumber, formatUsd } from "../format";
import { Link, paths } from "../router";
import type { RunDetail, WorkUnit } from "../types";

function files(unit: WorkUnit): string {
  const shown = unit.files.slice(0, 3).join(", ");
  return unit.files.length > 3 ? `${shown} (+${unit.files.length - 3})` : shown;
}

function sites(unit: WorkUnit): number {
  return Object.values(unit.rule_sites).reduce((sum, n) => sum + n, 0);
}

export function PlanPanel({ run }: { run: RunDetail }) {
  const plan = usePlan(run.id);
  const queryClient = useQueryClient();
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(new Set());
  const decide = useMutation({
    mutationFn: ({ decision, reason }: { decision: "approve" | "reject"; reason: string | null }) =>
      api.decidePlan(run.id, {
        decision,
        reason,
        exclude: decision === "approve" ? [...excluded].sort() : [],
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.plan(run.id) });
      void queryClient.invalidateQueries({ queryKey: keys.run(run.id) });
    },
  });

  if (plan.isPending) return <Loading what="Loading the plan" />;
  if (plan.isError) {
    if (isNotFound(plan.error)) {
      return (
        <div className="card empty">
          <h2>No plan yet</h2>
          <p>{plan.error.message}. It appears once the run has measured its baseline and indexed the code.</p>
        </div>
      );
    }
    return <ErrorBox error={plan.error} title="Can't show the plan" />;
  }
  const { plan: p, gate_open: gateOpen, pending } = plan.data;
  const toggle = (unitId: string) =>
    setExcluded((current) => {
      const next = new Set(current);
      if (next.has(unitId)) next.delete(unitId);
      else next.add(unitId);
      return next;
    });
  const canDecide = gateOpen && !pending && !decide.data;

  return (
    <>
      {gateOpen || decide.data ? (
        <div className="card">
          <h2>Plan gate</h2>
          {pending && !decide.data ? <PendingDecision approval={pending} /> : null}
          {canDecide ? (
            <DecisionForm
              approveLabel={
                excluded.size
                  ? `Approve without ${excluded.size} unit${excluded.size > 1 ? "s" : ""}`
                  : "Approve the plan"
              }
              rejectLabel="Reject the plan"
              busy={decide.isPending}
              error={decide.error}
              result={decide.data}
              onDecide={(decision, reason) => decide.mutate({ decision, reason })}
            >
              <p>
                {run.status === "awaiting_plan_approval"
                  ? "The run waits for this decision."
                  : "The plan can be decided while the run is still preparing it."}{" "}
                Untick units below to leave them out; rejecting ends the run.
              </p>
            </DecisionForm>
          ) : decide.data ? (
            <div className="callout ok" role="status">
              {decide.data.message}
            </div>
          ) : null}
          {gateOpen && !run.engine_alive ? <DecisionNote runId={run.id} /> : null}
        </div>
      ) : null}

      <div className="card">
        <h2>
          {p.language} {p.source_standard} → {p.target_standard}
        </h2>
        <div className="row meta">
          <span>{p.units.length} units</span>
          {p.graph ? (
            <span>
              · {formatNumber(p.graph.files)} files, {formatNumber(p.graph.clusters)} clusters
              {p.graph.cyclic_clusters ? `, ${p.graph.cyclic_clusters} cyclic` : ""}
            </span>
          ) : null}
          {p.estimate ? (
            <span>
              · estimated {formatNumber(p.estimate.tokens)} tokens
              {p.estimate.cost_usd !== null ? `, about ${formatUsd(p.estimate.cost_usd)}` : ""} with{" "}
              <span className="mono">{p.estimate.model}</span>
            </span>
          ) : null}
        </div>
        {p.warnings.length ? (
          <div className="callout wait" style={{ marginTop: 12 }}>
            <strong>Warnings</strong>
            <ul>
              {p.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <h3 style={{ marginTop: 14 }}>Rules</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th>Kind</th>
                <th className="num">Sites</th>
                <th className="num">Files</th>
                <th className="num">Units</th>
              </tr>
            </thead>
            <tbody>
              {p.rules.map((rule) => (
                <tr key={rule.id}>
                  <td className="mono">
                    {rule.id}
                    {rule.automatic ? <span className="chip"> automatic</span> : null}
                  </td>
                  <td>{rule.kind.replaceAll("_", " ")}</td>
                  <td className="num">{rule.sites ?? "—"}</td>
                  <td className="num">
                    {rule.files}
                    {rule.skipped_files ? <span className="muted"> ({rule.skipped_files} skipped)</span> : null}
                  </td>
                  <td className="num">{rule.units}</td>
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
                {canDecide ? <th>Run it</th> : null}
                <th className="num">#</th>
                <th>Unit</th>
                <th>Rules</th>
                <th>Files</th>
                <th>Flags</th>
                <th className="num">Sites</th>
                <th className="num">Attempts</th>
                <th className="num">Cost</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {p.units.map((unit) => (
                <tr key={unit.id}>
                  {canDecide ? (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Run ${unit.id}`}
                        checked={!excluded.has(unit.id)}
                        disabled={unit.status !== "pending"}
                        onChange={() => toggle(unit.id)}
                      />
                    </td>
                  ) : null}
                  <td className="num">{unit.seq}</td>
                  <td>
                    <Link to={paths.unit(run.id, unit.id)} className="mono">
                      {unit.id}
                    </Link>
                  </td>
                  <td className="mono">{unit.rules.join(", ")}</td>
                  <td className="mono" title={unit.files.join("\n")}>
                    {files(unit)}
                    {unit.reference_files.length ? (
                      <span className="muted"> +{unit.reference_files.length} referencing</span>
                    ) : null}
                  </td>
                  <td>
                    {unit.flags.map((f) => (
                      <span key={f} className="chip">
                        {f.replaceAll("_", " ")}
                      </span>
                    ))}
                  </td>
                  <td className="num">{sites(unit)}</td>
                  <td className="num">{unit.estimate ? unit.estimate.expected_attempts.toFixed(1) : ""}</td>
                  <td className="num">{formatUsd(unit.estimate?.cost_usd)}</td>
                  <td>
                    <StatusBadge status={unit.status} title={unit.note ?? undefined} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
