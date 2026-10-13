// The one place this app talks to the engine. Every request goes through
// `request`, so what the app asks for is auditable here, and every failure
// carries the engine's own explanation instead of a bare status code.
import type {
  ArmsResponse,
  BenchDetail,
  BenchSummary,
  Engine,
  Health,
  Plan,
  RoutingAgent,
  RoutingDecision,
  RoutingStats,
  RunDetail,
  RunSummary,
  Verdict,
  VerdictRequest,
} from "./types";

export class EngineError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
  /** No response at all: the engine isn't running or isn't reachable. */
  get unreachable() {
    return this.status === 0;
  }
}

async function request<T>(path: string, init?: RequestInit & { signal?: AbortSignal }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new EngineError("The engine isn't reachable.", 0);
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      // FastAPI: { detail: "…" } or { detail: [{ msg }] } for validation errors.
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON; keep the status line */
    }
    throw new EngineError(message, res.status);
  }
  return (await res.json()) as T;
}

const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal });
const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body), signal });

export const api = {
  engine: (s?: AbortSignal) => get<Engine>("/api/engine", s),

  benches: (s?: AbortSignal) => get<{ benches: BenchSummary[] }>("/api/audit/benches", s),
  bench: (id: string, s?: AbortSignal) => get<BenchDetail>(`/api/audit/benches/${encodeURIComponent(id)}`, s),
  runs: (s?: AbortSignal) => get<{ runs: RunSummary[] }>("/api/audit/runs", s),
  run: (id: string, s?: AbortSignal) => get<RunDetail>(`/api/audit/runs/${encodeURIComponent(id)}`, s),
  arms: (s?: AbortSignal) => get<ArmsResponse>("/api/audit/arms", s),
  pricing: (s?: AbortSignal) => get<{ plans: Plan[] }>("/api/audit/pricing", s),
  verdict: (body: VerdictRequest, s?: AbortSignal) => post<Verdict>("/api/audit/verdict", body, s),

  health: (s?: AbortSignal) => get<Health>("/api/health", s),
  routingStats: (s?: AbortSignal) => get<RoutingStats>("/api/routing/stats", s),
  routingAgents: (s?: AbortSignal) => get<{ agents: RoutingAgent[] }>("/api/routing/agents", s),
  decisions: (limit: number, s?: AbortSignal) =>
    get<{ decisions: RoutingDecision[]; total_available: number }>(`/api/routing/decisions?limit=${limit}`, s),
};
