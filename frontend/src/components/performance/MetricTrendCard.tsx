import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import type { Metric, SeriesPoint } from "../../lib/series";
import { cn } from "../../lib/cn";

interface MetricTrendCardProps {
  label: string;
  metric: Metric;
  data: SeriesPoint[];
  current: number | null;
  delta: number | null;
  // direction: for latency, down is good; for success/reward, up is good.
  directionGood: "up" | "down";
  active?: boolean;
  onClick?: () => void;
}

function formatValue(metric: Metric, v: number | null): string {
  if (v === null) return "—";
  if (metric === "success") return `${(v * 100).toFixed(1)}%`;
  if (metric === "reward") return v.toFixed(3);
  return `${v.toFixed(2)}s`;
}

function formatDelta(metric: Metric, d: number | null): string {
  if (d === null || Math.abs(d) < 1e-6) return "±0";
  const sign = d > 0 ? "+" : "";
  if (metric === "success") return `${sign}${(d * 100).toFixed(1)}pt`;
  if (metric === "reward") return `${sign}${d.toFixed(2)}`;
  return `${sign}${d.toFixed(2)}s`;
}

function deltaKind(
  d: number | null,
  directionGood: "up" | "down"
): "good" | "bad" | "neutral" {
  if (d === null || Math.abs(d) < 1e-6) return "neutral";
  if (directionGood === "up") return d > 0 ? "good" : "bad";
  return d < 0 ? "good" : "bad";
}

// Domain helpers so chart area paints sensibly even with near-flat data.
function domainFor(metric: Metric, data: SeriesPoint[]): [number, number] | undefined {
  if (data.length === 0) return undefined;
  const values = data.map((d) => d.rolling);
  if (metric === "success") return [0, 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (metric === "reward") {
    return [Math.max(0, min - 0.05), Math.max(1, max + 0.05)];
  }
  // latency
  const pad = (max - min) * 0.2 || 0.5;
  return [Math.max(0, min - pad), max + pad];
}

export default function MetricTrendCard({
  label,
  metric,
  data,
  current,
  delta,
  directionGood,
  active,
  onClick,
}: MetricTrendCardProps) {
  const kind = deltaKind(delta, directionGood);
  const gradientId = `grad-${metric}`;
  const stroke =
    kind === "good"
      ? "hsl(var(--success))"
      : kind === "bad"
        ? "hsl(var(--destructive))"
        : "hsl(var(--chart-1))";

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative flex flex-col rounded-lg border border-border bg-card p-4 text-left transition-colors",
        onClick && "hover:border-foreground/20",
        active && "border-foreground/40 ring-1 ring-foreground/10"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
            kind === "good"
              ? "bg-success/10 text-success"
              : kind === "bad"
                ? "bg-destructive/10 text-destructive"
                : "bg-muted text-muted-foreground"
          )}
        >
          {formatDelta(metric, delta)}
        </span>
      </div>

      <div className="mt-1 font-heading text-[26px] font-semibold leading-none tracking-tight tabular-nums text-foreground">
        {formatValue(metric, current)}
      </div>

      <div className="mt-3 h-[52px] w-full">
        {data.length === 0 ? (
          <div className="flex h-full items-center text-[11px] text-muted-foreground">
            waiting for data…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={stroke} stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <YAxis hide domain={domainFor(metric, data) as [number, number]} />
              <Tooltip
                cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
                contentStyle={{
                  background: "hsl(var(--popover))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "var(--radius)",
                  fontSize: 11,
                  padding: "4px 8px",
                  color: "hsl(var(--popover-foreground))",
                }}
                labelStyle={{ display: "none" }}
                formatter={(v) => [formatValue(metric, Number(v)), "rolling"]}
              />
              <Area
                type="monotone"
                dataKey="rolling"
                stroke={stroke}
                strokeWidth={1.75}
                fill={`url(#${gradientId})`}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </button>
  );
}
