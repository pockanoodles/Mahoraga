import { ReactNode } from "react";
import { cn } from "../../lib/cn";

interface PanelProps {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  padded?: boolean;
}

export default function Panel({
  title,
  actions,
  children,
  className,
  bodyClassName,
  padded = true,
}: PanelProps) {
  return (
    <section
      className={cn(
        "fade-in rounded-lg bg-card shadow-[rgba(0,0,0,0.08)_0px_0px_0px_1px]",
        className
      )}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          {title && (
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          )}
          {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cn(padded ? "p-5" : "", bodyClassName)}>{children}</div>
    </section>
  );
}
