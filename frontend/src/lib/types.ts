// The engine's HTTP contract, as the app reads it. The engine is headless and
// this app is one client of it; the CLI and the MCP bridge are others. Shapes
// here mirror backend/orchestrator/service/audit_api.py exactly.

/** A task-weighted resolve rate with its 95% Wilson interval over tasks. */
export interface Rate {
  point: number;
  lo: number;
  hi: number;
  tasks: number;
  attempts: number;
}

export interface Engine {
  name: string;
  version: string;
  api: number;
  headless: boolean;
  ui: boolean;
  demo: boolean;
  bench_root: string;
  data_dir: string;
  python: string;
  platform: string;
  ollama: { url: string; up: boolean; version: string | null };
}

export interface BenchSummary {
  id: string;
  name: string;
  repo: string | null;
  tasks: number;
  dropped: number;
  attempts: number;
  degraded: number;
  arms: string[];
  conds: string[];
  runs: number;
  last_activity: string | null;
}

export interface Task {
  sha: string;
  subject: string;
  body: string;
  gold_src: string[];
  gold_lines: number;
  test_files: string[];
  /** Counts, not lists: fail-to-pass tests that must pass, pass-to-pass that must stay. */
  f2p: number;
  p2p: number;
}

export interface PrecisionPlan {
  target_half_width: number;
  tasks_needed: number;
  tasks_have: number;
  attempts_needed: number;
  nights: number;
  text: string;
}

export interface ArmSummary {
  arm: string;
  cond: string;
  n: number;
  resolved: number;
  tasks: number;
  tasks_solved_ever: number;
  tasks_solved_always: number;
  repeats: number;
  rate: Rate;
  median_minutes: number;
  outcomes: Record<string, number>;
  broke_other_code: number;
  touched_tests: number;
  multi_file_resolved: number;
  multi_file_n: number;
  tier: string;
  plan: PrecisionPlan;
}

export interface RunSummary {
  run_id: string;
  bench_id: string;
  repo_name: string;
  started_at: string;
  ended_at: string | null;
  stopped: string | null;
  attempts: number | null;
  arms: string[];
  machine: { model: string | null; memory_gb: number | null; os: string | null };
  harness: { commit: string | null; dirty: boolean | null };
  ollama: string | null;
}

export interface BenchDetail {
  id: string;
  name: string;
  repo: string | null;
  config: { python: string; pytest_args: string[] } | null;
  tasks: Task[];
  dropped: { sha: string; reason: string }[];
  arms: ArmSummary[];
  /** sha → "<arm> / <cond>" → one outcome per repeat. */
  matrix: Record<string, Record<string, string[]>>;
  degraded_note: string | null;
  runs: RunSummary[];
}

export interface Attempt {
  sha: string;
  arm: string;
  cond: string;
  resolved: boolean;
  f2p_pass: number;
  f2p_total: number;
  regressions: number;
  regression_ids: string[];
  suite_crashed: boolean;
  edited_files: string[];
  touched_tests: string[];
  hit_gold_file: boolean;
  secs: number;
  timed_out: boolean;
  llm_calls: number;
  tokens_sent: number;
  tokens_recv: number;
  extra: Record<string, number>;
  rep: number;
  degraded: string[];
  tier: string;
  run_id: string;
  outcome: string;
  subject: string;
}

export interface RunDetail {
  run: RunSummary;
  manifest: Record<string, unknown>;
  attempts: Attempt[];
}

export interface ArmPooled {
  arm: string;
  agent: string;
  model: string;
  benches: number;
  attempts: number;
  conds: string[];
  rate: Rate | null;
}

export interface ArmsResponse {
  arms: ArmPooled[];
  models: { name: string; digest: string | null }[];
  agents: { name: string; on_path: boolean }[];
}

export interface Plan {
  id: string;
  vendor: string;
  name: string;
  kind: string;
  usd_per_month: number | null;
  capacity_vs_pro: number | null;
  as_of: string;
  source: string;
  verified: boolean;
  note: string;
}

export type Billing =
  | { kind: "api"; monthly_usd: number }
  | { kind: "subscription"; plan: string; plan_price: number | null };

export interface Assumptions {
  tasks_per_month: number;
  deferrable_share?: number;
  triage_minutes?: number;
  hourly_usd?: number;
  watts?: number;
  usd_per_kwh?: number;
  usage_of_cap?: number;
}

export interface VerdictRequest {
  benches: string[] | null;
  arm: string;
  cond: string;
  billing: Billing;
  assumptions: Assumptions;
}

export interface Evidence {
  rate: Rate;
  minutes_per_attempt: number;
  tier: string;
  label: string;
  hardware: string;
}

export type Call = "stay" | "split" | "switch";

export interface Verdict {
  call: Call;
  confident: boolean;
  reason: string;
  evidence: Evidence;
  assumptions: Required<Assumptions> & { lines: string[] };
  billing: { kind: "api" | "subscription"; monthly_usd: number; plan: Plan | null; lower: Plan | null };
  monthly_usd: { lo: number; point: number; hi: number } | null;
  /** API billing only: the resolve rate at which routing to local pays. */
  breakeven: number | null;
  sensitivity: string[];
  rendered: string;
}

// ── The router (the last stage). These endpoints predate the audit. ─────────

export interface Health {
  status: string;
  uptime_s: number;
  agents_registered: number;
  agents_online: number;
  strategy: string;
  total_decisions: number;
}

export interface RoutingStats {
  strategy: string;
  total_decisions: number;
  stats: {
    total: number;
    successes: number;
    success_rate: number;
    avg_latency: number;
    avg_cost: number;
    total_cost: number;
    avg_reward: number;
    total_reward: number;
  };
}

export interface RoutingAgent {
  name: string;
  healthy: boolean;
  detail?: string;
  capabilities: string[];
  total: number;
  successes: number;
  success_rate: number;
  avg_latency: number;
  avg_cost: number;
  total_cost: number;
  avg_reward: number;
  total_reward: number;
}

export interface RoutingDecision {
  id: number;
  timestamp: string;
  task_id: string | null;
  task_goal: string;
  strategy: string;
  selected_agent: string;
  scores: string | null;
  success: number | null;
  latency_s: number | null;
  reward: number | null;
  error_message: string | null;
}
