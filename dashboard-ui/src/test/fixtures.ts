import type {
  Attempt,
  AttemptView,
  PlanView,
  RunDetail,
  RunSummary,
  UnitView,
  WorkUnit,
} from "../types";

export const RUN_ID = "20261104-120000-ab12";
const T0 = "2026-11-04T12:00:00+00:00";

export function summary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: RUN_ID,
    status: "migrating",
    created_at: T0,
    updated_at: T0,
    base_commit: "0123456789abcdef0123456789abcdef01234567",
    branch: `codecoremorph/${RUN_ID}`,
    unit_counts: { committed: 1, pending: 2 },
    waiting: [],
    engine_alive: true,
    agent: "model",
    sandbox: "docker",
    ...overrides,
  };
}

export function detail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    ...summary(),
    language: "cpp",
    source_standard: "cpp11",
    target_standard: "cpp20",
    units_total: 3,
    units_done: 1,
    current_unit: "U-0002",
    activity: { ts: T0, message: "unit U-0002: build" },
    agent_waiting: null,
    budget: { tokens: 1200, cost_usd: 0.42, max_tokens: null, max_cost_usd: 50 },
    plan_ready: true,
    graph_ready: true,
    report_ready: false,
    events: [],
    last_event_id: 7,
    ...overrides,
  };
}

export function unit(id: string, seq: number, overrides: Partial<WorkUnit> = {}): WorkUnit {
  return {
    id,
    seq,
    rules: ["replace_typedefs_with_using"],
    files: ["include/geo.h"],
    reference_files: [],
    rule_sites: { "include/geo.h": 2 },
    flags: [],
    status: "pending",
    estimate: { context_tokens: 4000, expected_attempts: 0.9, tokens: 9000, cost_usd: 0.12 },
    note: null,
    ...overrides,
  };
}

export function planView(overrides: Partial<PlanView> = {}): PlanView {
  return {
    plan: {
      language: "cpp",
      source_standard: "cpp11",
      target_standard: "cpp20",
      base_commit: "0".repeat(40),
      rules: [
        {
          id: "replace_typedefs_with_using",
          kind: "api_preserving",
          automatic: false,
          sites: 4,
          files: 2,
          skipped_files: 0,
          units: 2,
        },
      ],
      graph: { files: 3, edges: 2, clusters: 2, cyclic_clusters: 0, largest_cluster: 2 },
      units: [unit("U-0001", 1), unit("U-0002", 2, { files: ["src/geo.cpp"] })],
      estimate: {
        model: "anthropic/claude-opus-5-5",
        context_window: 1000000,
        unit_token_limit: 500000,
        tokens: 18000,
        cost_usd: 0.24,
      },
      warnings: [],
    },
    gate_open: true,
    pending: null,
    ...overrides,
  };
}

export function attempt(overrides: Partial<Attempt> = {}): Attempt {
  return {
    run_id: RUN_ID,
    unit_id: "U-0001",
    number: 1,
    role: "editor",
    model: "anthropic/claude-opus-5-5",
    started_at: T0,
    finished_at: T0,
    outcome: "build_failed",
    changed_files: ["include/geo.h"],
    diff_lines: 4,
    diagnostics: [
      {
        file_path: "include/geo.h",
        line: 3,
        column: 9,
        severity: "error",
        code: null,
        message: "unknown type name 'Scalar'",
        notes: [],
        origin: "compiler",
      },
    ],
    regressions: ["gtest:Geo.Area"],
    invariant_results: [
      { invariant: "I-1", passed: true, severity: "error", message: "", needs_approval: false },
      {
        invariant: "I-6",
        passed: false,
        severity: "error",
        message: "the public API changed",
        needs_approval: true,
      },
    ],
    summary: null,
    review_notes: [],
    ...overrides,
  };
}

export function unitView(overrides: Partial<UnitView> = {}): UnitView {
  return {
    unit: unit("U-0001", 1, { status: "awaiting_approval" }),
    result: {
      unit_id: "U-0001",
      status: "awaiting_approval",
      commit: "fedcba9876543210fedcba9876543210fedcba98",
      attempts: 1,
      agent_skipped: false,
      models: ["anthropic/claude-opus-5-5"],
      summary: "Replaced the typedefs.",
      reason: null,
      review_notes: [],
      warnings: [],
      approvals: ["it changes the public header include/geo.h"],
      approved_by: null,
      changed_files: ["include/geo.h"],
      test_files_changed: [],
      codemod_files: [],
      remaining_sites: {},
      file_warnings: {},
      started_at: T0,
      finished_at: T0,
      duration_s: 42,
    },
    attempts: [attempt()],
    attempt_files: [{ number: 1, files: ["diff.patch", "feedback.md"] }],
    gate: {
      waiting: true,
      reasons: ["it changes the public header include/geo.h"],
      commit: "fedcba9876543210fedcba9876543210fedcba98",
      pending: null,
    },
    ...overrides,
  };
}

export const PATCH = [
  "diff --git a/include/geo.h b/include/geo.h",
  "--- a/include/geo.h",
  "+++ b/include/geo.h",
  "@@ -1,3 +1,3 @@",
  " #pragma once",
  "-typedef double Scalar;",
  "+using Scalar = double;",
  " int f();",
  "",
].join("\n");

export function attemptView(overrides: Partial<AttemptView> = {}): AttemptView {
  return {
    unit_id: "U-0001",
    number: 1,
    attempt: attempt(),
    diff: { name: "diff.patch", text: PATCH, size: PATCH.length, truncated: false },
    feedback: { name: "feedback.md", text: "The build failed.", size: 17, truncated: false },
    build_log: null,
    test_log: null,
    transcript: [
      { role: "user", content: [{ type: "text", text: "Convert the typedefs." }] },
      {
        role: "assistant",
        content: [{ type: "tool_call", name: "submit_edits", arguments: { edits: [] } }],
        model: "anthropic/claude-opus-5-5",
      },
    ],
    transcript_truncated: false,
    files: ["diff.patch", "feedback.md"],
    ...overrides,
  };
}
