// The privacy policy page. It reads the way the engine page's pipeline does:
// numbered sections on a hairline, and anything that leaves the machine as a
// where / what / why row. On a phone the three columns stack inside each row,
// each cell keeping its own label, so no table scrolls sideways.
import type { Dict, Locale } from "@/content/copy";
import { UPDATED, policy, type LedgerRow } from "@/content/privacy";
import { Shell } from "./Shell";

const COLS = ["where", "what", "why"] as const;

function Ledger({ heads, rows }: { heads: LedgerRow; rows: LedgerRow[] }) {
  return (
    <div className="ledger" role="table">
      <div className="ledger__row ledger__head" role="row">
        {COLS.map((k) => (
          <p key={k} className="ledger__label" role="columnheader">
            {heads[k]}
          </p>
        ))}
      </div>
      {rows.map((row) => (
        <div key={row.where} className="ledger__row" role="row">
          {COLS.map((k) => (
            <div key={k} className="ledger__cell" data-col={k} role="cell">
              <p className="ledger__label">{heads[k]}</p>
              <p>{row[k]}</p>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export function Privacy({ t, locale }: { t: Dict; locale: Locale }) {
  const doc = policy[locale];
  return (
    <Shell t={t} locale={locale} page="privacy">
      <section className="band page-head" aria-labelledby="privacy-title">
        <div className="band__body">
          <h1 id="privacy-title" className="h2 center">
            {doc.title}
          </h1>
          <p className="lede center">{doc.lead}</p>
          <p className="policy__updated center">
            <time dateTime={UPDATED}>{doc.updated}</time>
          </p>
        </div>
      </section>

      <div className="band">
        <div className="band__body policy">
          {doc.sections.map((s, i) => (
            <section key={s.id} id={s.id} className="policy__section" aria-labelledby={`${s.id}-h`}>
              <p className="step__n">{String(i + 1).padStart(2, "0")}</p>
              <div className="policy__body">
                <h2 id={`${s.id}-h`} className="step__title">
                  {s.heading}
                </h2>
                {s.paras.map((p) => (
                  <p key={p} className="policy__p">
                    {p}
                  </p>
                ))}
                {s.ledger && <Ledger heads={doc.heads} rows={s.ledger} />}
                {s.after?.map((p) => (
                  <p key={p} className="policy__p policy__p--after">
                    {p}
                  </p>
                ))}
                {s.links && (
                  <ul className="policy__links">
                    {s.links.map((l) => (
                      <li key={l.href}>
                        <a className="textlink" href={l.href} target="_blank" rel="noreferrer">
                          {l.label} <span aria-hidden>↗</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ))}
        </div>
      </div>
    </Shell>
  );
}
