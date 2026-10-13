// A hover-and-focus tooltip. Tooltips here only ever repeat or explain what is
// already on screen in words, so nothing depends on them (dataviz: tooltips
// enhance, never gate).
import { useId, useState, type ReactNode } from "react";

export function Tooltip({
  content,
  children,
  side = "top",
  block = false,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  block?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span
      className={`tip-anchor${block ? " tip-anchor--block" : ""}`}
      onPointerEnter={() => setOpen(true)}
      onPointerLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      aria-describedby={open ? id : undefined}
    >
      {children}
      <span role="tooltip" id={id} className={`tip tip--${side}`} data-open={open || undefined}>
        {content}
      </span>
    </span>
  );
}
