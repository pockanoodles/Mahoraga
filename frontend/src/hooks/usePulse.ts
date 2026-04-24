import { useEffect, useRef, useState } from "react";
import {
  getJson,
  parseDecision,
  type RoutingAgentsResponse,
  type RoutingDecisionsResponse,
} from "../lib/api";

// The nine agents rendered in the Pulse hub-and-spoke. Order = visual layout.
// First entry is at the top, moving clockwise.
export const PULSE_AGENTS = [
  "ollama:qwen3-4b",
  "ollama:gemma4-e4b",
  "ollama:deepseek-r1",
  "ollama:lfm2",
  "codex-cli",
  "aider",
  "gemini-cli",
  "goose",
  "opencode",
] as const;

export type PulseAgentName = (typeof PULSE_AGENTS)[number];
export type PulseStage = "drop" | "route" | "process" | "return" | "gone";
export type PulseKind = "live" | "ambient";

export interface PulseParticle {
  id: string;
  kind: PulseKind;
  bucket: string;
  agent: PulseAgentName;
  exploration: boolean;
  success: boolean | null;
  spawnedAt: number;
  dropX: number; // horizontal spawn offset near the hub
}

export interface AgentVital {
  name: PulseAgentName;
  weight: number; // 0..1, used for halo size and ambient spawn weighting
  totalTasks: number;
  healthy: boolean;
  lastActiveAt: number; // ms since epoch, for warm glow
}

// Per-stage duration in ms. Total lifetime of a particle = sum of these.
export const STAGE_MS: Record<Exclude<PulseStage, "gone">, number> = {
  drop: 1400,
  route: 1200,
  process: 900,
  return: 1300,
};
export const TOTAL_LIFE_MS = STAGE_MS.drop + STAGE_MS.route + STAGE_MS.process + STAGE_MS.return;

// Given a particle and a timestamp, what stage is it in and how far through.
export function stageOf(p: PulseParticle, now: number): { stage: PulseStage; t: number } {
  const elapsed = now - p.spawnedAt;
  let acc = 0;
  const order: (keyof typeof STAGE_MS)[] = ["drop", "route", "process", "return"];
  for (const s of order) {
    const dur = STAGE_MS[s];
    if (elapsed < acc + dur) {
      return { stage: s, t: (elapsed - acc) / dur };
    }
    acc += dur;
  }
  return { stage: "gone", t: 1 };
}

interface UsePulseResult {
  particles: PulseParticle[];
  agents: AgentVital[];
  now: number;
  liveCount: number;
  totalDecisions: number;
}

const POLL_DECISIONS_MS = 4000;
const POLL_AGENTS_MS = 10000;
const AMBIENT_SPAWN_MS = 2500;
const FRAME_MS = 50; // ~20fps state update; visual interpolation inside components

function buildParticleFromDecision(
  d: ReturnType<typeof parseDecision>,
  seq: number
): PulseParticle | null {
  const agent = d.raw.selected_agent as PulseAgentName;
  if (!PULSE_AGENTS.includes(agent)) return null;
  return {
    id: `live-${d.raw.id}-${seq}`,
    kind: "live",
    bucket: "general", // task_goal → bucket classification isn't in the decision payload yet
    agent,
    exploration: d.exploration,
    success:
      d.raw.success === 1 ? true : d.raw.success === 0 ? false : null,
    spawnedAt: Date.now(),
    dropX: (Math.random() - 0.5) * 220,
  };
}

function buildAmbientParticle(agents: AgentVital[]): PulseParticle | null {
  if (agents.length === 0) return null;
  // Weight ambient spawn by avg_reward so phantom particles reflect
  // the bandit's learned distribution, not uniform noise.
  const weights = agents.map((a) => Math.max(0.15, a.weight));
  const total = weights.reduce((s, w) => s + w, 0);
  let r = Math.random() * total;
  let pick = agents[0];
  for (let i = 0; i < agents.length; i++) {
    r -= weights[i];
    if (r <= 0) {
      pick = agents[i];
      break;
    }
  }
  return {
    id: `amb-${Math.random().toString(36).slice(2, 10)}`,
    kind: "ambient",
    bucket: "general",
    agent: pick.name,
    exploration: Math.random() < 0.18,
    success: Math.random() < 0.82,
    spawnedAt: Date.now(),
    dropX: (Math.random() - 0.5) * 260,
  };
}

export function usePulse(): UsePulseResult {
  const [particles, setParticles] = useState<PulseParticle[]>([]);
  const [agents, setAgents] = useState<AgentVital[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [totalDecisions, setTotalDecisions] = useState(0);
  const seenIds = useRef<Set<number>>(new Set());
  const firstPollRef = useRef(true);

  // 1. Poll routing decisions; emit a particle for each new id after the first poll.
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const data = await getJson<RoutingDecisionsResponse>(
          "/api/routing/decisions?limit=20"
        );
        if (cancelled) return;
        setTotalDecisions(data.total_available);
        const fresh: PulseParticle[] = [];
        let seq = 0;
        for (const raw of data.decisions) {
          if (seenIds.current.has(raw.id)) continue;
          seenIds.current.add(raw.id);
          if (firstPollRef.current) continue; // don't flood on page load
          const particle = buildParticleFromDecision(parseDecision(raw), seq++);
          if (particle) fresh.push(particle);
        }
        if (fresh.length > 0) {
          setParticles((prev) => [...prev, ...fresh]);
        }
        firstPollRef.current = false;
      } catch {
        /* swallow */
      }
    };
    void run();
    const id = window.setInterval(run, POLL_DECISIONS_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // 2. Poll agent stats for halo weights.
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const data = await getJson<RoutingAgentsResponse>("/api/routing/agents");
        if (cancelled) return;
        const byName = new Map(data.agents.map((a) => [a.name, a]));
        const out: AgentVital[] = PULSE_AGENTS.map((name) => {
          const live = byName.get(name);
          return {
            name,
            weight: live ? Math.max(0, Math.min(1, live.avg_reward ?? 0)) : 0,
            totalTasks: live?.total ?? 0,
            healthy: live?.healthy ?? false,
            lastActiveAt: 0,
          };
        });
        setAgents(out);
      } catch {
        /* swallow */
      }
    };
    void run();
    const id = window.setInterval(run, POLL_AGENTS_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // 3. Ambient spawner — fires if live traffic is quiet.
  useEffect(() => {
    const id = window.setInterval(() => {
      setParticles((prev) => {
        const activeLive = prev.filter(
          (p) => p.kind === "live" && stageOf(p, Date.now()).stage !== "gone"
        ).length;
        if (activeLive >= 2) return prev; // real traffic takes the stage
        if (agents.length === 0) return prev;
        const p = buildAmbientParticle(agents);
        return p ? [...prev, p] : prev;
      });
    }, AMBIENT_SPAWN_MS);
    return () => window.clearInterval(id);
  }, [agents]);

  // 4. Frame tick + cull expired particles.
  useEffect(() => {
    const id = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      setParticles((prev) => prev.filter((p) => t - p.spawnedAt < TOTAL_LIFE_MS + 200));
    }, FRAME_MS);
    return () => window.clearInterval(id);
  }, []);

  const liveCount = particles.filter((p) => p.kind === "live").length;

  return { particles, agents, now, liveCount, totalDecisions };
}

// ── Geometry helpers ────────────────────────────────────────────────────────

export const CANVAS = { w: 1000, h: 700 };
export const HUB = { x: 500, y: 360 };
export const AGENT_RADIUS = 270;
export const AGENT_NODE_R = 14;

export function agentPosition(index: number, total: number): { x: number; y: number } {
  // Evenly spaced circle, top = index 0 (12 o'clock).
  const deg = -90 + index * (360 / total);
  const rad = (deg * Math.PI) / 180;
  return {
    x: HUB.x + AGENT_RADIUS * Math.cos(rad),
    y: HUB.y + AGENT_RADIUS * Math.sin(rad),
  };
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}
