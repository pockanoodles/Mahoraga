"use client";

// The verdict, played with. The slider sets what one cloud task costs; the
// page then shows the engine's own verdict for that price, looked up from the
// curve the exporter computed (site.json verdict_demo.curve). There is no
// verdict logic in this file: it only picks the nearest precomputed point.
import { useEffect, useMemo, useRef, useState } from "react";
import { copy, type Locale } from "@/content/copy";
import { data, pct, usd, type CurvePoint } from "@/lib/data";

const MIN = Math.log10(0.1);
const MAX = Math.log10(30);

function priceText(x: number) {
  return x < 1 ? `$${x.toFixed(2)}` : Number.isInteger(x) ? `$${x}` : `$${x.toFixed(2)}`;
}

export function VerdictDemo({ locale, initial = 0.75 }: { locale: Locale; initial?: number }) {
  const t = copy[locale].verdict;
  const curve = data.verdict_demo.curve;
  const ev = data.verdict_demo.evidence.rate;
  const [i, setI] = useState(() => nearest(curve, initial));
  const p = curve[i];
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const a = data.verdict_demo.assumptions;
  const lines = useMemo(
    () =>
      t.assumptions({
        tasks: a.tasks_per_month,
        share: pct(a.deferrable_share),
        triage: a.triage_minutes,
        hourly: a.hourly_usd,
        watts: a.watts,
        kwh: a.usd_per_kwh,
      }),
    [t, a],
  );

  const pad = 10;
  const tw = Math.max(0, w - pad * 2);
  const X = (v: number) => pad + Math.min(1, Math.max(0, v)) * tw;
  const sliderValue = Math.log10(p.cloud_usd_per_task);

  return (
    <div className="vdemo">
      <div className="vdemo__control">
        <label htmlFor="vdemo-price" className="vdemo__label">
          {t.slider}
        </label>
        <output htmlFor="vdemo-price" className="vdemo__price">
          {priceText(p.cloud_usd_per_task)} <span>{t.perTask}</span>
        </output>
        <input
          id="vdemo-price"
          type="range"
          className="vdemo__slider"
          min={MIN}
          max={MAX}
          step={0.001}
          value={sliderValue}
          onChange={(e) => setI(nearest(curve, Math.pow(10, Number(e.target.value))))}
          aria-valuetext={`${priceText(p.cloud_usd_per_task)} ${t.perTask}`}
          style={{ ["--fill" as string]: `${((sliderValue - MIN) / (MAX - MIN)) * 100}%` }}
        />
        <div className="vdemo__ticks" aria-hidden>
          {[0.1, 1, 10, 30].map((v) => (
            <span key={v} style={{ left: `${((Math.log10(v) - MIN) / (MAX - MIN)) * 100}%` }}>
              {priceText(v)}
            </span>
          ))}
        </div>
      </div>

      <div className="vdemo__result" aria-live="polite">
        <div className="vdemo__head">
          <p className="vdemo__word" data-call={p.call}>
            {t.words[p.call]}
          </p>
          <p className={`vdemo__status${p.confident ? "" : " vdemo__status--open"}`}>{p.confident ? t.status.decided : t.status.open}</p>
        </div>
        <p className="vdemo__means">{t.means[p.call]}</p>
        <p className="vdemo__reason">{t.reason(p, p.reason)}</p>
      </div>

      <div ref={box} className="vdemo__chart">
        {w > 0 && (
          <svg width={w} height={86} viewBox={`0 0 ${w} 86`} role="img" aria-label={`${t.facts.measured} ${pct(ev.point)} [${pct(ev.lo)}–${pct(ev.hi)}], ${t.facts.breakeven} ${pct(p.breakeven)}`}>
            <rect x={pad} y={30} width={tw} height={22} rx={11} className="vdemo__track" />
            <rect x={X(ev.lo)} y={30} width={Math.max(22, X(ev.hi) - X(ev.lo))} height={22} rx={11} className="vdemo__band" />
            <circle cx={X(ev.point)} cy={41} r={6.5} className="vdemo__dot" />
            <g className="vdemo__be" style={{ transform: `translateX(${X(p.breakeven)}px)` }}>
              <line x1={0} x2={0} y1={18} y2={60} />
            </g>
            {[0, 0.5, 1].map((g) => (
              <text key={g} x={X(g)} y={80} textAnchor={g === 0 ? "start" : g === 1 ? "end" : "middle"} className="vdemo__tick">
                {pct(g)}
              </text>
            ))}
            <text
              x={X(p.breakeven)}
              y={12}
              textAnchor={X(p.breakeven) > w - 80 ? "end" : X(p.breakeven) < 80 ? "start" : "middle"}
              className="vdemo__be-label"
            >
              {`${t.facts.breakeven} ${pct(p.breakeven)}`}
            </text>
          </svg>
        )}
      </div>

      <dl className="vdemo__facts">
        <div>
          <dt>{t.facts.measured}</dt>
          <dd>{`${pct(ev.point)} [${pct(ev.lo)}–${pct(ev.hi)}]`}</dd>
          <dd className="vdemo__fact-note">{t.measuredNote(ev.tasks)}</dd>
        </div>
        <div>
          <dt>{t.facts.breakeven}</dt>
          <dd>{pct(p.breakeven)}</dd>
          <dd className="vdemo__fact-note">{priceText(p.cloud_usd_per_task)} {t.perTask}</dd>
        </div>
        <div>
          <dt>{t.facts.net}</dt>
          <dd>{usd(p.monthly_usd.point)}</dd>
          <dd className="vdemo__fact-note">{t.facts.netNote(usd(p.monthly_usd.lo), usd(p.monthly_usd.hi))}</dd>
        </div>
      </dl>

      <div className="vdemo__assumed">
        <p className="vdemo__assumed-title">{t.assumed}</p>
        <ul>
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
        <p className="vdemo__note">{t.note(a.tasks_per_month)}</p>
      </div>
    </div>
  );
}

function nearest(curve: CurvePoint[], price: number): number {
  let best = 0;
  for (let j = 1; j < curve.length; j++) {
    if (Math.abs(Math.log(curve[j].cloud_usd_per_task / price)) < Math.abs(Math.log(curve[best].cloud_usd_per_task / price))) best = j;
  }
  return best;
}
