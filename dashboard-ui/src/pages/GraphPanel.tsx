import { lazy, Suspense, useMemo, useState } from "react";

import { useGraph } from "../api";
import type { GraphMode } from "../components/GraphView";
import { ErrorBox, isNotFound, Loading } from "../components/ui";
import { statusLabel, statusTone } from "../format";
import { navigate, paths } from "../router";
import type { Theme } from "../theme";
import { type RunDetail, UNIT_STATUSES } from "../types";

const GraphView = lazy(() => import("../components/GraphView"));
const NO_UNIT = "none";

export function GraphPanel({ run, theme }: { run: RunDetail; theme: Theme }) {
  const graph = useGraph(run.id);
  const [mode, setMode] = useState<GraphMode>("units");
  const [query, setQuery] = useState("");
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  const present = useMemo(() => {
    if (!graph.data) return [] as string[];
    const statuses = new Set<string>(
      mode === "units"
        ? graph.data.units.map((u) => u.status)
        : graph.data.files.map((f) => f.status ?? NO_UNIT),
    );
    return [...UNIT_STATUSES, NO_UNIT].filter((s) => statuses.has(s));
  }, [graph.data, mode]);

  if (graph.isPending) return <Loading what="Loading the dependency graph" />;
  if (graph.isError) {
    if (isNotFound(graph.error)) {
      return (
        <div className="card empty">
          <h2>No dependency graph</h2>
          <p>{graph.error.message}.</p>
          {graph.error.reason ? <p className="muted">{graph.error.reason}.</p> : null}
        </div>
      );
    }
    return <ErrorBox error={graph.error} title="Can't show the graph" />;
  }
  const toggle = (status: string) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });

  return (
    <div className="card">
      <div className="graph-tools">
        <div className="row" role="group" aria-label="Show">
          <button className={mode === "units" ? "primary" : undefined} onClick={() => setMode("units")}>
            Units ({graph.data.units.length})
          </button>
          <button className={mode === "files" ? "primary" : undefined} onClick={() => setMode("files")}>
            Files ({graph.data.files.length})
          </button>
        </div>
        <input
          type="search"
          aria-label="Search the graph"
          placeholder={mode === "units" ? "Unit, rule or file" : "File or unit"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="legend" aria-label="Filter by status">
          {present.map((status) => (
            <label key={status}>
              <input type="checkbox" checked={!hidden.has(status)} onChange={() => toggle(status)} />
              <span className={`badge tone-${status === NO_UNIT ? "idle" : statusTone(status)}`}>
                {status === NO_UNIT ? "in no unit" : statusLabel(status)}
              </span>
            </label>
          ))}
        </div>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Arrows point from a dependency to the code that depends on it; the plan works from the
        top. Click a unit to open it.
      </p>
      <Suspense fallback={<Loading what="Drawing the graph" />}>
        <GraphView
          graph={graph.data}
          mode={mode}
          query={query}
          hidden={hidden}
          theme={theme}
          onOpenUnit={(unitId) => navigate(paths.unit(run.id, unitId))}
        />
      </Suspense>
    </div>
  );
}
