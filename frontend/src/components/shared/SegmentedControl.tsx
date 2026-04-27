import { cn } from "../../lib/cn";

export interface Segment<T extends string> {
  value: T;
  label: string;
}

interface SegmentedControlProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: Segment<T>[];
  size?: "sm" | "md";
  label?: string;
}

// Shadcn-style pill-in-pill tabs. Used for window/time/metric switchers.
export default function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  size = "md",
  label,
}: SegmentedControlProps<T>) {
  return (
    <div className="inline-flex items-center gap-2">
      {label && (
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      <div
        className={cn(
          "inline-flex items-center gap-1 rounded-md bg-muted p-[3px]",
          size === "sm" ? "h-7" : "h-9"
        )}
        role="tablist"
      >
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(opt.value)}
              className={cn(
                "inline-flex items-center justify-center whitespace-nowrap rounded px-3 text-xs font-medium transition-colors",
                size === "sm" ? "h-5" : "h-7",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
