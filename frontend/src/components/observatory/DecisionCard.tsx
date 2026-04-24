import { Compass, Zap } from "lucide-react";
import type { ParsedDecision } from "../../lib/api";
import AgentBar from "../shared/AgentBar";
import { cn } from "../../lib/cn";

interface DecisionCardProps {
  decision: ParsedDecision;
  fresh?: boolean; // true for newly-arrived cards → play fade-in
}

function formatRelative(iso: string, now: number): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = (now - t) / 1000;
  if (diff < 1) return "now";
  if (diff < 60) return `${diff.toFixed(0)}s ago`;
  if (diff < 3600) return `${(diff / 60).toFixed(0)}m ago`;
  if (diff < 86400) return `${(diff / 3600).toFixed(0)}h ago`;
  return new Date(iso).toLocaleString();
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default function DecisionCard({ decision, fresh }: DecisionCardProps) {
  const d = decision.raw;
  const maxUcb = decision.candidates[0]?.ucb ?? 1;
  const now = Date.now();

  return (
    <article
      className={cn(
        "rounded-lg border border-border bg-card p-4 shadow-[rgba(0,0,0,0.04)_0px_0px_0px_1px]",
        fresh && "fade-in"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
            <span>{formatRelative(d.timestamp, now)}</span>
            <span>·</span>
            <span className="uppercase tracking-wide">{d.strategy}</span>
          </div>
          <div className="mt-1 text-sm leading-snug text-foreground">
            {truncate(d.task_goal || "(no prompt)", 120)}
          </div>
        </div>

        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
            decision.exploration
              ? "bg-chart-5/15 text-chart-5"
              : "bg-success/10 text-success"
          )}
        >
          {decision.exploration ? <Compass size={11} /> : <Zap size={11} />}
          {decision.exploration ? "EXPLORE" : "EXPLOIT"}
        </span>
      </div>

      <div className="mt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        Candidates
      </div>
      <div className="mt-1">
        {decision.candidates.slice(0, 3).map((c) => (
          <AgentBar
            key={c.agent}
            name={c.agent}
            value={maxUcb > 0 ? c.ucb / maxUcb : 0}
            label={c.ucb.toFixed(3)}
            highlighted={c.agent === d.selected_agent}
          />
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-border pt-2 text-[11px] font-mono text-muted-foreground">
        <div>
          <span className="text-muted-foreground">→ </span>
          <span
            className={cn(
              "font-semibold",
              d.success === 1
                ? "text-foreground"
                : d.success === 0
                  ? "text-destructive"
                  : "text-foreground"
            )}
          >
            {d.selected_agent}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span>reward {d.reward !== null ? d.reward.toFixed(2) : "—"}</span>
          <span
            className={cn(
              d.success === 1
                ? "text-success"
                : d.success === 0
                  ? "text-destructive"
                  : "text-muted-foreground"
            )}
          >
            {d.success === 1 ? "PASS" : d.success === 0 ? "FAIL" : "pending"}
          </span>
        </div>
      </div>
    </article>
  );
}
