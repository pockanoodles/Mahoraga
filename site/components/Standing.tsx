// The hero: where local stands now. Each measured row is a claim's resolve rate
// with its 95% interval over tasks, and the line is the share local has to
// resolve to pay at the worked example's cloud price, which the same claims'
// verdicts compute. The page only places them: every number is the engine's.
// The last row is the run still filling in, so the chart says what it lacks.
import type { Dict, Locale } from "@/content/copy";
import { PRICE, STANDING } from "@/content/log";
import { claim, num, pct } from "@/lib/data";

export function Standing({ t, locale, refs }: { t: Dict["standing"]; locale: Locale; refs: string[] }) {
  const rows = STANDING.rows.map((r) => {
    const c = claim(r.claim);
    return { ...r, rate: num(c, "rate"), lo: num(c, "rate_lo"), hi: num(c, "rate_hi"), k: num(c, "resolved"), n: num(c, "tasks") };
  });
  const be = num(claim(STANDING.breakeven), "breakeven_rate");
  // The title and caption say every measured row is on the same tasks, so a row
  // on another set (the laptop's own run attempts fewer) has to change them first.
  const tasks = rows[0].n;
  if (rows.some((r) => r.n !== tasks)) throw new Error(`the hero's rows are on different task sets (${rows.map((r) => r.n).join(", ")})`);

  return (
    <figure className="standing" aria-labelledby="standing-title" style={{ ["--be" as string]: be }}>
      <p id="standing-title" className="standing__title">
        {t.title(tasks)}
      </p>
      <div className="standing__plot">
        <p className="standing__be-label" aria-hidden>
          <span>{t.breakeven(pct(be), `$${PRICE.toFixed(2)}`)}</span>
        </p>
        <ol className="standing__rows">
          {rows.map((r, i) => (
            <li key={r.claim} className="standing__row" style={{ ["--i" as string]: i }}>
              <p className="standing__label">
                <span className="standing__model">{r.model[locale]}</span>
                <span className="standing__fit">{r.fit[locale]}</span>
              </p>
              <div className="standing__track" role="img" aria-label={t.row(r.model[locale], pct(r.rate), r.k, r.n, pct(r.lo), pct(r.hi))}>
                <span
                  className="standing__band"
                  style={{ left: pct(r.lo, 2), width: pct(r.hi - r.lo, 2), ["--o" as string]: pct((r.rate - r.lo) / (r.hi - r.lo), 2) }}
                />
                <span className="standing__dot" style={{ left: pct(r.rate, 2) }} />
              </div>
              <p className="standing__value" aria-hidden>
                <span className="standing__pct">{pct(r.rate)}</span>
                <span className="standing__count">{t.count(r.k, r.n)}</span>
              </p>
            </li>
          ))}
          <li className="standing__row standing__row--running" style={{ ["--i" as string]: rows.length }}>
            <p className="standing__label">
              <span className="standing__model">{STANDING.running.model[locale]}</span>
              <span className="standing__fit">{t.runningNote}</span>
            </p>
            <div className="standing__track" aria-hidden />
            <p className="standing__value">
              <a className="standing__running" href={`#log-${STANDING.running.entry}`}>
                {t.running}
              </a>
            </p>
          </li>
        </ol>
        <p className="standing__axis" aria-hidden>
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </p>
      </div>
      <figcaption className="standing__caption">
        {t.caption(tasks)}
        {refs.map((label, i) => (
          <a key={i} href={`#note-${i + 1}`} className="fnref" aria-label={label}>
            {i + 1}
          </a>
        ))}
      </figcaption>
    </figure>
  );
}
