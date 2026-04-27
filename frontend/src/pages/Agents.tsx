import { useCallback } from "react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import StatusBadge from "../components/shared/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { getJson, type AgentStatus } from "../lib/api";

const REFRESH_MS = 5000;

function capLabels(caps: AgentStatus["capabilities"]): string {
  return caps
    .slice(0, 4)
    .map((c) => c.name)
    .join(", ");
}

export default function AgentsPage() {
  const fetcher = useCallback(
    (signal: AbortSignal) => getJson<AgentStatus[]>("/api/agents/status", signal),
    []
  );
  const { data, loading, error } = usePolling(fetcher, REFRESH_MS);

  const agents = data ?? [];

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <PageHeader
        title="Agents"
        subtitle="Registered workers and their current health."
      />

      <Panel padded={false}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              <th className="px-5 py-3">Agent</th>
              <th className="px-5 py-3">Worker ID</th>
              <th className="px-5 py-3">Capabilities</th>
              <th className="px-5 py-3">Status</th>
              <th className="px-5 py-3">Detail</th>
            </tr>
          </thead>
          <tbody>
            {loading && agents.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {!loading && agents.length === 0 && !error && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-muted-foreground">
                  No agents registered.
                </td>
              </tr>
            )}
            {error && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-destructive">
                  {error}
                </td>
              </tr>
            )}
            {agents.map((a) => (
              <tr
                key={a.worker_id}
                className="border-b border-border last:border-b-0 transition-colors hover:bg-muted/40"
              >
                <td className="px-5 py-3 font-medium text-foreground">{a.name}</td>
                <td className="px-5 py-3 font-mono text-xs text-muted-foreground">
                  {a.worker_id}
                </td>
                <td className="px-5 py-3 text-xs text-muted-foreground">
                  {capLabels(a.capabilities)}
                </td>
                <td className="px-5 py-3">
                  <StatusBadge variant={a.available ? "ok" : "error"}>
                    {a.available ? "online" : "offline"}
                  </StatusBadge>
                </td>
                <td className="px-5 py-3 text-xs text-muted-foreground">{a.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
