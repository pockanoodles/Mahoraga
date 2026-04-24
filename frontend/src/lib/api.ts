// Thin fetch wrappers + typed response shapes for the Mahoraga REST surface.

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function postJson<T, B = unknown>(path: string, body: B): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

// ── Response shapes ────────────────────────────────────────────────────────

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

export interface AgentCapability {
  name: string;
  confidence: number;
}

export interface AgentStatus {
  name: string;
  worker_id: string;
  available: boolean;
  detail?: string | null;
  latency_ms?: number | null;
  rate_limited?: boolean;
  error?: string | null;
  capabilities: AgentCapability[];
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

export interface RoutingAgentsResponse {
  agents: RoutingAgent[];
}

// One row from /api/routing/decisions. `scores` is a stringified JSON map:
// { [agent]: { ucb: number; exploit: number; explore: number } }
// Outcome fields are null on rows logged before the result came back.
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

export interface RoutingDecisionsResponse {
  decisions: RoutingDecision[];
  total_available: number;
}

export interface DecisionScore {
  agent: string;
  ucb: number;
  exploit: number;
  explore: number;
}

export interface ParsedDecision {
  raw: RoutingDecision;
  candidates: DecisionScore[];
  topAgent: string; // highest UCB
  exploration: boolean; // selected !== top
}

export function parseDecision(d: RoutingDecision): ParsedDecision {
  // Guard against null/empty/non-string scores — older DB rows have any of these.
  let entries: [string, { ucb?: number; exploit?: number; explore?: number }][] = [];
  if (d.scores && typeof d.scores === "string") {
    const parsed = safeJsonParse<
      Record<string, { ucb: number; exploit: number; explore: number }> | null
    >(d.scores, null);
    if (parsed && typeof parsed === "object") {
      entries = Object.entries(parsed);
    }
  }
  const candidates: DecisionScore[] = entries
    .map(([agent, v]) => ({
      agent,
      ucb: typeof v?.ucb === "number" ? v.ucb : 0,
      exploit: typeof v?.exploit === "number" ? v.exploit : 0,
      explore: typeof v?.explore === "number" ? v.explore : 0,
    }))
    .sort((a, b) => b.ucb - a.ucb);
  const topAgent = candidates[0]?.agent ?? d.selected_agent;
  return {
    raw: d,
    candidates,
    topAgent,
    exploration: candidates.length > 0 && topAgent !== d.selected_agent,
  };
}

function safeJsonParse<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

// /logs/recent
export interface ChatLogEntry {
  id: string;
  user_message: string;
  assistant_response: string;
  worker_id: string;
  cost_usd: number;
  created_at: number; // seconds since epoch
}

export interface LogsResponse {
  entries: ChatLogEntry[];
}
