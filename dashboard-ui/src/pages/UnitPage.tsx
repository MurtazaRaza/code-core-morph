import { useMutation, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState } from "react";

import { api, keys, useAttempt, useAttemptFile, usePort, useRun, useUnit } from "../api";
import { DecisionForm, DecisionNote, PendingDecision } from "../components/DecisionForm";
import { Diagnostics, Transcript } from "../components/Transcript";
import { Commit, ErrorBox, Loading, StatusBadge, Tabs } from "../components/ui";
import { displayPath, languageOf, parseUnifiedDiff, sidesOf } from "../diff";
import { useRunEvents } from "../events";
import { formatClock, formatDuration, formatTime, statusLabel } from "../format";
import { Link, navigate, paths } from "../router";
import type { Theme } from "../theme";
import {
  type Attempt,
  type AttemptView,
  type PortFile,
  TERMINAL_RUN_STATUSES,
  type TextFile,
  type UnitView,
} from "../types";

const DiffView = lazy(() => import("../components/DiffView"));
const CodeView = lazy(() => import("../components/CodeView"));
// Units that ended without a commit: their note says why.
const LEFT_OUT = new Set(["failed", "skipped", "too_large", "excluded"]);

export function UnitPage({
  runId,
  unitId,
  attempt,
  theme,
}: {
  runId: string;
  unitId: string;
  attempt: number | undefined;
  theme: Theme;
}) {
  const run = useRun(runId, 10_000);
  const unit = useUnit(runId, unitId);
  const terminal = run.data ? TERMINAL_RUN_STATUSES.has(run.data.status) : false;
  useRunEvents(runId, run.data?.last_event_id, Boolean(run.data) && !terminal);

  if (unit.isPending) return <Loading what={`Loading unit ${unitId}`} />;
  if (unit.isError) return <ErrorBox error={unit.error} title={`Can't show unit ${unitId}`} />;
  const view = unit.data;
  const numbers = attemptNumbers(view);
  const selected = attempt && numbers.includes(attempt) ? attempt : numbers.at(-1);

  return (
    <>
      <div className="crumbs">
        <Link to={paths.runs()}>Runs</Link> / <Link to={paths.run(runId)} className="mono">{runId}</Link> /{" "}
        <span className="mono">{unitId}</span>
      </div>
      <div className="page-head">
        <h1 className="mono">{view.unit.id}</h1>
        <StatusBadge status={view.unit.status} title={view.unit.note ?? undefined} />
        <span className="mono meta">{view.unit.rules.join(", ")}</span>
        {view.unit.flags.map((f) => (
          <span key={f} className="chip">
            {f.replaceAll("_", " ")}
          </span>
        ))}
      </div>
      {view.gate.waiting ? (
        <UnitGatePanel runId={runId} view={view} engineAlive={run.data?.engine_alive ?? false} />
      ) : null}
      <UnitSummary view={view} />
      {view.unit.port ? <PortPanel runId={runId} unitId={unitId} theme={theme} /> : null}
      <div className="card">
        <h2>Attempts</h2>
        {numbers.length === 0 ? (
          <p className="muted">
            {view.unit.status === "pending" ? "The unit hasn't started yet." : "No attempt was recorded."}
          </p>
        ) : (
          <>
            <div className="timeline" role="list" aria-label="Attempts">
              {numbers.map((n) => (
                <AttemptButton
                  key={n}
                  n={n}
                  attempt={view.attempts.find((a) => a.number === n)}
                  active={n === selected}
                  onSelect={() => navigate(paths.unit(runId, unitId, n), { replace: true })}
                />
              ))}
            </div>
            {selected !== undefined ? (
              <AttemptDetail runId={runId} unitId={unitId} n={selected} theme={theme} />
            ) : null}
          </>
        )}
      </div>
    </>
  );
}

// A module port's unit side by side (D-58): the C++ before the unit, the code it committed.
function PortPanel({ runId, unitId, theme }: { runId: string; unitId: string; theme: Theme }) {
  const port = usePort(runId, unitId, true);
  const [source, setSource] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  if (port.isPending) return <Loading what="Loading the port" />;
  if (port.isError) return <ErrorBox error={port.error} title="Can't show the port" />;
  const view = port.data;
  const left = view.sources.find((f) => f.path === source) ?? view.sources[0];
  const right = view.targets.find((f) => f.path === target) ?? view.targets[0];
  return (
    <div className="card">
      <h2>Port</h2>
      <p className="muted">
        Into <span className="mono">{`${view.crate}::${view.module}`}</span>, the crate of{" "}
        <span className="mono">{view.build_target}</span>
        {view.after ? null : ": nothing is committed yet"}.
      </p>
      <div className="port-sides">
        <PortSide
          label="Before (C++)"
          files={view.sources}
          selected={left}
          onSelect={setSource}
          empty="No C++ file of the unit."
          theme={theme}
        />
        <PortSide
          label="After (Rust)"
          files={view.targets}
          selected={right}
          onSelect={setTarget}
          empty="The unit has no commit with new code yet."
          theme={theme}
        />
      </div>
    </div>
  );
}

function PortSide({
  label,
  files,
  selected,
  onSelect,
  empty,
  theme,
}: {
  label: string;
  files: PortFile[];
  selected: PortFile | undefined;
  onSelect: (path: string) => void;
  empty: string;
  theme: Theme;
}) {
  return (
    <div className="port-side">
      <div className="port-side-head">
        <strong>{label}</strong>
        {files.length ? (
          <select aria-label={label} value={selected?.path} onChange={(e) => onSelect(e.target.value)}>
            {files.map((f) => (
              <option key={f.path} value={f.path}>
                {f.path}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      {selected ? (
        <>
          <Suspense fallback={<Loading what="Loading the editor" />}>
            <CodeView text={selected.text} language={languageOf(selected.path)} theme={theme} />
          </Suspense>
          {selected.truncated ? <p className="muted">Only the start of the file is shown.</p> : null}
        </>
      ) : (
        <p className="muted">{empty}</p>
      )}
    </div>
  );
}

function attemptNumbers(view: UnitView): number[] {
  const numbers = new Set<number>([
    ...view.attempts.map((a) => a.number),
    ...view.attempt_files.map((f) => f.number),
  ]);
  return [...numbers].sort((a, b) => a - b);
}

function AttemptButton({
  n,
  attempt,
  active,
  onSelect,
}: {
  n: number;
  attempt: Attempt | undefined;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button role="listitem" className={active ? "active" : undefined} onClick={onSelect} aria-pressed={active}>
      <span>
        <strong>#{n}</strong>{" "}
        {attempt?.outcome ? <StatusBadge status={attempt.outcome} /> : <span className="muted">running</span>}
      </span>
      <span className="muted" style={{ fontSize: 12 }}>
        {attempt ? `${attempt.role}${attempt.model ? ` · ${attempt.model}` : ""}` : ""}
      </span>
      <span className="muted" style={{ fontSize: 12 }}>
        {attempt ? formatClock(attempt.started_at) : ""}
        {attempt?.diff_lines !== null && attempt?.diff_lines !== undefined ? ` · ${attempt.diff_lines} lines` : ""}
      </span>
    </button>
  );
}

function UnitGatePanel({ runId, view, engineAlive }: { runId: string; view: UnitView; engineAlive: boolean }) {
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ decision, reason }: { decision: "approve" | "reject"; reason: string | null }) =>
      api.decideUnit(runId, view.unit.id, { decision, reason }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.unit(runId, view.unit.id) });
      void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
    },
  });
  const { gate } = view;
  return (
    <div className="card">
      <h2>Unit gate</h2>
      <div className="callout wait">
        <strong>This unit waits for your approval.</strong>
        {gate.commit ? (
          <p>
            Its commit <Commit commit={gate.commit} /> is on the branch: keep it, or roll it back.
          </p>
        ) : null}
        {gate.reasons.length ? (
          <ul>
            {gate.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {gate.pending && !decide.data ? <PendingDecision approval={gate.pending} /> : null}
      {!gate.pending && !decide.data ? (
        <DecisionForm
          approveLabel="Keep the unit"
          rejectLabel="Roll it back"
          busy={decide.isPending}
          error={decide.error}
          result={decide.data}
          onDecide={(decision, reason) => decide.mutate({ decision, reason })}
        />
      ) : decide.data ? (
        <div className="callout ok" role="status">
          {decide.data.message}
        </div>
      ) : null}
      {!engineAlive ? <DecisionNote runId={runId} /> : null}
    </div>
  );
}

function UnitSummary({ view }: { view: UnitView }) {
  const { unit, result } = view;
  const remaining = result ? Object.entries(result.remaining_sites).filter(([, n]) => n > 0) : [];
  return (
    <div className="card">
      <h2>Unit</h2>
      <div className="table-wrap">
        <table>
          <tbody>
            {result?.commit ? (
              <tr>
                <th>Commit</th>
                <td>
                  <Commit commit={result.commit} />
                  {result.approved_by ? <span className="muted"> approved by {result.approved_by}</span> : null}
                </td>
              </tr>
            ) : null}
            {result ? (
              <tr>
                <th>Result</th>
                <td>
                  {result.attempts} attempt{result.attempts === 1 ? "" : "s"} in {formatDuration(result.duration_s)}
                  {result.agent_skipped ? " · the codemods were enough" : ""}
                  {result.models.length ? ` · ${result.models.join(", ")}` : ""}
                  <span className="muted"> · finished {formatTime(result.finished_at)}</span>
                </td>
              </tr>
            ) : null}
            {result?.summary ? (
              <tr>
                <th>Summary</th>
                <td className="wrap">{result.summary}</td>
              </tr>
            ) : null}
            {LEFT_OUT.has(unit.status) && (result?.reason || unit.note) ? (
              <tr>
                <th>Why</th>
                <td className="wrap">{result?.reason ?? unit.note}</td>
              </tr>
            ) : null}
            <tr>
              <th>Files</th>
              <td className="mono">
                {unit.files.map((f) => (
                  <div key={f}>
                    {f}
                    {unit.reference_files.includes(f) ? <span className="muted"> (references only)</span> : null}
                  </div>
                ))}
              </td>
            </tr>
            {result?.codemod_files.length ? (
              <tr>
                <th>Changed by codemods</th>
                <td className="mono">{result.codemod_files.join(", ")}</td>
              </tr>
            ) : null}
            {result?.test_files_changed.length ? (
              <tr>
                <th>Test files changed</th>
                <td className="mono">{result.test_files_changed.join(", ")}</td>
              </tr>
            ) : null}
            {remaining.length ? (
              <tr>
                <th>Rule sites left</th>
                <td>{remaining.map(([rule, n]) => `${rule}: ${n}`).join(", ")}</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {result?.review_notes.length || result?.warnings.length ? (
        <div style={{ marginTop: 12 }}>
          {result.review_notes.length ? (
            <>
              <h3>Notes from the agent</h3>
              <ul>
                {result.review_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </>
          ) : null}
          {result.warnings.length ? (
            <>
              <h3>Warnings</h3>
              <ul>
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

type AttemptTab = "diff" | "diagnostics" | "tests" | "invariants" | "feedback" | "transcript" | "logs" | "files";

function AttemptDetail({ runId, unitId, n, theme }: { runId: string; unitId: string; n: number; theme: Theme }) {
  const attempt = useAttempt(runId, unitId, n);
  const [tab, setTab] = useState<AttemptTab>("diff");
  if (attempt.isPending) return <Loading what={`Loading attempt ${n}`} />;
  if (attempt.isError) return <ErrorBox error={attempt.error} title={`Can't show attempt ${n}`} />;
  const view = attempt.data;
  const record = view.attempt;
  const failedInvariants = record?.invariant_results.filter((r) => !r.passed).length ?? 0;
  return (
    <div>
      {record?.summary ? <p className="wrap">{record.summary}</p> : null}
      <Tabs<AttemptTab>
        label={`Attempt ${n}`}
        active={tab}
        onSelect={setTab}
        tabs={[
          { id: "diff", label: "Diff" },
          { id: "diagnostics", label: "Compiler", count: record?.diagnostics.length },
          { id: "tests", label: "Tests", count: record?.regressions.length },
          { id: "invariants", label: "Invariants", count: failedInvariants || undefined },
          { id: "feedback", label: "Feedback", hidden: !view.feedback },
          { id: "transcript", label: "Transcript", hidden: !view.transcript },
          { id: "logs", label: "Logs", hidden: !view.build_log && !view.test_log },
          { id: "files", label: "Files", count: view.files.length },
        ]}
      />
      {tab === "diff" ? <DiffTab view={view} theme={theme} /> : null}
      {tab === "diagnostics" ? <Diagnostics diagnostics={record?.diagnostics ?? []} /> : null}
      {tab === "tests" ? <TestsTab attempt={record} /> : null}
      {tab === "invariants" ? <InvariantsTab attempt={record} /> : null}
      {tab === "feedback" && view.feedback ? <LogFile file={view.feedback} wrap /> : null}
      {tab === "transcript" && view.transcript ? (
        <Transcript entries={view.transcript} truncated={view.transcript_truncated} />
      ) : null}
      {tab === "logs" ? (
        <div className="stack">
          {view.build_log ? <LogFile file={view.build_log} title="Build" /> : null}
          {view.test_log ? <LogFile file={view.test_log} title="Tests" /> : null}
        </div>
      ) : null}
      {tab === "files" ? <FilesTab runId={runId} unitId={unitId} n={n} names={view.files} /> : null}
    </div>
  );
}

function DiffTab({ view, theme }: { view: AttemptView; theme: Theme }) {
  const files = useMemo(() => (view.diff ? parseUnifiedDiff(view.diff.text) : []), [view.diff]);
  const [raw, setRaw] = useState(false);
  if (!view.diff || !view.diff.text.trim()) return <p className="muted">This attempt changed no file.</p>;
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="muted">
          {files.length} file{files.length === 1 ? "" : "s"}
          {view.diff.truncated ? " (the patch is longer than shown)" : ""}
        </span>
        <button className="link" onClick={() => setRaw((r) => !r)}>
          {raw ? "Side by side" : "Show the patch"}
        </button>
      </div>
      {raw ? (
        <pre className="log">{view.diff.text}</pre>
      ) : (
        files.map((file, i) => {
          const path = displayPath(file);
          return (
            <section className="diff-file" key={`${path}-${i}`}>
              <header>
                <span>{path}</span>
                <span className="added">+{file.added}</span>
                <span className="removed">−{file.removed}</span>
                {file.oldPath === null ? <span className="chip">new</span> : null}
                {file.newPath === null ? <span className="chip">deleted</span> : null}
              </header>
              {file.binary ? (
                <p className="muted" style={{ padding: 12 }}>
                  A binary file.
                </p>
              ) : (
                <Suspense fallback={<Loading what="Loading the diff editor" />}>
                  <DiffView sides={sidesOf(file)} language={languageOf(path)} theme={theme} />
                </Suspense>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}

function TestsTab({ attempt }: { attempt: Attempt | null }) {
  if (!attempt) return <p className="muted">No test results were recorded.</p>;
  if (attempt.regressions.length === 0) {
    return (
      <p className="muted">
        {attempt.outcome === "succeeded"
          ? "Every test that passed before the run passes."
          : "No baseline test regressed in this attempt (it may have stopped before the tests ran)."}
      </p>
    );
  }
  return (
    <>
      <p>These tests passed before the run and fail after this attempt:</p>
      <ul className="mono">
        {attempt.regressions.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </>
  );
}

function InvariantsTab({ attempt }: { attempt: Attempt | null }) {
  const results = attempt?.invariant_results ?? [];
  if (results.length === 0) return <p className="muted">No safety invariant was checked.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Invariant</th>
            <th>Result</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.invariant}>
              <td className="mono">{r.invariant}</td>
              <td>
                <span className={`badge tone-${r.passed ? "ok" : r.severity === "error" ? "bad" : "wait"}`}>
                  {r.passed ? "passed" : r.needs_approval ? "needs approval" : statusLabel(r.severity)}
                </span>
              </td>
              <td className="wrap">{r.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogFile({ file, title, wrap = false }: { file: TextFile; title?: string; wrap?: boolean }) {
  return (
    <div>
      {title ? <h3>{title}</h3> : null}
      {file.truncated ? (
        <p className="muted">
          Showing {wrap ? "the start" : "the end"} of {file.name} ({(file.size / 1024).toFixed(0)} KB).
        </p>
      ) : null}
      <pre className={wrap ? "log wrap" : "log"}>{file.text}</pre>
    </div>
  );
}

function FilesTab({ runId, unitId, n, names }: { runId: string; unitId: string; n: number; names: string[] }) {
  const [name, setName] = useState<string | null>(null);
  const file = useAttemptFile(runId, unitId, n, name);
  if (names.length === 0) return <p className="muted">The attempt kept no files.</p>;
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        {names.map((n) => (
          <button key={n} className={n === name ? "primary" : undefined} onClick={() => setName(n)}>
            {n}
          </button>
        ))}
      </div>
      {name === null ? <p className="muted">Pick a file to show it.</p> : null}
      {file.isFetching ? <Loading what={`Loading ${name}`} /> : null}
      {file.error ? <ErrorBox error={file.error} title={`Can't show ${name}`} /> : null}
      {file.data && !file.isFetching ? <LogFile file={file.data} wrap /> : null}
    </div>
  );
}
