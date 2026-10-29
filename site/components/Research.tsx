// The research page, which is the landing page: where local stands now, one
// task in full, the setting that mattered, and the log of every result. Every
// measured number is read from the engine's export through lib/data.ts or
// content/log.ts, and a footnote or a source link names the artifact it came from.
import type { Dict, Locale } from "@/content/copy";
import { STANDING } from "@/content/log";
import { blob, cfg, claim, data } from "@/lib/data";
import { pagePath } from "@/lib/routes";
import { Log } from "./Log";
import { Notes } from "./Notes";
import { Reveal } from "./Reveal";
import { SettingsGrid } from "./SettingsGrid";
import { Shell } from "./Shell";
import { Specimen } from "./Specimen";
import { Standing } from "./Standing";

export function Research({ t, locale }: { t: Dict; locale: Locale }) {
  const ja = locale === "ja";
  const run = claim(STANDING.note);
  const dates = cfg(run, "dates");
  const settings = data.settings;
  const solved = (key: string) => {
    const arm = settings.arms.find((a) => a.key === key);
    if (!arm) throw new Error(`site.json has no settings arm ${key}`);
    return arm.resolved;
  };

  const notes = [
    t.notes.standing({
      dates: ja ? dates.replace(" to ", "〜") : dates,
      machine: cfg(run, "machine"),
      agent: cfg(run, "agent"),
      artifact: run.artifact,
    }),
    t.notes.verdict,
  ];

  return (
    <Shell t={t} locale={locale} page="research">
      {/* ── Hero: where local stands ── */}
      <section className="hero" data-hero aria-labelledby="hero-title">
        <div className="hero__stage" aria-hidden />
        <div className="hero__text">
          <p className="hero__eyebrow">{t.hero.eyebrow}</p>
          <h1 id="hero-title" className="hero__statement">
            {t.hero.statement}
          </h1>
          <p className="hero__sub">{t.hero.sub}</p>
        </div>
        <div className="hero__chart">
          <Standing t={t.standing} locale={locale} refs={[t.notes.ref(1), t.notes.ref(2)]} />
        </div>
        <div className="hero__cta">
          <a href="#log" className="pill">
            {t.hero.cta}
          </a>
          <a href={pagePath(locale, "engine")} className="hero__more">
            {t.hero.more}
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </a>
        </div>
      </section>

      {/* ── One task in full ── */}
      <section id="task" className="band" aria-labelledby="task-title">
        <div className="band__body band__body--wide">
          <Reveal>
            <h2 id="task-title" className="h2 center">
              {t.specimen.title}
            </h2>
            <p className="lede center">{t.specimen.lead}</p>
          </Reveal>
          <Reveal delay={80}>
            <Specimen t={t.specimen} locale={locale} />
          </Reveal>
        </div>
      </section>

      {/* ── The setting that mattered ── */}
      <section id="settings" className="band" aria-labelledby="settings-title">
        <div className="band__body band__body--wide">
          <Reveal>
            <h2 id="settings-title" className="h2 center">
              {t.settings.title}
            </h2>
            <p className="lede center">
              {t.settings.lead({ k32: solved("32k"), k131: solved("131k"), k35: solved("35b"), n: settings.tasks.length })}
            </p>
          </Reveal>
          <Reveal delay={80}>
            <SettingsGrid arms={settings.arms} tasks={settings.tasks} pairs={settings.pairs} locale={locale} />
          </Reveal>
          <p className="sgrid__advice center">{t.settings.advice}</p>
        </div>
      </section>

      {/* ── The log ── */}
      <section id="log" className="band" aria-labelledby="log-title">
        <div className="band__body band__body--wide">
          <Reveal>
            <h2 id="log-title" className="h2 center">
              {t.log.title}
            </h2>
            <p className="lede center">{t.log.lead}</p>
          </Reveal>
          <Log t={t.log} locale={locale} />
          <p className="small center after-grid">
            {t.log.ledger}{" "}
            <a className="textlink" href={blob("brain/state/findings.md")} target="_blank" rel="noreferrer">
              {t.log.ledgerLink} <span aria-hidden>↗</span>
            </a>
          </p>
          <p className="small center">
            <a className="textlink" href="/mahoraga/log.xml" type="application/atom+xml">
              {t.log.follow}
            </a>
          </p>
        </div>
      </section>

      <Notes t={t} notes={notes} />
    </Shell>
  );
}
