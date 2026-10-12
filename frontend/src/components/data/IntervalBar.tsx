// A rate and its 95% interval on a 0–100% track, small enough for a table
// row. One series, so no legend: the row's own text names it.
import { intervalText, pct } from "@/lib/format";
import type { Rate } from "@/lib/types";
import { Tooltip } from "../ui/Tooltip";

export function IntervalBar({ rate, width = 132, breakeven }: { rate: Rate; width?: number; breakeven?: number | null }) {
  const pad = 6;
  const w = width - pad * 2;
  const x = (p: number) => pad + Math.min(1, Math.max(0, p)) * w;
  const label = `${pct(rate.point)} resolved, 95% interval ${intervalText(rate)}, over ${rate.tasks} tasks`;
  return (
    <Tooltip
      content={
        <span className="tip-lines">
          <span>
            <strong>{pct(rate.point)}</strong> resolved over {rate.tasks} tasks ({rate.attempts} attempts).
          </span>
          <span>The 95% interval is {intervalText(rate)}. It counts tasks, not attempts, so only more tasks narrow it.</span>
        </span>
      }
    >
      <svg width={width} height={16} viewBox={`0 0 ${width} 16`} role="img" aria-label={label} className="ibar" tabIndex={0}>
        <line x1={pad} x2={pad + w} y1={8} y2={8} stroke="var(--mh-line-strong)" strokeWidth="1" />
        <rect x={x(rate.lo)} y={4} width={Math.max(2, x(rate.hi) - x(rate.lo))} height={8} rx={4} fill="var(--mh-accent-band)" />
        {breakeven != null && breakeven <= 1 && (
          <line x1={x(breakeven)} x2={x(breakeven)} y1={1} y2={15} stroke="var(--mh-ink)" strokeWidth="2" />
        )}
        <circle cx={x(rate.point)} cy={8} r={4} fill="var(--mh-accent)" stroke="var(--mh-bg)" strokeWidth="2" />
      </svg>
    </Tooltip>
  );
}
