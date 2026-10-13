// The verdict, drawn. The measured resolve rate sits on a 0–100% track with
// its 95% interval, and the break-even rate is a line across it. Where the
// band sits against the line is the whole decision: entirely past it is a
// confident "local pays", entirely short of it a confident "it doesn't", and
// straddling it is "not yet decided". Every number here is also written out in
// the text beside the chart, so the drawing never has to be read alone.
import { pct } from "@/lib/format";
import { useTween, useWidth } from "@/lib/motion";
import type { Rate } from "@/lib/types";
import { Tooltip } from "../ui/Tooltip";

const TRACK_Y = 44;
const TRACK_H = 24;
const H = 108;

export function VerdictChart({ rate, breakeven, breakevenNote }: { rate: Rate; breakeven: number | null; breakevenNote?: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const beTarget = breakeven == null ? -1 : breakeven;
  const [lo, point, hi, be] = useTween([rate.lo, rate.point, rate.hi, beTarget]);
  const pad = 8;
  const w = Math.max(0, width - pad * 2);
  const x = (p: number) => pad + Math.min(1, Math.max(0, p)) * w;
  const showBe = breakeven != null;
  const beOff = breakeven != null && breakeven > 1; // can't pay at any resolve rate
  const beX = x(Math.min(be, 1));
  // Labels share one row unless they'd touch; then the break-even moves down a row.
  const collide = showBe && Math.abs(beX - x(point)) < 120;
  const beLabelY = collide ? 30 : 14;
  const beAnchor = beX > width - 90 ? "end" : beX < 90 ? "start" : "middle";
  const pAnchor = x(point) > width - 60 ? "end" : x(point) < 60 ? "start" : "middle";

  return (
    <div ref={ref} className="vchart">
      {width > 0 && (
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={describe(rate, breakeven)}>
          {/* track: the whole 0–100% range */}
          <rect x={pad} y={TRACK_Y} width={w} height={TRACK_H} rx={TRACK_H / 2} fill="var(--mh-hover)" />
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={TRACK_Y + TRACK_H + 4} y2={TRACK_Y + TRACK_H + 8} stroke="var(--mh-line-strong)" />
              <text
                x={x(t)}
                y={H - 8}
                className="vchart__tick"
                textAnchor={t === 0 ? "start" : t === 1 ? "end" : "middle"}
              >
                {pct(t)}
              </text>
            </g>
          ))}
          {/* measured interval */}
          <rect
            x={x(lo)}
            y={TRACK_Y}
            width={Math.max(TRACK_H, x(hi) - x(lo))}
            height={TRACK_H}
            rx={TRACK_H / 2}
            fill="var(--mh-accent)"
            fillOpacity={0.14}
            stroke="var(--mh-accent)"
            strokeOpacity={0.75}
            strokeWidth={1.5}
          />
          {/* break-even */}
          {showBe && (
            <g className="vchart__be">
              <line x1={beX} x2={beX} y1={beLabelY + 6} y2={TRACK_Y + TRACK_H + 6} stroke="var(--mh-ink)" strokeWidth="2" />
              <text x={beX + (beAnchor === "start" ? 6 : beAnchor === "end" ? -6 : 0)} y={beLabelY} textAnchor={beAnchor} className="vchart__label vchart__label--be">
                {beOff ? "Break-even: above 100%" : `Break-even ${pct(be)}`}
              </text>
            </g>
          )}
          {/* point */}
          <circle cx={x(point)} cy={TRACK_Y + TRACK_H / 2} r={7} fill="var(--mh-accent)" stroke="var(--mh-bg)" strokeWidth="3" />
          <text x={x(point)} y={14} textAnchor={pAnchor} className="vchart__label">
            {`${pct(point)} measured`}
          </text>
        </svg>
      )}
      {/* Hover targets sit over the drawing; their content restates the text beside it. */}
      {width > 0 && (
        <div className="vchart__hits" aria-hidden>
          <Tooltip
            block
            content={`Measured ${pct(rate.point)} over ${rate.tasks} tasks. The 95% interval runs from ${pct(rate.lo)} to ${pct(rate.hi)}.`}
          >
            <span className="vchart__hit" style={{ left: x(rate.lo) - 4, width: Math.max(TRACK_H, x(rate.hi) - x(rate.lo)) + 8, top: TRACK_Y - 6, height: TRACK_H + 12 }} />
          </Tooltip>
          {breakeven != null && breakevenNote && (
            <Tooltip block content={breakevenNote}>
              <span className="vchart__hit" style={{ left: x(Math.min(breakeven, 1)) - 12, width: 24, top: 0, height: TRACK_Y + TRACK_H + 8 }} />
            </Tooltip>
          )}
        </div>
      )}
    </div>
  );
}

function describe(rate: Rate, breakeven: number | null): string {
  const m = `Measured resolve rate ${pct(rate.point)}, 95% interval ${pct(rate.lo)} to ${pct(rate.hi)}, over ${rate.tasks} tasks.`;
  if (breakeven == null) return m;
  return `${m} Break-even at ${breakeven > 1 ? "more than 100%" : pct(breakeven)}.`;
}
