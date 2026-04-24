import type { RoutingDecision } from "./api";

export type Metric = "success" | "reward" | "latency";
export type WindowMode = "tasks" | "time";

export type TaskWindow = 50 | 100 | 250 | 500 | 1000 | 0; // 0 = all
export type TimeWindow = "1h" | "24h" | "7d" | "30d" | "all";

export interface SeriesPoint {
  index: number; // chronological index (1..n)
  timestamp: number; // ms since epoch
  value: number; // raw metric value
  rolling: number; // rolling average up to this point
}

const TIME_WINDOW_MS: Record<TimeWindow, number | null> = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  all: null,
};

function extract(d: RoutingDecision, metric: Metric): number | null {
  if (metric === "success") return d.success;
  if (metric === "reward") return d.reward;
  if (metric === "latency") return d.latency_s;
  return null;
}

function rollingAvg(values: number[], k: number): number[] {
  const out: number[] = [];
  let sum = 0;
  const q: number[] = [];
  for (let i = 0; i < values.length; i++) {
    q.push(values[i]);
    sum += values[i];
    if (q.length > k) sum -= q.shift()!;
    out.push(sum / q.length);
  }
  return out;
}

export interface BuildSeriesArgs {
  decisions: RoutingDecision[];
  metric: Metric;
  mode: WindowMode;
  taskWindow: TaskWindow;
  timeWindow: TimeWindow;
}

export function buildSeries(args: BuildSeriesArgs): SeriesPoint[] {
  const { decisions, metric, mode, taskWindow, timeWindow } = args;

  // Decisions arrive newest-first; reverse for chronological order.
  const chronological = [...decisions].reverse();

  // Drop rows that don't have the metric populated (unverified tasks).
  let filtered = chronological
    .map((d, i) => ({
      d,
      v: extract(d, metric),
      t: new Date(d.timestamp).getTime(),
      i,
    }))
    .filter((r) => r.v !== null && !Number.isNaN(r.t));

  if (mode === "tasks") {
    if (taskWindow !== 0) {
      filtered = filtered.slice(-taskWindow);
    }
  } else {
    const delta = TIME_WINDOW_MS[timeWindow];
    if (delta !== null) {
      const cutoff = Date.now() - delta;
      filtered = filtered.filter((r) => r.t >= cutoff);
    }
  }

  const values = filtered.map((r) => r.v as number);
  const k = Math.max(3, Math.floor(values.length / 15));
  const rolling = rollingAvg(values, k);

  return filtered.map((r, idx) => ({
    index: idx + 1,
    timestamp: r.t,
    value: r.v as number,
    rolling: rolling[idx],
  }));
}

export function summarize(series: SeriesPoint[]): {
  n: number;
  avg: number;
  latest: number | null;
  delta: number | null; // change from first to last rolling
} {
  if (series.length === 0) return { n: 0, avg: 0, latest: null, delta: null };
  const avg =
    series.reduce((s, p) => s + p.value, 0) / series.length;
  const latest = series[series.length - 1].rolling;
  const first = series[0].rolling;
  return {
    n: series.length,
    avg,
    latest,
    delta: latest - first,
  };
}
