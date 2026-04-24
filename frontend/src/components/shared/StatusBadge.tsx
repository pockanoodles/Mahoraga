import { cn } from "../../lib/cn";

type Variant = "ok" | "warn" | "error" | "info" | "muted";

interface StatusBadgeProps {
  variant: Variant;
  children: React.ReactNode;
}

const VARIANT: Record<Variant, string> = {
  ok: "bg-success/10 text-success",
  warn: "bg-chart-5/15 text-chart-5",
  error: "bg-destructive/10 text-destructive",
  info: "bg-chart-2/15 text-chart-2",
  muted: "bg-muted text-muted-foreground",
};

export default function StatusBadge({ variant, children }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        VARIANT[variant]
      )}
    >
      {children}
    </span>
  );
}
