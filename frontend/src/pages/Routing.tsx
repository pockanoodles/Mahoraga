import { useCallback } from "react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import StatusBadge from "../components/shared/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { getJson, type Health, type RoutingStats } from "../lib/api";

const REFRESH_MS = 5000;

export default function RoutingPage() {
  const healthFetcher = useCallback(
    (signal: AbortSignal) => getJson<Health>("/api/health", signal),
    []
  );
  const statsFetcher = useCallback(
    (signal: AbortSignal) => getJson<RoutingStats>("/api/routing/stats", signal),
    []
  );
  const health = usePolling(healthFetcher, REFRESH_MS);
  const stats = usePolling(statsFetcher, REFRESH_MS);

  const strategy = health.data?.strategy ?? "—";
  const total = health.data?.total_decisions ?? stats.data?.total_decisions ?? 0;

  return (
    <>
      <PageHeader
        title="Routing"
        subtitle="Bandit strategy, routing mode, and live engine state."
      />

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Active strategy">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="font-heading text-xl font-semibold text-foreground">
                {strategy}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {total.toLocaleString()} decisions recorded
              </div>
            </div>
            <StatusBadge variant="info">locked</StatusBadge>
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
            Strategy switching (LinUCB / UCB1 / Thompson / Static) wires up in phase 3.
          </p>
        </Panel>

        <Panel title="Routing mode">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="font-heading text-xl font-semibold text-foreground">balanced</div>
              <div className="mt-1 text-xs text-muted-foreground">
                let the bandit pick freely
              </div>
            </div>
            <StatusBadge variant="muted">default</StatusBadge>
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
            Modes: local_first · balanced · quality_first. Toggle lands in phase 3.
          </p>
        </Panel>
      </div>
    </>
  );
}
