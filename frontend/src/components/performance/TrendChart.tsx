import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SeriesPoint, Metric } from "../../lib/series";

interface TrendChartProps {
  data: SeriesPoint[];
  metric: Metric;
}

function yDomain(metric: Metric, data: SeriesPoint[]): [number, number] | undefined {
  if (data.length === 0) return undefined;
  if (metric === "success") return [0, 1];
  if (metric === "reward") {
    const max = Math.max(...data.map((d) => d.rolling));
    return [0, Math.max(1, max * 1.1)];
  }
  // latency — auto scale
  const max = Math.max(...data.map((d) => d.rolling));
  return [0, max * 1.2];
}

function formatX(p: SeriesPoint, totalLen: number): string {
  if (totalLen > 40) {
    // Show only milestones
    if (p.index === 1 || p.index === totalLen || p.index % Math.floor(totalLen / 5) === 0) {
      return `#${p.index}`;
    }
    return "";
  }
  return `#${p.index}`;
}

function formatYValue(metric: Metric, v: number): string {
  if (metric === "success") return `${(v * 100).toFixed(0)}%`;
  if (metric === "reward") return v.toFixed(2);
  return `${v.toFixed(2)}s`;
}

export default function TrendChart({ data, metric }: TrendChartProps) {
  const domain = yDomain(metric, data);
  const n = data.length;

  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid
            stroke="hsl(var(--border))"
            strokeDasharray="3 3"
            vertical={false}
          />
          <XAxis
            dataKey="index"
            axisLine={false}
            tickLine={false}
            minTickGap={32}
            tickFormatter={(v: number) => formatX({ index: v } as SeriesPoint, n)}
            tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            domain={domain as [number, number]}
            tickFormatter={(v: number) => formatYValue(metric, v)}
            tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
          />
          <Tooltip
            cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: "var(--radius)",
              fontSize: 11,
              color: "hsl(var(--popover-foreground))",
            }}
            labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
            labelFormatter={(label) => `task #${label}`}
            formatter={(value) => [
              formatYValue(metric, Number(value)),
              "rolling avg",
            ]}
          />
          <Line
            type="monotone"
            dataKey="rolling"
            stroke="hsl(var(--chart-1))"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
