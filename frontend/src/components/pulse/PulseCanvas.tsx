import { useMemo } from "react";
import {
  agentPosition,
  CANVAS,
  PULSE_AGENTS,
  stageOf,
  usePulse,
} from "../../hooks/usePulse";
import AgentNode from "./AgentNode";
import ClassifierHub from "./ClassifierHub";
import Particle from "./Particle";

export default function PulseCanvas() {
  const { particles, agents, now, liveCount, totalDecisions } = usePulse();

  const agentPositions = useMemo(() => {
    const map: Record<string, { x: number; y: number; index: number }> = {};
    PULSE_AGENTS.forEach((name, i) => {
      const pos = agentPosition(i, PULSE_AGENTS.length);
      map[name] = { ...pos, index: i };
    });
    return map;
  }, []);

  // Activity intensity per agent — decays over SONAR_WINDOW_MS after a hit.
  const agentActivity = useMemo(() => {
    const by: Record<string, number> = {};
    for (const p of particles) {
      const { stage, t } = stageOf(p, now);
      // Count as "landing" during process + early return
      if (stage === "process") {
        by[p.agent] = Math.max(by[p.agent] ?? 0, t);
      } else if (stage === "return" && t < 0.3) {
        by[p.agent] = Math.max(by[p.agent] ?? 0, t + 0.7);
      }
    }
    return by;
  }, [particles, now]);

  // Hub intensity — how many particles are currently in drop or route.
  const hubIntensity = useMemo(() => {
    let n = 0;
    for (const p of particles) {
      const s = stageOf(p, now).stage;
      if (s === "drop" || s === "route") n++;
    }
    return Math.min(1, n / 3);
  }, [particles, now]);

  // Fallback vital info for agents if /api/routing/agents hasn't responded yet.
  const vitalsByName = useMemo(() => {
    const map: Record<string, (typeof agents)[number]> = {};
    for (const a of agents) map[a.name] = a;
    return map;
  }, [agents]);

  return (
    <div
      className="relative h-full w-full overflow-hidden bg-background"
      style={{
        backgroundImage:
          "radial-gradient(circle at 50% 45%, hsl(var(--muted) / 0.6) 0%, hsl(var(--background)) 65%)",
      }}
    >
      {/* Overlay: top-left title + top-right stats */}
      <div className="pointer-events-none absolute left-6 top-6 z-10">
        <div className="font-heading text-[18px] font-semibold text-foreground">Pulse</div>
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {liveCount > 0 ? "live" : "ambient"} · {totalDecisions.toLocaleString()} decisions
        </div>
      </div>
      <div className="pointer-events-none absolute right-6 top-6 z-10 text-right">
        <div className="flex items-center justify-end gap-2 font-mono text-[11px] text-muted-foreground">
          <span className="inline-block h-2 w-2 rounded-full bg-chart-1" /> exploit
          <span className="inline-block h-2 w-2 rounded-full bg-chart-5" /> explore
          <span className="inline-block h-2 w-2 rounded-full bg-chart-3" /> pass
          <span className="inline-block h-2 w-2 rounded-full bg-destructive" /> fail
        </div>
      </div>

      <svg
        viewBox={`0 0 ${CANVAS.w} ${CANVAS.h}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full"
        role="img"
        aria-label="Mahoraga routing pulse"
      >
        {/* Spoke lines — hub to each agent, soft guide */}
        {PULSE_AGENTS.map((name) => {
          const pos = agentPositions[name];
          return (
            <line
              key={`spoke-${name}`}
              x1={500}
              y1={360}
              x2={pos.x}
              y2={pos.y}
              stroke="hsl(var(--border))"
              strokeOpacity="0.4"
              strokeDasharray="2 6"
              strokeWidth="0.8"
            />
          );
        })}

        {/* Agent nodes */}
        {PULSE_AGENTS.map((name) => {
          const pos = agentPositions[name];
          const vital = vitalsByName[name] ?? {
            name,
            weight: 0.3,
            totalTasks: 0,
            healthy: true,
            lastActiveAt: 0,
          };
          return (
            <AgentNode
              key={`agent-${name}`}
              vital={vital}
              x={pos.x}
              y={pos.y}
              activity={agentActivity[name] ?? 0}
            />
          );
        })}

        {/* Classifier hub (above particles so it's always on top in center) */}
        <ClassifierHub intensity={hubIntensity} />

        {/* Particles */}
        {particles.map((p) => (
          <Particle
            key={p.id}
            p={p}
            now={now}
            agentPos={agentPositions[p.agent]}
          />
        ))}
      </svg>
    </div>
  );
}
