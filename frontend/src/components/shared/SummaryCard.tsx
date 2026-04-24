import { ReactNode } from "react";
import { cn } from "../../lib/cn";

interface SummaryCardProps {
  label: string;
  value: ReactNode;
  trend?: {
    label: string;
    kind: "up-good" | "down-good" | "up-bad" | "down-bad" | "neutral";
  };
  subtitle?: string;
  icon?: ReactNode;
}

const TREND_STYLES: Record<NonNullable<SummaryCardProps["trend"]>["kind"], string> = {
  "up-good": "text-success bg-success/10",
  "down-good": "text-success bg-success/10",
  "up-bad": "text-destructive bg-destructive/10",
  "down-bad": "text-destructive bg-destructive/10",
  neutral: "text-muted-foreground bg-muted-foreground/10",
};

export default function SummaryCard({ label, value, trend, subtitle, icon }: SummaryCardProps) {
  return (
    <div className="fade-in rounded-lg bg-card p-5 shadow-[rgba(0,0,0,0.08)_0px_0px_0px_1px] transition-shadow hover:shadow-[rgba(0,0,0,0.1)_0px_0px_0px_1px]">
      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        {icon && <span className="opacity-80">{icon}</span>}
        <span>{label}</span>
        {trend && (
          <span
            className={cn(
              "ml-auto inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium",
              TREND_STYLES[trend.kind]
            )}
          >
            {trend.label}
          </span>
        )}
      </div>
      <div className="mt-1 font-heading text-[30px] font-bold leading-[1.1] tracking-tight text-foreground">
        {value}
      </div>
      {subtitle && (
        <div className="mt-2 text-xs text-muted-foreground">{subtitle}</div>
      )}
    </div>
  );
}
