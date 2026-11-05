// The hero: where local stands now. Each measured row is a claim's resolve rate
// with its 95% interval over tasks, and the line is the share local has to
// resolve to pay at the worked example's cloud price, which the same claims'
// verdicts compute. The page only places them: every number is the engine's.
// The settings rows share one set of tasks, the laptop's own row is on its
// share of them, and a run still filling in, when there is one, comes last.
import type { Dict, Locale } from "@/content/copy";
import { PRICE, STANDING, entryById, type StandingRow } from "@/content/log";
import { claim, num, pct } from "@/lib/data";

// The day a run began, from its log entry: "October 28", or 「10 月 28 日」 with
// the spaces the Japanese copy puts around numbers.
function since(date: string, locale: Locale): string {
  const [, m, d] = date.split("-").map(Number);
  if (locale === "ja") return `${m} 月 ${d} 日`;
  return new Date(Date.UTC(2026, m - 1, d)).toLocaleDateString("en-US", { month: "long", day: "numeric", timeZone: "UTC" });
}

function measured(r: StandingRow) {
  const c = claim(r.claim);
  return { ...r, rate: num(c, "rate"), lo: num(c, "rate_lo"), hi: num(c, "rate_hi"), k: num(c, "resolved"), n: num(c, "tasks") };
}

export function Standing({ t, locale, refs }: { t: Dict["standing"]; locale: Locale; refs: string[] }) {
  const settings = STANDING.rows.map(measured);
  const own = measured(STANDING.own);
  const be = num(claim(STANDING.breakeven), "breakeven_rate");
  // The caption says the settings rows are on the same tasks and the laptop's
  // row is on its share of them, with the Studio's count on that same share.
  const tasks = settings[0].n;
  if (settings.some((r) => r.n !== tasks)) throw new Error(`the hero's settings rows are on different task sets (${settings.map((r) => r.n).join(", ")})`);
  const pair = claim(STANDING.own.versus);
  if (num(pair, "pairs") !== own.n || num(pair, "pair_resolved") !== own.k)
    throw new Error(`${pair.id} pairs ${num(pair, "pairs")} tasks, and the laptop's row has ${own.n}`);
  const rows = [...settings, own];
  const running = STANDING.running;

  return (
    <figure className="standing" aria-labelledby="standing-title" style={{ ["--be" as string]: be }}>
      <p id="standing-title" className="standing__title">
        {t.title}
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
          {running && (
            <li className="standing__row standing__row--running" style={{ ["--i" as string]: rows.length }}>
              <p className="standing__label">
                <span className="standing__model">{running.model[locale]}</span>
                <span className="standing__fit">{t.runningNote(since(entryById(running.entry).date, locale))}</span>
              </p>
              <div className="standing__track" aria-hidden />
              <p className="standing__value">
                <a className="standing__running" href={`#log-${running.entry}`}>
                  {t.running}
                </a>
              </p>
            </li>
          )}
        </ol>
        <p className="standing__axis" aria-hidden>
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </p>
      </div>
      <figcaption className="standing__caption">
        {t.caption(tasks, own.n, num(pair, "versus_resolved"))}
        {refs.map((label, i) => (
          <a key={i} href={`#note-${i + 1}`} className="fnref" aria-label={label}>
            {i + 1}
          </a>
        ))}
      </figcaption>
    </figure>
  );
}
