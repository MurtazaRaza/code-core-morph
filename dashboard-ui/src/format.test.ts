import { describe, expect, it } from "vitest";

import { describeEvent, formatDuration, statusLabel, statusTone, timeAgo } from "./format";

describe("format", () => {
  it("labels and colors statuses", () => {
    expect(statusLabel("awaiting_plan_approval")).toBe("waiting for plan approval");
    expect(statusLabel("in_progress")).toBe("in progress");
    expect(statusTone("committed")).toBe("ok");
    expect(statusTone("failed")).toBe("bad");
    expect(statusTone("awaiting_approval")).toBe("wait");
    expect(statusTone("migrating")).toBe("busy");
  });

  it("says how long ago and how long", () => {
    const now = Date.parse("2026-11-04T12:10:00Z");
    expect(timeAgo("2026-11-04T12:09:30Z", now)).toBe("30 s ago");
    expect(timeAgo("2026-11-04T11:11:00Z", now)).toBe("59 min ago");
    expect(timeAgo("2026-11-04T11:10:00Z", now)).toBe("1 h ago");
    expect(formatDuration(5.25)).toBe("5.3 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(formatDuration(7260)).toBe("2 h 1 min");
  });

  it("describes events", () => {
    expect(describeEvent("step.started", { message: "final verification" })).toBe("final verification");
    expect(describeEvent("gate.decided", { gate: "plan", decision: "approve", source: "dashboard" })).toBe(
      "plan gate: approve (dashboard)",
    );
    expect(describeEvent("run.status", { status: "completed" })).toBe("run completed");
  });
});
