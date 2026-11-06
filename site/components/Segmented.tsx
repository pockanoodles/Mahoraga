"use client";

// A segmented control: a few choices in one track, with a thumb that slides to
// the chosen one as a native control's does. It is a radio group or a tab
// list, so Tab enters it once and the arrow keys move the choice. Until the
// script has measured the choices, the chosen one carries the thumb's look
// itself, so the control reads the same before hydration and without motion.
import { useLayoutEffect, useRef, type KeyboardEvent } from "react";

export interface SegOption<K extends string | number> {
  key: K;
  label: string;
}

export function Segmented<K extends string | number>({
  options,
  value,
  onChange,
  role = "radio",
  label,
  labelledBy,
  className = "",
  ids,
}: {
  options: SegOption<K>[];
  value: K;
  onChange: (key: K) => void;
  role?: "radio" | "tab";
  label?: string;
  labelledBy?: string;
  className?: string;
  /** Tabs only: each tab's own id and the id of the panel it shows. */
  ids?: (key: K) => { tab: string; panel: string };
}) {
  const track = useRef<HTMLDivElement>(null);

  // The thumb is placed from the chosen item's box, again whenever the track
  // resizes (a font arriving, a wrap on a narrow screen). It only starts to
  // glide after its first placement, so it never slides in from the edge.
  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const place = () => {
      const on = el.querySelector<HTMLElement>("[data-on]");
      if (!on) return;
      el.style.setProperty("--seg-x", `${on.offsetLeft}px`);
      el.style.setProperty("--seg-y", `${on.offsetTop}px`);
      el.style.setProperty("--seg-w", `${on.offsetWidth}px`);
      el.style.setProperty("--seg-h", `${on.offsetHeight}px`);
      if (!el.dataset.thumb) {
        el.dataset.thumb = "placed";
        requestAnimationFrame(() => requestAnimationFrame(() => (el.dataset.thumb = "live")));
      }
    };
    place();
    const ro = new ResizeObserver(place);
    ro.observe(el);
    return () => ro.disconnect();
  }, [value]);

  const keys = (e: KeyboardEvent, i: number) => {
    const n = options.length;
    const next = e.key === "ArrowRight" || (role === "radio" && e.key === "ArrowDown");
    const prev = e.key === "ArrowLeft" || (role === "radio" && e.key === "ArrowUp");
    const to = next ? (i + 1) % n : prev ? (i + n - 1) % n : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    onChange(options[to].key);
    track.current?.querySelectorAll<HTMLElement>(".seg__item")[to]?.focus();
  };

  return (
    <div
      ref={track}
      className={`seg ${className}`}
      role={role === "tab" ? "tablist" : "radiogroup"}
      aria-label={label}
      aria-labelledby={labelledBy}
      style={{ ["--seg-n" as string]: options.length }}
    >
      <span className="seg__thumb" aria-hidden />
      {options.map((o, i) => {
        const on = o.key === value;
        const tab = role === "tab" ? ids?.(o.key) : undefined;
        return (
          <button
            key={o.key}
            type="button"
            role={role}
            id={tab?.tab}
            aria-controls={tab?.panel}
            aria-checked={role === "radio" ? on : undefined}
            aria-selected={role === "tab" ? on : undefined}
            tabIndex={on ? 0 : -1}
            className="seg__item"
            data-on={on || undefined}
            onClick={() => onChange(o.key)}
            onKeyDown={(e) => keys(e, i)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
