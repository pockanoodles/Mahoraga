"use client";

// The engine section's exhibit: the first probe, replayed. Each of the 18
// commits lands in the order the probe ran them, and the 95% interval on the
// resolve rate is drawn as it stood after each one, so the band visibly
// settles as evidence arrives (not at every step: one resolve near the end
// widens it slightly, and the drawing shows that too). It starts the first
// time it scrolls into view. The break-even line is where local would start to
// pay at the stated cloud price. Every number is the engine's (site.json
// probe.replay, computed with agentbench.stats.task_rate); nothing is
// interpolated except the drawing between two real steps while it animates.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { copy, type Locale } from "@/content/copy";
import type { ProbeTask, ReplayStep } from "@/lib/data";
import { pct } from "@/lib/data";

const STEP_MS = 125;
const LEAD_MS = 450;

function ease(t: number) {
  return 1 - Math.pow(1 - t, 3);
}

export function Replay({
  steps,
  tasks,
  cell,
  breakeven,
  breakevenLabel,
  locale,
}: {
  steps: ReplayStep[];
  tasks: ProbeTask[];
  cell: string;
  breakeven: number;
  breakevenLabel: string;
  locale: Locale;
}) {
  const t = copy[locale];
  const labels = {
    chart: t.replay.chartLabel(steps.length),
    axisTasks: t.replay.axisTasks,
    breakeven: breakevenLabel,
    resolved: t.replay.resolved,
    interval: t.replay.interval,
    replay: t.replay.replay,
    outcome: (o: string) => t.outcomes[o] ?? o,
  };
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [k, setK] = useState(0); // how many steps have landed (fractional while drawing)
  const [run, setRun] = useState(0);
  const [active, setActive] = useState<number | null>(null);
  const [seen, setSeen] = useState(false);
  const N = steps.length;
  const done = k >= N;
  const byS = useMemo(() => new Map(tasks.map((t) => [t.sha, t])), [tasks]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Below the fold, so it waits for the reader instead of finishing unseen.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -20% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setK(N);
      return;
    }
    if (!seen) return;
    setK(0);
    let raf = 0;
    let start = 0;
    const tick = (now: number) => {
      if (!start) start = now;
      const t = (now - start - LEAD_MS) / STEP_MS;
      const whole = Math.floor(t);
      const next = t <= 0 ? 0 : Math.min(N, whole + ease(Math.min(1, t - whole)));
      setK(next);
      if (next < N) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [N, run, seen]);

  const { w, h } = size;
  const narrow = w < 560;
  const L = narrow ? 38 : 48;
  const R = narrow ? 88 : 150;
  const T = 30;
  const B = 58;
  const pw = Math.max(1, w - L - R);
  const ph = Math.max(1, h - T - B);
  const x = useCallback((n: number) => L + ((n - 1) / (N - 1)) * pw, [L, N, pw]);
  const y = useCallback((p: number) => T + (1 - p) * ph, [ph]);

  // The drawn head: every landed step, then a partial step toward the next.
  const pts = useMemo(() => {
    const out: { n: number; lo: number; hi: number; p: number }[] = [];
    const whole = Math.floor(k);
    for (let i = 0; i < Math.min(whole, N); i++) {
      const r = steps[i].rate;
      out.push({ n: i + 1, lo: r.lo, hi: r.hi, p: r.point });
    }
    const f = k - whole;
    if (whole >= 1 && whole < N && f > 0) {
      const a = steps[whole - 1].rate;
      const b = steps[whole].rate;
      out.push({ n: whole + f, lo: a.lo + (b.lo - a.lo) * f, hi: a.hi + (b.hi - a.hi) * f, p: a.point + (b.point - a.point) * f });
    }
    return out;
  }, [k, N, steps]);

  const band =
    pts.length > 1
      ? `M${pts.map((q) => `${x(q.n)},${y(q.hi)}`).join("L")}L${[...pts].reverse().map((q) => `${x(q.n)},${y(q.lo)}`).join("L")}Z`
      : "";
  const hiLine = pts.length > 1 ? `M${pts.map((q) => `${x(q.n)},${y(q.hi)}`).join("L")}` : "";
  const loLine = pts.length > 1 ? `M${pts.map((q) => `${x(q.n)},${y(q.lo)}`).join("L")}` : "";
  const line = pts.length > 1 ? `M${pts.map((q) => `${x(q.n)},${y(q.p)}`).join("L")}` : "";
  const head = pts[pts.length - 1];
  const last = steps[N - 1];
  const solved = steps.filter((s) => s.outcome === "resolved").length;

  const pick = (clientX: number) => {
    const el = box.current;
    if (!el) return;
    const rx = clientX - el.getBoundingClientRect().left;
    const n = Math.round(((rx - L) / pw) * (N - 1)) + 1;
    setActive(Math.max(1, Math.min(Math.floor(k) || 1, n)));
  };

  const act = active != null && active <= Math.floor(k) ? steps[active - 1] : null;
  const actTask = act ? byS.get(act.sha) : undefined;

  return (
    <figure className="replay" aria-label={labels.chart}>
      <div
        ref={box}
        className="replay__plot"
        tabIndex={0}
        onPointerMove={(e) => pick(e.clientX)}
        onPointerLeave={() => setActive(null)}
        onBlur={() => setActive(null)}
        onFocus={() => setActive((a) => a ?? Math.max(1, Math.floor(k)))}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            const max = Math.max(1, Math.floor(k));
            setActive((a) => Math.max(1, Math.min(max, (a ?? max) + (e.key === "ArrowRight" ? 1 : -1))));
          }
          if (e.key === "Escape") setActive(null);
        }}
      >
        {w > 0 && h > 0 && (
          <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
            {[0, 0.5, 1].map((g) => (
              <g key={g}>
                <line x1={L} x2={L + pw} y1={y(g)} y2={y(g)} className="replay__grid" />
                <text x={L - 10} y={y(g) + 4} textAnchor="end" className="replay__tick">
                  {pct(g)}
                </text>
              </g>
            ))}
            <line x1={L} x2={L + pw} y1={y(breakeven)} y2={y(breakeven)} className="replay__be" />
            <text x={L + pw} y={y(breakeven) - 9} textAnchor="end" className="replay__be-label">
              {labels.breakeven}
            </text>

            {band && <path d={band} className="replay__band" />}
            {hiLine && <path d={hiLine} className="replay__edge" />}
            {loLine && <path d={loLine} className="replay__edge" />}
            {line && <path d={line} className="replay__line" />}

            {head && !done && (
              <g>
                <line x1={x(head.n)} x2={x(head.n)} y1={y(head.hi)} y2={y(head.lo)} className="replay__head" />
                <circle cx={x(head.n)} cy={y(head.p)} r={5} className="replay__dot" />
              </g>
            )}
            {done && <circle cx={x(N)} cy={y(last.rate.point)} r={5.5} className="replay__dot" />}

            {act && (
              <line x1={x(act.n)} x2={x(act.n)} y1={T} y2={T + ph + 26} className="replay__cross" />
            )}

            {steps.map((s, i) => (
              <circle
                key={s.sha}
                cx={x(i + 1)}
                cy={T + ph + 26}
                r={s.outcome === "resolved" ? 4.5 : 4}
                className={`replay__task${s.outcome === "resolved" ? " replay__task--solved" : ""}`}
                data-on={k >= i + 1 || undefined}
                data-active={act?.n === i + 1 || undefined}
              />
            ))}
            <text x={L} y={h - 6} className="replay__tick">
              1
            </text>
            <text x={L + pw} y={h - 6} textAnchor="end" className="replay__tick">
              {`${N} ${labels.axisTasks}`}
            </text>

            <g className="replay__end" data-on={done || undefined}>
              <text x={L + pw + (narrow ? 12 : 22)} y={y(last.rate.point) + (narrow ? 8 : 11)} className="replay__big">
                {pct(last.rate.point)}
              </text>
              <text x={L + pw + (narrow ? 12 : 22)} y={y(last.rate.point) + (narrow ? 26 : 34)} className="replay__small">
                {`${pct(last.rate.lo)}–${pct(last.rate.hi)}`}
              </text>
              {!narrow && (
                <text x={L + pw + 22} y={y(last.rate.point) + 53} className="replay__tiny">
                  {labels.resolved(solved, N)}
                </text>
              )}
            </g>
          </svg>
        )}
        {act && w > 0 && (
          <div className="replay__tip" style={{ left: Math.min(Math.max(x(act.n), 140), w - 140) }} role="status">
            <p className="replay__tip-head">
              <span className="mono">{act.sha}</span>
              <span>{labels.outcome(act.outcome)}</span>
            </p>
            {actTask && <p className="replay__tip-subject">{actTask.subject}</p>}
            <p className="replay__tip-rate">
              {`${act.n}/${N}: ${pct(act.rate.point)} [${pct(act.rate.lo)}–${pct(act.rate.hi)}]`}
              {actTask?.outcomes[cell] ? ` · ${actTask.outcomes[cell].minutes.toFixed(1)} min` : ""}
            </p>
          </div>
        )}
      </div>
      <figcaption className="replay__caption">
        <span>{labels.chart}</span>
        {done && (
          <button type="button" className="replay__again" onClick={() => setRun((r) => r + 1)}>
            ↻ {labels.replay}
          </button>
        )}
      </figcaption>
      <table className="sr-only">
        <caption>{labels.chart}</caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Commit</th>
            <th scope="col">Outcome</th>
            <th scope="col">{labels.interval}</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s) => (
            <tr key={s.sha}>
              <td>{s.n}</td>
              <td>
                {s.sha} {byS.get(s.sha)?.subject}
              </td>
              <td>{labels.outcome(s.outcome)}</td>
              <td>{`${pct(s.rate.point)} [${pct(s.rate.lo)}–${pct(s.rate.hi)}]`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
