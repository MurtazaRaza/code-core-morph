const STATUS_LABELS: Record<string, string> = {
  awaiting_plan_approval: "waiting for plan approval",
  awaiting_approval: "waiting for approval",
  in_progress: "in progress",
  completed_with_failures: "completed with failures",
  budget_exhausted: "budget exhausted",
  too_large: "too large",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replaceAll("_", " ");
}

/** The color family a status is shown in. */
export function statusTone(status: string): "ok" | "bad" | "busy" | "wait" | "idle" | "muted" {
  switch (status) {
    case "committed":
    case "completed":
    case "succeeded":
      return "ok";
    case "failed":
    case "aborted":
    case "rejected":
    case "budget_exhausted":
    case "build_failed":
    case "tests_failed":
    case "invariant_failed":
    case "edits_rejected":
    case "gate_rejected":
    case "error":
    case "timed_out":
    case "refused":
      return "bad";
    case "awaiting_plan_approval":
    case "awaiting_approval":
    case "completed_with_failures":
      return "wait";
    case "pending":
    case "created":
      return "idle";
    case "skipped":
    case "too_large":
    case "excluded":
    case "no_submission":
      return "muted";
    default:
      return "busy";
  }
}

export function shortCommit(commit: string | null | undefined): string {
  return commit ? commit.slice(0, 12) : "";
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export function formatClock(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString();
}

export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return iso;
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return `$${value.toFixed(value < 10 ? 2 : 0)}`;
}

/** A one-line description of an engine event, for the activity list. */
export function describeEvent(type: string, data: Record<string, unknown>): string {
  const text = (key: string) => (typeof data[key] === "string" ? (data[key] as string) : "");
  switch (type) {
    case "step.started":
      return text("message");
    case "run.status":
      return `run ${statusLabel(text("status"))}` + (text("reason") ? `: ${text("reason")}` : "");
    case "unit.status":
      return statusLabel(text("status"));
    case "gate.waiting":
      return `waiting at the ${text("gate")} gate`;
    case "gate.decided":
      return `${text("gate")} gate: ${text("decision")} (${text("source")})`;
    case "attempt.finished":
      return `attempt ${statusLabel(text("outcome"))}`;
    case "step.finished":
      return `${text("step")} finished`;
    case "log":
      return text("message");
    default:
      return Object.entries(data)
        .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join(", ");
  }
}
