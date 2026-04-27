import {
  easeInOut,
  HUB,
  lerp,
  stageOf,
  type PulseParticle,
  type PulseStage,
} from "../../hooks/usePulse";

interface ParticleProps {
  p: PulseParticle;
  now: number;
  agentPos: { x: number; y: number };
}

// Returns the absolute SVG position + opacity + radius for a particle in its
// current stage. Interpolates smoothly along straight lines between:
//   drop   → (spawnX, top) to HUB
//   route  → HUB to agent
//   process → rests at agent (slight bob)
//   return  → agent back toward HUB, fading
function positionFor(
  stage: PulseStage,
  t: number,
  spawnX: number,
  agentX: number,
  agentY: number
): { x: number; y: number; opacity: number; r: number } {
  const e = easeInOut(t);
  const topY = -30;
  switch (stage) {
    case "drop": {
      return {
        x: lerp(HUB.x + spawnX, HUB.x, e),
        y: lerp(topY, HUB.y, e),
        opacity: Math.min(1, t * 3),
        r: 3.5,
      };
    }
    case "route": {
      return {
        x: lerp(HUB.x, agentX, e),
        y: lerp(HUB.y, agentY, e),
        opacity: 1,
        r: 3.5,
      };
    }
    case "process": {
      // Small bob at agent node while "working"
      const bob = Math.sin(t * Math.PI) * 2;
      return {
        x: agentX + bob,
        y: agentY,
        opacity: 1,
        r: 4.5 + Math.sin(t * Math.PI) * 2,
      };
    }
    case "return": {
      return {
        x: lerp(agentX, HUB.x, e),
        y: lerp(agentY, HUB.y, e),
        opacity: 1 - e,
        r: 3,
      };
    }
    default:
      return { x: 0, y: 0, opacity: 0, r: 0 };
  }
}

export default function Particle({ p, now, agentPos }: ParticleProps) {
  const { stage, t } = stageOf(p, now);
  if (stage === "gone") return null;

  const pos = positionFor(stage, t, p.dropX, agentPos.x, agentPos.y);

  // Color choice:
  //   - drop & route stages: amber for EXPLORE, teal for EXPLOIT.
  //   - return stage: green if success, red if fail, muted if unknown.
  let fill: string;
  if (stage === "return") {
    if (p.success === true) fill = "hsl(var(--chart-3))"; // mint = pass
    else if (p.success === false) fill = "hsl(var(--destructive))";
    else fill = "hsl(var(--muted-foreground))";
  } else if (p.exploration) {
    fill = "hsl(var(--chart-5))"; // amber
  } else {
    fill = "hsl(var(--chart-1))"; // teal
  }

  const finalOpacity = pos.opacity * (p.kind === "ambient" ? 0.55 : 1);

  return (
    <g>
      {/* Glow aura */}
      <circle
        cx={pos.x}
        cy={pos.y}
        r={pos.r * 2.4}
        fill={fill}
        fillOpacity={finalOpacity * 0.15}
      />
      {/* Core */}
      <circle
        cx={pos.x}
        cy={pos.y}
        r={pos.r}
        fill={fill}
        fillOpacity={finalOpacity}
      />
    </g>
  );
}
