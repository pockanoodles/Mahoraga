import { HUB } from "../../hooks/usePulse";

interface ClassifierHubProps {
  intensity: number; // 0..1 — how much "traffic is flowing right now"
}

// A slow-pulsing core at the canvas center. Particles converge on it before
// routing out to the agents; its intensity tracks particle throughput so it
// feels alive even during idle ambient mode.
export default function ClassifierHub({ intensity }: ClassifierHubProps) {
  const scale = 0.85 + intensity * 0.35;
  return (
    <g transform={`translate(${HUB.x} ${HUB.y})`} aria-label="Classifier">
      {/* Outer faint ring — always-on ambient breath */}
      <circle r={36} fill="none" stroke="hsl(var(--chart-1))" strokeOpacity="0.18" strokeWidth="1">
        <animate attributeName="r" values="34;44;34" dur="4.2s" repeatCount="indefinite" />
        <animate attributeName="stroke-opacity" values="0.12;0.26;0.12" dur="4.2s" repeatCount="indefinite" />
      </circle>
      <circle r={24} fill="none" stroke="hsl(var(--chart-1))" strokeOpacity="0.3" strokeWidth="1.5">
        <animate attributeName="r" values="22;30;22" dur="3s" repeatCount="indefinite" />
      </circle>
      {/* Core */}
      <circle r={14 * scale} fill="hsl(var(--chart-1))" fillOpacity="0.85" />
      <circle r={9 * scale} fill="hsl(var(--background))" />
      <circle r={4 * scale} fill="hsl(var(--chart-1))" />
    </g>
  );
}
