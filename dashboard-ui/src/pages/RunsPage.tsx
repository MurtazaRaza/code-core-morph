import { useRuns } from "../api";
import { ErrorBox, Loading, Meter, StatusBadge } from "../components/ui";
import { formatTime, shortCommit, timeAgo } from "../format";
import { Link, paths } from "../router";
import type { RunSummary } from "../types";

function total(run: RunSummary): number {
  return Object.values(run.unit_counts).reduce((sum, n) => sum + n, 0);
}

export function RunsPage() {
  const runs = useRuns();
  if (runs.isPending) return <Loading what="Loading the runs" />;
  if (runs.isError) return <ErrorBox error={runs.error} title="Can't list the runs" />;
  if (runs.data.length === 0) {
    return (
      <div className="card empty">
        <h2>No runs yet</h2>
        <p>
          Start one in this repository with <code>codecoremorph run</code>; it shows up here
          while it runs.
        </p>
      </div>
    );
  }
  return (
    <>
      <div className="page-head">
        <h1>Runs</h1>
        <span className="meta">{runs.data.length} in this repository</span>
      </div>
      <div className="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Units</th>
              <th>Needs you</th>
              <th>Engine</th>
              <th>Base</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.map((run) => {
              const units = total(run);
              const committed = run.unit_counts.committed ?? 0;
              return (
                <tr key={run.id}>
                  <td>
                    <Link to={paths.run(run.id)} className="mono">
                      {run.id}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td style={{ minWidth: 140 }}>
                    {units ? (
                      <>
                        <span className="mono">
                          {committed}/{units}
                        </span>{" "}
                        <span className="muted">committed</span>
                        <Meter
                          value={committed}
                          max={units}
                          label={`${run.id} units committed`}
                          progress
                        />
                      </>
                    ) : (
                      <span className="muted">not planned yet</span>
                    )}
                  </td>
                  <td>
                    {run.waiting.map((what) => (
                      <Link
                        key={what}
                        to={what === "plan" ? paths.run(run.id, "plan") : paths.unit(run.id, what)}
                        className="badge tone-wait"
                      >
                        {what === "plan" ? "plan approval" : what}
                      </Link>
                    ))}
                  </td>
                  <td>
                    <span className="row" title={run.engine_alive ? "an engine is running it" : "no engine is running it"}>
                      <span className={run.engine_alive ? "dot live" : "dot"} />
                      {run.engine_alive ? "running" : "stopped"}
                    </span>
                  </td>
                  <td className="mono">{shortCommit(run.base_commit)}</td>
                  <td title={formatTime(run.updated_at)}>{timeAgo(run.updated_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
