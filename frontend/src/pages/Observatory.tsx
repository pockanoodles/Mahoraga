import { useCallback, useMemo } from "react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import EmptyState from "../components/shared/EmptyState";
import StatusBadge from "../components/shared/StatusBadge";
import DecisionCard from "../components/observatory/DecisionCard";
import AgentBar from "../components/shared/AgentBar";
import { usePolling } from "../hooks/usePolling";
import {
  getJson,
  parseDecision,
  type RoutingAgentsResponse,
  type RoutingDecisionsResponse,
} from "../lib/api";

const REFRESH_MS = 4000;
const FEED_LIMIT = 50;

export default function ObservatoryPage() {
  const decisionsFetcher = useCallback(
    (signal: AbortSignal) =>
      getJson<RoutingDecisionsResponse>(
        `/api/routing/decisions?limit=${FEED_LIMIT}`,
        signal
      ),
    []
  );
  const agentsFetcher = useCallback(
    (signal: AbortSignal) => getJson<RoutingAgentsResponse>("/api/routing/agents", signal),
    []
  );

  const decisions = usePolling(decisionsFetcher, REFRESH_MS);
  const agents = usePolling(agentsFetcher, REFRESH_MS);

  const parsed = useMemo(
    () => (decisions.data?.decisions ?? []).map(parseDecision),
    [decisions.data]
  );

  const exploreCount = parsed.filter((p) => p.exploration).length;
  const exploreRate = parsed.length > 0 ? exploreCount / parsed.length : 0;

  const agentShares = useMemo(() => {
    const agentList = agents.data?.agents ?? [];
    const total = agentList.reduce((sum, a) => sum + a.total, 0);
    if (total === 0) return [];
    return agentList
      .map((a) => ({
        name: a.name,
        value: a.total / total,
        share: a.total,
        success_rate: a.success_rate,
      }))
      .sort((x, y) => y.value - x.value);
  }, [agents.data]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Observatory"
        subtitle="The bandit's decisions in real time — what it chose, what it considered, and whether it played it safe."
        actions={
          <StatusBadge variant={decisions.error ? "error" : "ok"}>
            {decisions.error ? "error" : decisions.loading ? "loading" : "live"}
          </StatusBadge>
        }
      />

      {/* Quick-context row */}
      <div className="mb-5 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
        <Panel>
          <div className="text-sm font-medium text-muted-foreground">Shown</div>
          <div className="mt-1 font-heading text-2xl font-bold tracking-tight">
            {parsed.length}
          </div>
          <div className="text-xs text-muted-foreground">
            of {decisions.data?.total_available?.toLocaleString() ?? 0} total
          </div>
        </Panel>
        <Panel>
          <div className="text-sm font-medium text-muted-foreground">Exploration</div>
          <div className="mt-1 font-heading text-2xl font-bold tracking-tight">
            {(exploreRate * 100).toFixed(0)}%
          </div>
          <div className="text-xs text-muted-foreground">
            {exploreCount} of last {parsed.length} picks
          </div>
        </Panel>
        <Panel>
          <div className="text-sm font-medium text-muted-foreground">Agents in rotation</div>
          <div className="mt-1 font-heading text-2xl font-bold tracking-tight">
            {agentShares.length}
          </div>
          <div className="text-xs text-muted-foreground">
            {agents.data?.agents?.length ?? 0} registered
          </div>
        </Panel>
      </div>

      {/*
        Bounded region. min-h-0 + overflow-hidden on the grid parent lets the
        feed scroll internally instead of pushing the page. Side column gets
        its own scroll if needed.
      */}
      <div className="grid min-h-0 flex-1 gap-5 overflow-hidden md:grid-cols-[1fr_320px]">
        <div className="flex min-h-0 flex-col overflow-hidden rounded-lg bg-card shadow-[rgba(0,0,0,0.08)_0px_0px_0px_1px]">
          <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
            <h2 className="text-sm font-semibold text-foreground">Live routing decisions</h2>
            <span className="font-mono text-[11px] text-muted-foreground">
              last {parsed.length} · refresh {REFRESH_MS / 1000}s
            </span>
          </div>
          {decisions.loading && parsed.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-muted-foreground">
              Waiting for the first decision…
            </div>
          ) : parsed.length === 0 ? (
            <EmptyState
              title="Nothing routed yet"
              description="Send a task from Chat and it'll show up here with agent scores and verdict."
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <div className="flex flex-col gap-3">
                {parsed.map((p) => (
                  <DecisionCard key={p.raw.id} decision={p} />
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex min-h-0 flex-col gap-5 overflow-y-auto pr-0.5">
          <Panel title="Distribution">
            {agentShares.length === 0 ? (
              <div className="text-sm text-muted-foreground">No agent data yet.</div>
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

          <Panel title="Success rate">
            {agentShares.length === 0 ? (
              <div className="text-sm text-muted-foreground">—</div>
            ) : (
              <div>
                {agentShares.map((a) => (
                  <AgentBar
                    key={a.name}
                    name={a.name}
                    value={a.success_rate}
                    label={`${(a.success_rate * 100).toFixed(0)}%`}
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
