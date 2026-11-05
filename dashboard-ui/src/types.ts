// The dashboard API's JSON (src/codecoremorph/dashboard/models.py and the core models it
// returns). Only the fields the UI reads are listed.

export type RunStatus =
  | "created"
  | "provisioning"
  | "indexing"
  | "baselining"
  | "characterizing"
  | "planning"
  | "awaiting_plan_approval"
  | "migrating"
  | "verifying"
  | "completed"
  | "completed_with_failures"
  | "budget_exhausted"
  | "aborted"
  | "rejected"
  | "failed";

export const TERMINAL_RUN_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "completed",
  "completed_with_failures",
  "budget_exhausted",
  "aborted",
  "rejected",
  "failed",
]);

export type UnitStatus =
  | "pending"
  | "in_progress"
  | "awaiting_approval"
  | "committed"
  | "failed"
  | "skipped"
  | "too_large"
  | "excluded";

export const UNIT_STATUSES: readonly UnitStatus[] = [
  "pending",
  "in_progress",
  "awaiting_approval",
  "committed",
  "failed",
  "skipped",
  "too_large",
  "excluded",
];

export interface SessionInfo {
  project: string;
  repository: string;
  config_path: string;
  version: string;
}

export interface RunSummary {
  id: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  base_commit: string | null;
  branch: string;
  unit_counts: Record<string, number>;
  waiting: string[];
  engine_alive: boolean;
  agent: string | null;
  sandbox: string | null;
}

export interface RunEvent {
  id: number;
  run_id: string;
  ts: string;
  type: string;
  unit_id: string | null;
  attempt: number | null;
  data: Record<string, unknown>;
}

export interface Budget {
  tokens: number;
  cost_usd: number;
  max_tokens: number | null;
  max_cost_usd: number | null;
}

export interface RunDetail extends RunSummary {
  language: string | null;
  source_standard: string | null;
  target_standard: string | null;
  units_total: number;
  units_done: number;
  current_unit: string | null;
  activity: { ts: string; message: string } | null;
  agent_waiting: Record<string, unknown> | null;
  budget: Budget;
  plan_ready: boolean;
  graph_ready: boolean;
  report_ready: boolean;
  events: RunEvent[];
  last_event_id: number;
}

export interface UnitEstimate {
  context_tokens: number;
  expected_attempts: number;
  tokens: number;
  cost_usd: number | null;
}

// A module port's unit (D-58): where its code goes, and the C++ that keeps using it.
export interface PortUnit {
  build_target: string;
  crate: string;
  module: string;
  callers: string[];
  c_headers: string[];
}

export interface WorkUnit {
  id: string;
  seq: number;
  rules: string[];
  files: string[];
  reference_files: string[];
  rule_sites: Record<string, number>;
  flags: string[];
  status: UnitStatus;
  estimate: UnitEstimate | null;
  note: string | null;
  port?: PortUnit | null;
}

export interface PortFile {
  path: string;
  text: string;
  truncated: boolean;
}

// The two sides of a module port's unit: its C++ before it, the code it committed.
export interface PortView {
  unit_id: string;
  crate: string;
  module: string;
  build_target: string;
  before: string | null;
  after: string | null;
  sources: PortFile[];
  targets: PortFile[];
}

export interface PlanRule {
  id: string;
  kind: string;
  automatic: boolean;
  sites: number | null;
  files: number;
  skipped_files: number;
  units: number;
}

export interface Plan {
  language: string | null;
  source_standard: string | null;
  target_standard: string | null;
  base_commit: string | null;
  rules: PlanRule[];
  graph: {
    files: number;
    edges: number;
    clusters: number;
    cyclic_clusters: number;
    largest_cluster: number;
  } | null;
  units: WorkUnit[];
  estimate: {
    model: string;
    context_window: number | null;
    unit_token_limit: number;
    tokens: number;
    cost_usd: number | null;
  } | null;
  warnings: string[];
}

export interface Approval {
  id: number | null;
  gate: "plan" | "unit" | "run";
  unit_id: string | null;
  decision: "approve" | "reject" | "abort";
  reason: string | null;
  exclude: string[];
  source: string;
  created_at: string;
  consumed_at: string | null;
}

export interface PlanView {
  plan: Plan;
  gate_open: boolean;
  pending: Approval | null;
}

export interface GraphFile {
  path: string;
  cluster: number;
  units: string[];
  status: UnitStatus | null;
}

export interface GraphUnit {
  id: string;
  seq: number;
  status: UnitStatus;
  rules: string[];
  files: string[];
}

export interface GraphView {
  files: GraphFile[];
  edges: [string, string][];
  units: GraphUnit[];
  unit_edges: [string, string][];
}

export interface Diagnostic {
  file_path: string | null;
  line: number | null;
  column: number | null;
  severity: "error" | "warning" | "note";
  code: string | null;
  message: string;
  notes: Diagnostic[];
  origin: string;
}

export interface InvariantResult {
  invariant: string;
  passed: boolean;
  severity: "error" | "warning" | "note";
  message: string;
  needs_approval: boolean;
}

export interface Attempt {
  run_id: string;
  unit_id: string;
  number: number;
  role: string;
  model: string | null;
  started_at: string;
  finished_at: string | null;
  outcome: string | null;
  changed_files: string[];
  diff_lines: number | null;
  diagnostics: Diagnostic[];
  regressions: string[];
  invariant_results: InvariantResult[];
  summary: string | null;
  review_notes: string[];
}

export interface UnitResult {
  unit_id: string;
  status: UnitStatus;
  commit: string | null;
  attempts: number;
  agent_skipped: boolean;
  models: string[];
  summary: string | null;
  reason: string | null;
  review_notes: string[];
  warnings: string[];
  approvals: string[];
  approved_by: string | null;
  changed_files: string[];
  test_files_changed: string[];
  codemod_files: string[];
  remaining_sites: Record<string, number>;
  file_warnings: Record<string, number>;
  started_at: string;
  finished_at: string;
  duration_s: number;
}

export interface UnitGate {
  waiting: boolean;
  reasons: string[];
  commit: string | null;
  pending: Approval | null;
}

export interface UnitView {
  unit: WorkUnit;
  result: UnitResult | null;
  attempts: Attempt[];
  attempt_files: { number: number; files: string[] }[];
  gate: UnitGate;
}

export interface TextFile {
  name: string;
  text: string;
  size: number;
  truncated: boolean;
}

export interface TranscriptBlock {
  type: "text" | "tool_call" | "tool_result" | "opaque";
  text?: string | null;
  name?: string;
  arguments?: Record<string, unknown>;
  content?: string;
  is_error?: boolean;
  kind?: string;
}

export interface TranscriptEntry {
  role: "user" | "assistant";
  content: TranscriptBlock[];
  usage?: Record<string, number>;
  model?: string;
}

export interface AttemptView {
  unit_id: string;
  number: number;
  attempt: Attempt | null;
  diff: TextFile | null;
  feedback: TextFile | null;
  build_log: TextFile | null;
  test_log: TextFile | null;
  transcript: TranscriptEntry[] | null;
  transcript_truncated: boolean;
  files: string[];
}

export interface ActionResult {
  run_id: string;
  approval_id: number | null;
  engine_alive: boolean;
  message: string;
}

export interface UsageTotals {
  calls: number;
  tokens: number;
  cost_usd: number;
  latency_s: number;
}

export interface RunReport {
  run_id: string;
  project: string;
  status: RunStatus;
  reason: string | null;
  language: string;
  source_standard: string;
  target_standard: string;
  base_commit: string;
  branch: string;
  head: string | null;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  unit_counts: Record<string, number>;
  units: UnitResult[];
  unit_notes: Record<string, string>;
  rules: {
    id: string;
    units: number;
    committed: number;
    planned_sites: number;
    converted_sites: number;
  }[];
  baseline_tests: Record<string, number>;
  excused_tests: string[];
  plan_warnings: string[];
  final: {
    ran: boolean;
    passed: boolean;
    commit: string | null;
    clean: boolean;
    build_errors: string[];
    tests_run: number;
    regressions: string[];
    reason: string | null;
    duration_s: number;
  } | null;
  usage_total: UsageTotals;
  usage_by_role: Record<string, UsageTotals>;
  providers: string[];
  review: { kind: string; unit_id: string | null; text: string }[];
}
