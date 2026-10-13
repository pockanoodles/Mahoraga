// The wheel (brand/BRAND.md). Ink follows currentColor; the lit spoke is the
// accent. Geometry matches brand/mark-*.svg and the app's Mark exactly.
const SPOKES = Array.from({ length: 8 }, (_, i) => {
  const a = ((i * 45 - 90) * Math.PI) / 180;
  return { i, x1: 32 + 7 * Math.cos(a), y1: 32 + 7 * Math.sin(a), x2: 32 + 20 * Math.cos(a), y2: 32 + 20 * Math.sin(a) };
});

export function Mark({ size = 22, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} className={className} aria-hidden="true">
      <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="6" />
      {SPOKES.map((s) => (
        <line key={s.i} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} stroke={s.i === 0 ? "var(--mh-accent)" : "currentColor"} strokeWidth="3.6" />
      ))}
      <circle cx="32" cy="32" r="7" fill="currentColor" />
    </svg>
  );
}
