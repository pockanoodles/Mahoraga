// One task in full: a real commit, the fix the bench took out, what the agent
// wrote in its place, and what the commit's own tests said about it. The
// attempt was picked by position (scripts/specimen.py), and every field is the
// bench's own record, read through the export (site.json specimen).
import type { Dict, Locale } from "@/content/copy";
import type { Specimen as Attempt } from "@/lib/data";

// A diff's hunks only: the header lines name a file the card already names.
const HEADER = /^(diff --git |index |--- |\+\+\+ )/;

function Diff({ label, text, agent = false }: { label: string; text: string; agent?: boolean }) {
  const lines = text.replace(/\n$/, "").split("\n").filter((l) => !HEADER.test(l));
  return (
    <figure className={agent ? "specimen__diff specimen__diff--agent" : "specimen__diff"}>
      <figcaption className="specimen__diff-label">{label}</figcaption>
      <pre className="specimen__code">
        <code>
          {lines.map((l, i) => (
            <span key={i} className={l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : undefined}>
              {l}
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}

export function Specimen({ t, locale, s }: { t: Dict["specimen"]; locale: Locale; s: Attempt }) {
  const a = s.attempt;
  const fmt = (n: number) => n.toLocaleString(locale === "ja" ? "ja-JP" : "en-US");
  // The run's own calendar day: the manifest writes its offset as -0400, which
  // Date parsing doesn't promise to read, so only the date part is used.
  const night = new Date(`${s.run.started_at.slice(0, 10)}T12:00:00Z`);
  const date = night.toLocaleDateString(locale === "ja" ? "ja-JP" : "en-US", { month: "long", day: "numeric", timeZone: "UTC" });

  return (
    <article className="specimen" aria-labelledby="specimen-subject">
      <header className="specimen__head">
        <p className="specimen__where">
          <span>{s.repo}</span>
          <a className="mono" href={`${s.upstream}/commit/${s.sha}`} target="_blank" rel="noreferrer">
            {s.sha} <span aria-hidden>↗</span>
          </a>
        </p>
        <h3 id="specimen-subject" className="specimen__subject">
          {s.subject}
        </h3>
      </header>
      <div className="specimen__diffs">
        <Diff label={t.fix(s.gold_src.join(", "))} text={s.gold_diff} />
        <Diff label={t.agent} text={s.patch} agent />
      </div>
      <footer className="specimen__result">
        <p className="specimen__outcome" data-resolved={a.resolved || undefined}>
          {a.resolved ? t.resolved : t.unresolved}
        </p>
        <p className="specimen__tests">{t.tests(a.f2p_pass, a.f2p_total, a.regressions, fmt(s.p2p))}</p>
        <ul className="specimen__facts">
          <li>{t.minutes((a.secs / 60).toFixed(1))}</li>
          <li>{t.tokens(fmt(a.tokens_sent), fmt(a.tokens_recv), a.llm_calls)}</li>
          <li>{t.machine(s.run.machine, date)}</li>
        </ul>
        <p className="specimen__picked">{t.picked}</p>
      </footer>
    </article>
  );
}
