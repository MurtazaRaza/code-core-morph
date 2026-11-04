import type { ReactNode } from "react";

import { ApiError } from "../api";
import { statusLabel, statusTone } from "../format";

export function StatusBadge({ status, title }: { status: string; title?: string }) {
  return (
    <span className={`badge tone-${statusTone(status)}`} title={title}>
      {statusLabel(status)}
    </span>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status">
      {what}…
    </p>
  );
}

export function ErrorBox({ error, title }: { error: unknown; title?: string }) {
  if (error instanceof ApiError && error.status === 401) {
    return (
      <div className="error-box" role="alert">
        <strong>This browser hasn&apos;t opened this dashboard&apos;s link.</strong>
        <p>
          Open the address <code>codecoremorph dashboard</code> printed: it holds the access
          token of this launch.
        </p>
      </div>
    );
  }
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-box" role="alert">
      <strong>{title ?? "Something went wrong"}:</strong> {message}
      {error instanceof ApiError && error.reason ? <p>Why: {error.reason}</p> : null}
      {error instanceof ApiError && error.hint ? <p>Next: {error.hint}</p> : null}
    </div>
  );
}

export function isNotFound(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404;
}

/** A bar of ``value`` out of ``max``. A budget turns amber near its cap and red at it;
 * progress (``progress``) never warns. */
export function Meter({
  value,
  max,
  label,
  progress = false,
}: {
  value: number;
  max: number | null;
  label: string;
  progress?: boolean;
}) {
  const share = max && max > 0 ? Math.min(1, value / max) : 0;
  const level = progress ? "" : share >= 1 ? "full" : share >= 0.8 ? "high" : "";
  return (
    <div
      className={`meter ${level}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max ?? undefined}
      aria-valuenow={value}
    >
      <span style={{ width: `${(share * 100).toFixed(1)}%` }} />
    </div>
  );
}

export function Stat({ label, value, children }: { label: string; value: ReactNode; children?: ReactNode }) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {children}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onSelect,
  label,
}: {
  tabs: { id: T; label: string; count?: number; hidden?: boolean }[];
  active: T;
  onSelect: (id: T) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs
        .filter((tab) => !tab.hidden)
        .map((tab) => (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={tab.id === active}
            className={tab.id === active ? "active" : undefined}
            onClick={() => onSelect(tab.id)}
          >
            {tab.label}
            {tab.count !== undefined ? <span className="count">{tab.count}</span> : null}
          </button>
        ))}
    </div>
  );
}

export function Commit({ commit }: { commit: string | null | undefined }) {
  if (!commit) return null;
  return (
    <code title={commit} className="mono">
      {commit.slice(0, 12)}
    </code>
  );
}
