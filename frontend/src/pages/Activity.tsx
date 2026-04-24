import { Fragment, useCallback, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import PageHeader from "../components/shared/PageHeader";
import Panel from "../components/shared/Panel";
import { usePolling } from "../hooks/usePolling";
import { getJson, type LogsResponse } from "../lib/api";
import { cn } from "../lib/cn";

const REFRESH_MS = 5000;

function formatTime(secs: number): string {
  const d = new Date(secs * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default function ActivityPage() {
  const fetcher = useCallback(
    (signal: AbortSignal) => getJson<LogsResponse>("/logs/recent?limit=50", signal),
    []
  );
  const { data, loading, error } = usePolling(fetcher, REFRESH_MS);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const entries = data?.entries ?? [];

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <>
      <PageHeader
        title="Activity"
        subtitle="Every prompt that's passed through Mahoraga and how it got answered."
      />

      <Panel padded={false}>
        {loading && entries.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">Loading…</div>
        ) : error ? (
          <div className="px-5 py-10 text-center text-sm text-destructive">{error}</div>
        ) : entries.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            No activity yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                <th className="px-5 py-3 w-[14px]" />
                <th className="px-5 py-3 w-[80px]">Time</th>
                <th className="px-5 py-3">Prompt</th>
                <th className="px-5 py-3 w-[140px]">Agent</th>
                <th className="px-5 py-3 w-[88px] text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const open = expanded.has(e.id);
                return (
                  <Fragment key={e.id}>
                    <tr
                      className={cn(
                        "cursor-pointer border-b border-border transition-colors last:border-b-0 hover:bg-muted/40",
                        open && "bg-muted/30"
                      )}
                      onClick={() => toggle(e.id)}
                    >
                      <td className="pl-5 text-muted-foreground">
                        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </td>
                      <td className="px-5 py-3 font-mono text-[11px] text-muted-foreground">
                        {formatTime(e.created_at)}
                      </td>
                      <td className="px-5 py-3 text-foreground">
                        {truncate(e.user_message || "(empty)", 120)}
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-muted-foreground">
                        {e.worker_id || "—"}
                      </td>
                      <td className="px-5 py-3 text-right font-mono tabular-nums text-muted-foreground">
                        ${e.cost_usd.toFixed(4)}
                      </td>
                    </tr>
                    {open && (
                      <tr className="border-b border-border last:border-b-0 bg-muted/20">
                        <td />
                        <td colSpan={4} className="px-5 py-4">
                          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                            Prompt
                          </div>
                          <div className="mt-1 whitespace-pre-wrap rounded-md border border-border bg-background px-3 py-2 text-sm">
                            {e.user_message}
                          </div>
                          <div className="mt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                            Response
                          </div>
                          <div className="mt-1 max-h-[360px] overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground">
                            {e.assistant_response || "(empty)"}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
