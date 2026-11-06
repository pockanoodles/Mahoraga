// The research log (content/log.ts), newest first under month headings. Each
// entry is a hairline row in Design 03's list of points: its date and kind on
// the left, then what was found and the files and pull requests behind it. A
// kind is carried by its label and ink weight, never by a second colour.
import type { Dict, Locale } from "@/content/copy";
import { byMonth } from "@/content/log";
import { Reveal } from "./Reveal";

export function Log({ t, locale }: { t: Dict["log"]; locale: Locale }) {
  return (
    <div className="log">
      {byMonth().map((g) => (
        <Reveal key={`${g.year}-${g.month}`} className="log__month">
          <h3 className="log__month-title">{t.month(g.year, g.month)}</h3>
          <ol className="log__entries">
            {g.entries.map((e) => (
              <li key={e.id} id={`log-${e.id}`} className="entry" data-kind={e.kind}>
                <p className="entry__meta">
                  <time className="entry__date" dateTime={e.date}>
                    {e.date}
                  </time>
                  <span className="entry__kind">{t.kinds[e.kind]}</span>
                </p>
                <div className="entry__main">
                  <h4 className="entry__title">{e.title[locale]}</h4>
                  <p className="entry__body">{e.body[locale]}</p>
                  <ul className="entry__sources" aria-label={t.sources}>
                    {e.receipts.map((r) => (
                      <li key={r.href}>
                        <a href={r.href} target="_blank" rel="noreferrer">
                          {r.label[locale]}
                          <span aria-hidden> ↗</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            ))}
          </ol>
        </Reveal>
      ))}
    </div>
  );
}
