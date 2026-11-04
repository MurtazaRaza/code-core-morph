import { describe, expect, it } from "vitest";

import { keys } from "./api";
import { staleKeys } from "./events";
import type { RunEvent } from "./types";

const event = (type: string, unit_id: string | null = null): RunEvent => ({
  id: 1,
  run_id: "R",
  ts: "2026-11-04T12:00:00+00:00",
  type,
  unit_id,
  attempt: null,
  data: {},
});

describe("staleKeys", () => {
  it("refreshes the run on every event", () => {
    expect(staleKeys("R", event("step.started"))).toEqual([keys.run("R")]);
  });

  it("refreshes the unit, the plan and the graph when a unit changes", () => {
    const stale = staleKeys("R", event("unit.status", "U-0002"));
    expect(stale).toContainEqual(keys.unit("R", "U-0002"));
    expect(stale).toContainEqual(keys.plan("R"));
    expect(stale).toContainEqual(keys.graph("R"));
  });

  it("refreshes the runs list when the run's status changes", () => {
    const stale = staleKeys("R", event("run.status"));
    expect(stale).toContainEqual(keys.runs);
    expect(stale).toContainEqual(keys.report("R"));
  });
});
