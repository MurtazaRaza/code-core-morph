import { useQuery } from "@tanstack/react-query";

import type {
  ActionResult,
  AttemptView,
  GraphView,
  PlanView,
  PortView,
  RunDetail,
  RunReport,
  RunSummary,
  SessionInfo,
  TextFile,
  UnitView,
} from "./types";

/** An error the API answered with: what happened, and often what to do next. */
export class ApiError extends Error {
  readonly status: number;
  readonly hint: string | null;
  readonly reason: string | null;

  constructor(status: number, message: string, hint?: string | null, reason?: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.hint = hint ?? null;
    this.reason = reason ?? null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (init?.body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    let body: { error?: string; hint?: string; reason?: string; detail?: unknown } = {};
    try {
      body = (await response.json()) as typeof body;
    } catch {
      // not JSON: keep the status text
    }
    const message =
      body.error ?? (body.detail !== undefined ? "the request was not valid" : response.statusText);
    throw new ApiError(response.status, message || `HTTP ${response.status}`, body.hint, body.reason);
  }
  return (await response.json()) as T;
}

const enc = encodeURIComponent;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export interface Decision {
  decision: "approve" | "reject";
  reason: string | null;
  exclude?: string[];
}

export const api = {
  session: () => request<SessionInfo>("/api/session"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (runId: string) => request<RunDetail>(`/api/runs/${enc(runId)}`),
  plan: (runId: string) => request<PlanView>(`/api/runs/${enc(runId)}/plan`),
  graph: (runId: string) => request<GraphView>(`/api/runs/${enc(runId)}/graph`),
  unit: (runId: string, unitId: string) =>
    request<UnitView>(`/api/runs/${enc(runId)}/units/${enc(unitId)}`),
  port: (runId: string, unitId: string) =>
    request<PortView>(`/api/runs/${enc(runId)}/units/${enc(unitId)}/port`),
  attempt: (runId: string, unitId: string, n: number) =>
    request<AttemptView>(`/api/runs/${enc(runId)}/units/${enc(unitId)}/attempts/${n}`),
  attemptFile: (runId: string, unitId: string, n: number, name: string) =>
    request<TextFile>(
      `/api/runs/${enc(runId)}/units/${enc(unitId)}/attempts/${n}/files/${enc(name)}`,
    ),
  report: (runId: string) => request<RunReport>(`/api/runs/${enc(runId)}/report`),
  decidePlan: (runId: string, body: Decision) =>
    request<ActionResult>(`/api/runs/${enc(runId)}/plan/decision`, post(body)),
  decideUnit: (runId: string, unitId: string, body: Decision) =>
    request<ActionResult>(`/api/runs/${enc(runId)}/units/${enc(unitId)}/decision`, post(body)),
  abort: (runId: string, reason: string | null) =>
    request<ActionResult>(`/api/runs/${enc(runId)}/abort`, post({ reason })),
};

export const reportUrl = (runId: string, format: "md" | "json") =>
  `/api/runs/${enc(runId)}/report?format=${format}&download=true`;

export const keys = {
  session: ["session"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["run", runId] as const,
  plan: (runId: string) => ["plan", runId] as const,
  graph: (runId: string) => ["graph", runId] as const,
  unit: (runId: string, unitId: string) => ["unit", runId, unitId] as const,
  port: (runId: string, unitId: string) => ["port", runId, unitId] as const,
  attempt: (runId: string, unitId: string, n: number) => ["attempt", runId, unitId, n] as const,
  report: (runId: string) => ["report", runId] as const,
};

// Not found means "not there yet" here (no plan before planning): don't retry it.
const retry = (count: number, error: unknown) =>
  !(error instanceof ApiError && error.status < 500) && count < 2;

export const useSession = () =>
  useQuery({ queryKey: keys.session, queryFn: api.session, staleTime: Infinity, retry });

export const useRuns = () =>
  useQuery({ queryKey: keys.runs, queryFn: api.runs, refetchInterval: 5000, retry });

export const useRun = (runId: string, refetchInterval: number | false) =>
  useQuery({ queryKey: keys.run(runId), queryFn: () => api.run(runId), refetchInterval, retry });

export const usePlan = (runId: string) =>
  useQuery({ queryKey: keys.plan(runId), queryFn: () => api.plan(runId), retry });

export const useGraph = (runId: string) =>
  useQuery({ queryKey: keys.graph(runId), queryFn: () => api.graph(runId), retry });

export const useUnit = (runId: string, unitId: string) =>
  useQuery({ queryKey: keys.unit(runId, unitId), queryFn: () => api.unit(runId, unitId), retry });

export const usePort = (runId: string, unitId: string, enabled: boolean) =>
  useQuery({
    queryKey: keys.port(runId, unitId),
    queryFn: () => api.port(runId, unitId),
    enabled,
    retry,
  });

export const useAttempt = (runId: string, unitId: string, n: number | undefined) =>
  useQuery({
    queryKey: keys.attempt(runId, unitId, n ?? 0),
    queryFn: () => api.attempt(runId, unitId, n ?? 0),
    enabled: n !== undefined,
    retry,
  });

export const useAttemptFile = (runId: string, unitId: string, n: number, name: string | null) =>
  useQuery({
    queryKey: [...keys.attempt(runId, unitId, n), "file", name],
    queryFn: () => api.attemptFile(runId, unitId, n, name ?? ""),
    enabled: name !== null,
    retry,
  });

export const useReport = (runId: string) =>
  useQuery({ queryKey: keys.report(runId), queryFn: () => api.report(runId), retry });
