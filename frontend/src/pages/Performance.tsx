import { useCallback, useMemo } from "react";
import { Activity, CheckCircle2, Clock, Cpu, Sparkles, Target } from "lucide-react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import SummaryCard from "../components/shared/SummaryCard";
import StatusBadge from "../components/shared/StatusBadge";
import AgentBar from "../components/shared/AgentBar";
import { usePolling } from "../hooks/usePolling";
import {
  getJson,
  parseDecision,
  type AgentStatus,
  type Health,
  type RoutingAgentsResponse,
  type RoutingDecisionsResponse,
  type RoutingStats,
} from "../lib/api";

const REFRESH_MS = 5000;

function formatPct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}
function formatCurrency(x: number): string {
  if (x < 0.01) return `$${x.toFixed(4)}`;
  return `$${x.toFixed(2)}`;
}
function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)}s`;
}
function formatUptime(s: number): string {
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s - hours * 3600) / 60);
  return `${hours}h ${minutes}m`;
}
function formatRelative(iso: string, now: number): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diff = (now - t) / 1000;
  if (diff < 60) return `${diff.toFixed(0)}s`;
  if (diff < 3600) return `${(diff / 60).toFixed(0)}m`;
  if (diff < 86400) return `${(diff / 3600).toFixed(0)}h`;
  return `${(diff / 86400).toFixed(0)}d`;
}
function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default function PerformancePage() {
  const healthFetcher = useCallback(
    (signal: AbortSignal) => getJson<Health>("/api/health", signal),
    []
  );
  const statsFetcher = useCallback(
    (signal: AbortSignal) => getJson<RoutingStats>("/api/routing/stats", signal),
    []
  );
  const agentsHealthFetcher = useCallback(
    (signal: AbortSignal) => getJson<AgentStatus[]>("/api/agents/status", signal),
    []
  );
  const routingAgentsFetcher = useCallback(
    (signal: AbortSignal) => getJson<RoutingAgentsResponse>("/api/routing/agents", signal),
    []
  );
  const decisionsFetcher = useCallback(
    (signal: AbortSignal) =>
      getJson<RoutingDecisionsResponse>(
        "/api/routing/decisions?limit=10",
        signal
      ),
    []
  );

  const health = usePolling(healthFetcher, REFRESH_MS);
  const stats = usePolling(statsFetcher, REFRESH_MS);
  const agentsHealth = usePolling(agentsHealthFetcher, REFRESH_MS);
  const routingAgents = usePolling(routingAgentsFetcher, REFRESH_MS);
  const decisions = usePolling(decisionsFetcher, REFRESH_MS);

  const h = health.data;
  const s = stats.data?.stats;
  const agentHealthList = agentsHealth.data ?? [];
  const onlineAgents = agentHealthList.filter((a) => a.available).length;

  const agentShares = useMemo(() => {
    const list = routingAgents.data?.agents ?? [];
    const total = list.reduce((sum, a) => sum + a.total, 0);
    if (total === 0) return [];
    return list
      .map((a) => ({
        name: a.name,
        share: a.total,
        value: a.total / total,
        reward: a.avg_reward,
        success_rate: a.success_rate,
      }))
      .sort((a, b) => b.value - a.value);
  }, [routingAgents.data]);

  const recentDecisions = useMemo(
    () => (decisions.data?.decisions ?? []).slice(0, 6).map(parseDecision),
    [decisions.data]
  );
  const now = Date.now();

  return (
    <>
      <PageHeader
        title="Performance"
        subtitle="How the routing engine is doing across every task it has handled."
        actions={
          <StatusBadge variant={h?.status === "ok" ? "ok" : "warn"}>
            {h ? (h.status === "ok" ? "healthy" : h.status) : "…"}
          </StatusBadge>
        }
      />

      <div className="mb-6 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
        <SummaryCard
          label="Decisions"
          value={h?.total_decisions?.toLocaleString() ?? "—"}
          subtitle={`strategy: ${h?.strategy ?? "—"}`}
          icon={<Target size={14} />}
        />
        <SummaryCard
          label="Success rate"
          value={s ? formatPct(s.success_rate) : "—"}
          subtitle={s ? `${s.successes.toLocaleString()} of ${s.total.toLocaleString()}` : "no data"}
          icon={<CheckCircle2 size={14} />}
        />
        <SummaryCard
          label="Avg reward"
          value={s ? s.avg_reward.toFixed(3) : "—"}
          subtitle={s ? `total reward ${s.total_reward.toFixed(1)}` : ""}
          icon={<Sparkles size={14} />}
        />
        <SummaryCard
          label="Avg latency"
          value={s ? formatSeconds(s.avg_latency * 1000) : "—"}
          subtitle={s ? `total spend ${formatCurrency(s.total_cost)}` : ""}
          icon={<Clock size={14} />}
        />
        <SummaryCard
          label="Agents online"
          value={
            agentsHealth.loading && agentHealthList.length === 0
              ? "—"
              : `${onlineAgents} / ${agentHealthList.length}`
          }
          subtitle={h ? `uptime ${formatUptime(h.uptime_s)}` : ""}
          icon={<Cpu size={14} />}
        />
        <SummaryCard
          label="Avg cost / task"
          value={s ? formatCurrency(s.avg_cost) : "—"}
          subtitle={s ? `from ${s.total.toLocaleString()} tasks` : ""}
          icon={<Activity size={14} />}
        />
      </div>

      <div className="mb-5 grid gap-5 md:grid-cols-2">
        <Panel title="Agent distribution" actions={<span className="text-xs text-muted-foreground">last {stats.data?.stats?.total ?? 0} tasks</span>}>
          {agentShares.length === 0 ? (
            <div className="text-sm text-muted-foreground">No routing data yet.</div>
          ) : (
            <div>
              {agentShares.map((a) => (
                <AgentBar
                  key={a.name}
                  name={a.name}
                  value={a.value}
                  label={`${a.share}`}
                  highlighted={a.value === agentShares[0].value}
                />
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Avg reward by agent" actions={<span className="text-xs text-muted-foreground">higher is better</span>}>
          {agentShares.length === 0 ? (
            <div className="text-sm text-muted-foreground">—</div>
          ) : (
            <div>
              {agentShares.map((a) => {
                const r = typeof a.reward === "number" ? a.reward : 0;
                return (
                  <AgentBar
                    key={a.name}
                    name={a.name}
                    value={Math.max(0, Math.min(1, r))}
                    label={r.toFixed(2)}
                  />
                );
              })}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Recent decisions" padded={false}>
        {recentDecisions.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            No decisions logged yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                <th className="px-5 py-3 w-[64px]">Age</th>
                <th className="px-5 py-3">Prompt</th>
                <th className="px-5 py-3 w-[120px]">Agent</th>
                <th className="px-5 py-3 w-[96px]">Mode</th>
                <th className="px-5 py-3 w-[88px] text-right">Reward</th>
              </tr>
            </thead>
            <tbody>
              {recentDecisions.map((p) => (
                <tr
                  key={p.raw.id}
                  className="border-b border-border last:border-b-0 transition-colors hover:bg-muted/40"
                >
                  <td className="px-5 py-3 font-mono text-[11px] text-muted-foreground">
                    {formatRelative(p.raw.timestamp, now)}
                  </td>
                  <td className="px-5 py-3 text-foreground">
                    {truncate(p.raw.task_goal || "(empty)", 80)}
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-foreground">
                    {p.raw.selected_agent}
                  </td>
                  <td className="px-5 py-3">
                    <span
                      className={
                        p.exploration
                          ? "rounded-full bg-chart-5/15 px-2 py-0.5 text-[11px] font-medium text-chart-5"
                          : "rounded-full bg-success/10 px-2 py-0.5 text-[11px] font-medium text-success"
                      }
                    >
                      {p.exploration ? "explore" : "exploit"}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right font-mono tabular-nums text-muted-foreground">
                    {p.raw.reward !== null ? p.raw.reward.toFixed(2) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {stats.error && (
        <Panel className="mt-4">
          <div className="text-sm text-destructive">Routing stats error: {stats.error}</div>
        </Panel>
      )}
    </>
  );
}
