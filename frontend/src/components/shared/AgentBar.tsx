import { cn } from "../../lib/cn";

interface AgentBarProps {
  name: string;
  value: number; // 0..1
  label?: string; // right-side label (defaults to percentage)
  highlighted?: boolean;
}

export default function AgentBar({ name, value, label, highlighted }: AgentBarProps) {
  const pct = Math.max(0, Math.min(1, value));
  const display = label ?? `${(pct * 100).toFixed(1)}%`;
  return (
    <div className="flex items-center gap-3 py-1">
      <div
        className={cn(
          "w-[148px] truncate font-mono text-[12px]",
          highlighted ? "text-foreground" : "text-muted-foreground"
        )}
      >
        {name}
      </div>
      <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500 ease-out",
            highlighted ? "bg-chart-1" : "bg-chart-2/70"
          )}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <div
        className={cn(
          "w-[56px] text-right font-mono text-[12px] tabular-nums",
          highlighted ? "font-semibold text-foreground" : "text-muted-foreground"
        )}
      >
        {display}
      </div>
    </div>
  );
}
