import { useCallback, useMemo, useState } from "react";
import Panel from "../shared/Panel";
import SegmentedControl from "../shared/SegmentedControl";
import TrendChart from "./TrendChart";
import { usePolling } from "../../hooks/usePolling";
import { getJson, type RoutingDecisionsResponse } from "../../lib/api";
import {
  buildSeries,
  summarize,
  type Metric,
  type TaskWindow,
  type TimeWindow,
  type WindowMode,
} from "../../lib/series";

const REFRESH_MS = 10_000;
const FETCH_LIMIT = 2000;

const MODE_OPTIONS: { value: WindowMode; label: string }[] = [
  { value: "tasks", label: "By tasks" },
  { value: "time", label: "By time" },
];

const TASK_OPTIONS: { value: string; label: string }[] = [
  { value: "50", label: "50" },
  { value: "100", label: "100" },
  { value: "250", label: "250" },
  { value: "500", label: "500" },
  { value: "1000", label: "1000" },
  { value: "0", label: "All" },
];

const TIME_OPTIONS: { value: TimeWindow; label: string }[] = [
  { value: "1h", label: "1h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "all", label: "All" },
];

const METRIC_OPTIONS: { value: Metric; label: string }[] = [
  { value: "success", label: "Success" },
  { value: "reward", label: "Reward" },
  { value: "latency", label: "Latency" },
];

function formatMetric(metric: Metric, v: number): string {
  if (metric === "success") return `${(v * 100).toFixed(1)}%`;
  if (metric === "reward") return v.toFixed(3);
  return `${v.toFixed(2)}s`;
}

function deltaLabel(metric: Metric, d: number | null): string {
  if (d === null || Math.abs(d) < 1e-6) return "";
  const sign = d > 0 ? "+" : "";
  if (metric === "success") return `${sign}${(d * 100).toFixed(1)} pts`;
  if (metric === "reward") return `${sign}${d.toFixed(2)}`;
  return `${sign}${d.toFixed(2)}s`;
}

export default function TrendsPanel() {
  const [mode, setMode] = useState<WindowMode>("tasks");
  const [taskWindow, setTaskWindow] = useState<TaskWindow>(250);
  const [timeWindow, setTimeWindow] = useState<TimeWindow>("24h");
  const [metric, setMetric] = useState<Metric>("success");

  const fetcher = useCallback(
    (signal: AbortSignal) =>
      getJson<RoutingDecisionsResponse>(
        `/api/routing/decisions?limit=${FETCH_LIMIT}`,
        signal
      ),
    []
  );
  const { data, loading, error } = usePolling(fetcher, REFRESH_MS);

  const decisions = data?.decisions ?? [];
  const series = useMemo(
    () => buildSeries({ decisions, metric, mode, taskWindow, timeWindow }),
    [decisions, metric, mode, taskWindow, timeWindow]
  );
  const stats = summarize(series);

  // For reward/latency, success only makes sense as the "good" direction varies.
  // Success + reward → up is good. Latency → down is good.
  const deltaKind: "good" | "bad" | "neutral" =
    stats.delta === null || Math.abs(stats.delta) < 1e-6
      ? "neutral"
      : metric === "latency"
        ? stats.delta < 0
          ? "good"
          : "bad"
        : stats.delta > 0
          ? "good"
          : "bad";

  return (
    <Panel
      title="Trends"
      actions={
        <div className="flex items-center gap-3">
          <SegmentedControl
            value={metric}
            onChange={setMetric}
            options={METRIC_OPTIONS}
            size="sm"
          />
        </div>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SegmentedControl
          label="Mode"
          value={mode}
          onChange={setMode}
          options={MODE_OPTIONS}
          size="sm"
        />
        {mode === "tasks" ? (
          <SegmentedControl
            label="Window"
            value={String(taskWindow)}
            onChange={(v) => setTaskWindow(Number(v) as TaskWindow)}
            options={TASK_OPTIONS}
            size="sm"
          />
        ) : (
          <SegmentedControl
            label="Range"
            value={timeWindow}
            onChange={(v) => setTimeWindow(v as TimeWindow)}
            options={TIME_OPTIONS}
            size="sm"
          />
        )}
      </div>

      {error ? (
        <div className="py-10 text-center text-sm text-destructive">{error}</div>
      ) : loading && decisions.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">Loading decisions…</div>
      ) : series.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          No data in this window yet. Widen the window or send more tasks.
        </div>
      ) : (
        <>
          <TrendChart data={series} metric={metric} />
          <div className="mt-3 flex flex-wrap items-center gap-5 border-t border-border pt-3 text-xs text-muted-foreground">
            <span>
              <span className="text-muted-foreground">n </span>
              <span className="font-mono tabular-nums text-foreground">
                {stats.n.toLocaleString()}
              </span>
            </span>
            <span>
              <span className="text-muted-foreground">avg </span>
              <span className="font-mono tabular-nums text-foreground">
                {formatMetric(metric, stats.avg)}
              </span>
            </span>
            <span>
              <span className="text-muted-foreground">latest </span>
              <span className="font-mono tabular-nums text-foreground">
                {stats.latest !== null ? formatMetric(metric, stats.latest) : "—"}
              </span>
            </span>
            {stats.delta !== null && (
              <span
                className={
                  deltaKind === "good"
                    ? "rounded bg-success/10 px-1.5 py-0.5 font-mono tabular-nums text-success"
                    : deltaKind === "bad"
                      ? "rounded bg-destructive/10 px-1.5 py-0.5 font-mono tabular-nums text-destructive"
                      : "rounded bg-muted px-1.5 py-0.5 font-mono tabular-nums text-muted-foreground"
                }
              >
                {deltaLabel(metric, stats.delta)}
              </span>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}
