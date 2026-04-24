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
const FEED_LIMIT = 30;

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
    <>
      <PageHeader
        title="Observatory"
        subtitle="The bandit's decisions in real time — what it chose, what it considered, and whether it played it safe."
        actions={
          <StatusBadge variant={decisions.error ? "error" : "ok"}>
            {decisions.error ? "error" : decisions.loading ? "loading" : "live"}
          </StatusBadge>
        }
      />

      {/* Top row — quick context */}
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

      <div className="grid gap-5 md:grid-cols-[1fr_320px]">
        <Panel title="Live routing decisions" padded={false}>
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
            <div className="flex flex-col gap-3 p-4">
              {parsed.map((p) => (
                <DecisionCard key={p.raw.id} decision={p} />
              ))}
            </div>
          )}
        </Panel>

        <div className="flex flex-col gap-5">
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
    </>
  );
}
