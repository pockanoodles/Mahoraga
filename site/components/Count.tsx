"use client";

// A count that rolls to its new value digit by digit: a digit that changes
// lifts away as its successor rises into place (upward when the count grows,
// downward when it falls), and a digit that stays the same stays still. Every
// figure takes the width of the widest the count can show, so the words beside
// it never move. The first value is simply there, and a screen reader is given
// the whole number once instead of its digits.
import { useState } from "react";

export function Count({ value, max, className = "" }: { value: number; max: number; className?: string }) {
  const [now, setNow] = useState(value);
  const [was, setWas] = useState<number | null>(null);
  if (value !== now) {
    setWas(now);
    setNow(value);
  }
  const width = String(max).length;
  const digits = (v: number) => String(v).padStart(width, " ").split("");
  const before = was == null ? null : digits(was);
  return (
    <span className={`count ${className}`} data-roll={was == null ? undefined : now > was ? "up" : "down"}>
      <span className="sr-only">{now}</span>
      {digits(now).map((d, i) => {
        const old = before?.[i];
        const moved = old != null && old !== d;
        return (
          <span key={i} className="count__cell" aria-hidden>
            {moved && (
              <span key={`was-${old}-${d}`} className="count__was" onAnimationEnd={() => setWas(null)}>
                {old}
              </span>
            )}
            <span key={moved ? `now-${old}-${d}` : `still-${d}`} className={moved ? "count__now" : undefined}>
              {d}
            </span>
          </span>
        );
      })}
    </span>
  );
}
