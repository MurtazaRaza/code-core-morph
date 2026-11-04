import { type ReactNode, useState } from "react";

import { formatTime } from "../format";
import type { ActionResult, Approval } from "../types";
import { ErrorBox } from "./ui";

/** Approve or reject at a gate, with an optional reason. */
export function DecisionForm({
  approveLabel,
  rejectLabel,
  onDecide,
  busy,
  error,
  result,
  children,
}: {
  approveLabel: string;
  rejectLabel: string;
  onDecide: (decision: "approve" | "reject", reason: string | null) => void;
  busy: boolean;
  error: unknown;
  result: ActionResult | undefined;
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const decide = (decision: "approve" | "reject") => onDecide(decision, reason.trim() || null);
  return (
    <div className="gate-form">
      {children}
      <textarea
        aria-label="Reason"
        placeholder="Reason (optional, kept in the report)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div className="row">
        <button className="primary" disabled={busy} onClick={() => decide("approve")}>
          {approveLabel}
        </button>
        <button className="danger" disabled={busy} onClick={() => decide("reject")}>
          {rejectLabel}
        </button>
      </div>
      {error ? <ErrorBox error={error} title="The decision wasn't recorded" /> : null}
      {result ? (
        <div className="callout ok" role="status" style={{ marginTop: 10 }}>
          {result.message}
        </div>
      ) : null}
    </div>
  );
}

export function PendingDecision({ approval }: { approval: Approval }) {
  const what = approval.decision === "abort" ? "An abort request" : `A decision to ${approval.decision}`;
  return (
    <div className="callout" role="status">
      <strong>
        {what} was recorded ({approval.source}, {formatTime(approval.created_at)}).
      </strong>
      <p>The run acts on it when its engine reaches the gate.</p>
    </div>
  );
}

/** What to run when no engine is driving the run: decisions wait for `resume`. */
export function DecisionNote({ runId }: { runId: string }) {
  return (
    <p className="muted">
      No engine is running this run: once you decide, continue it with{" "}
      <code>codecoremorph resume {runId}</code> (a coding agent driving the run does this itself).
    </p>
  );
}
