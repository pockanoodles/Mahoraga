import { AGENT_NODE_R, HUB, type AgentVital } from "../../hooks/usePulse";

interface AgentNodeProps {
  vital: AgentVital;
  x: number;
  y: number;
  activity: number; // 0..1 — recent activity intensity for sonar ring
}

// Visual hierarchy:
//   - Outer halo: UCB/reward weight. Fainter for weak arms, brighter for strong.
//   - Sonar ring: animates out when a particle lands here (activity > 0).
//   - Core: the agent itself.
//   - Label below: short model name.
export default function AgentNode({ vital, x, y, activity }: AgentNodeProps) {
  // Label: shorten "ollama:qwen3-4b" → "qwen3-4b"; keep short CLI names as-is.
  const short = vital.name.startsWith("ollama:") ? vital.name.slice(7) : vital.name;
  const haloR = 18 + vital.weight * 22;
  const haloOpacity = 0.05 + vital.weight * 0.18;

  // Angle from hub → node for label placement (outward).
  const dx = x - HUB.x;
  const dy = y - HUB.y;
  const angle = Math.atan2(dy, dx);
  const labelOffset = AGENT_NODE_R + 22;
  const labelX = x + Math.cos(angle) * labelOffset;
  const labelY = y + Math.sin(angle) * labelOffset;
  const anchor = Math.abs(dx) < 30 ? "middle" : dx > 0 ? "start" : "end";

  return (
    <g aria-label={vital.name}>
      {/* Reward halo — subtle, always-on */}
      <circle
        cx={x}
        cy={y}
        r={haloR}
        fill="hsl(var(--chart-1))"
        fillOpacity={haloOpacity}
      />

      {/* Sonar ring — expands outward when activity > 0 */}
      {activity > 0 && (
        <circle
          cx={x}
          cy={y}
          r={AGENT_NODE_R + activity * 34}
          fill="none"
          stroke="hsl(var(--chart-1))"
          strokeOpacity={0.6 * (1 - activity)}
          strokeWidth="1.5"
        />
      )}

      {/* Core circle */}
      <circle
        cx={x}
        cy={y}
        r={AGENT_NODE_R}
        fill={vital.healthy ? "hsl(var(--card))" : "hsl(var(--muted))"}
        stroke={vital.healthy ? "hsl(var(--chart-1))" : "hsl(var(--muted-foreground))"}
        strokeWidth="1.5"
      />
      {/* Inner dot — denotes weight */}
      <circle
        cx={x}
        cy={y}
        r={3 + vital.weight * 5}
        fill={vital.healthy ? "hsl(var(--chart-1))" : "hsl(var(--muted-foreground))"}
        fillOpacity={0.7 + vital.weight * 0.3}
      />

      {/* Label */}
      <text
        x={labelX}
        y={labelY}
        textAnchor={anchor}
        dominantBaseline="middle"
        fontSize="11"
        fontFamily="var(--font-mono)"
        fill="hsl(var(--muted-foreground))"
        opacity="0.85"
      >
        {short}
      </text>
    </g>
  );
}
