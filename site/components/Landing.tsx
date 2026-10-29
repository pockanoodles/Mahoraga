// The landing page, top to bottom: where local stands now, the log of every
// result, the engine that produces them, the first probe replayed, the verdict
// it computes, the ways to script it, privacy, and how to run it on your own
// repository. One idea per screen (Design 03). Every measured number is read
// from the engine's export through lib/data.ts or content/log.ts, and a
// footnote or a source link names the artifact it came from.
import type { Dict, Locale } from "@/content/copy";
import { GIT, SPONSOR_URL } from "@/content/channels";
import { PRICE, STANDING, UPDATED, entryFor } from "@/content/log";
import { REPO_URL, blob, cfg, claim, curveAt, data } from "@/lib/data";
import { CodeTabs } from "./CodeTabs";
import { LocalNav } from "./LocalNav";
import { Log } from "./Log";
import { Mark } from "./Mark";
import { Replay } from "./Replay";
import { Reveal } from "./Reveal";
import { Run } from "./Run";
import { SettingsGrid } from "./SettingsGrid";
import { SiteMenu } from "./SiteMenu";
import { Specimen } from "./Specimen";
import { Standing } from "./Standing";
import { VerdictDemo } from "./VerdictDemo";

const STAGES = ["mine", "doctor", "run", "report", "verdict"] as const;

// What a 16 GB owner runs: the model the log measures, at the window it measured best at.
const ARM = "aider:qwen3.5:latest@ctx=131072";
const COMMANDS = [
  "mahoraga audit tonight ~/code/app",
  "mahoraga audit mine ~/code/app",
  `mahoraga audit doctor ~/code/app --arm ${ARM}`,
  `mahoraga audit run ~/code/app --arm ${ARM} --timeout 5400 --until 07:30`,
  `mahoraga audit verdict ~/code/app --arm ${ARM} \\\n    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6`,
];

const REPORT = "docs/report/2026-10-local-coding-ai.md";

export function Landing({ t, locale }: { t: Dict; locale: Locale }) {
  const ja = locale === "ja";
  const vd = data.verdict_demo;
  const at = curveAt(PRICE); // the verdict at the worked example's price, as the log quotes it
  const replay = data.probe.replay;
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

  const httpReq = `curl -s localhost:8000/api/audit/verdict \\
  -H 'Content-Type: application/json' \\
  -d '{"arm": "${vd.select.arm}", "cond": "${vd.select.cond}",
       "billing": {"kind": "api", "monthly_usd": ${PRICE * vd.assumptions.tasks_per_month}},
       "assumptions": {"tasks_per_month": ${vd.assumptions.tasks_per_month}}}'`;
  const r3 = (x: number) => Number(x.toFixed(3));
  const ev = vd.evidence.rate;
  const httpOut = JSON.stringify(
    {
      call: at.call,
      confident: at.confident,
      breakeven: r3(at.breakeven),
      monthly_usd: { lo: Math.round(at.monthly_usd.lo), point: Math.round(at.monthly_usd.point), hi: Math.round(at.monthly_usd.hi) },
      evidence: { rate: { point: r3(ev.point), lo: r3(ev.lo), hi: r3(ev.hi), tasks: ev.tasks } },
    },
    null,
    2,
  );
  const mcp = `claude mcp add mahoraga -- mahoraga mcp

# or, in any MCP client's config
{
  "mcpServers": {
    "mahoraga": { "command": "mahoraga", "args": ["mcp"] }
  }
}`;
  const { updated: updatedLabel, ...nav } = t.nav;

  return (
    <>
      <LocalNav t={nav} locale={locale} updated={UPDATED} updatedLabel={updatedLabel(UPDATED)} />
      <main id="top">
        {/* ── Hero: where local stands ── */}
        <section className="hero" data-hero aria-labelledby="hero-title">
          <div className="hero__stage" aria-hidden />
          <div className="hero__chart">
            <Standing t={t.standing} locale={locale} refs={[t.notes.ref(1), t.notes.ref(2)]} />
          </div>
          <div className="hero__text">
            <p className="hero__eyebrow">{t.hero.eyebrow}</p>
            <h1 id="hero-title" className="hero__statement">
              {t.hero.statement}
            </h1>
            <p className="hero__sub">{t.hero.sub}</p>
          </div>
          <div className="hero__cta">
            <a href="#log" className="pill">
              {t.hero.cta}
            </a>
            <a href="#engine" className="hero__more">
              {t.hero.more}
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 5v14M6 13l6 6 6-6" />
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

        {/* ── The engine ── */}
        <section id="engine" className="band screen" aria-labelledby="engine-title">
          <div className="band__body band__body--wide">
            <Reveal>
              <h2 id="engine-title" className="h2 center">
                {t.engine.title}
              </h2>
              <p className="lede center">{t.engine.lead}</p>
            </Reveal>
            <ol className="pipeline">
              {t.engine.stages.map((s, i) => (
                <Reveal as="li" key={s.name} delay={i * 70} className="pipeline__stage">
                  <p className="step__n">{String(i + 1).padStart(2, "0")}</p>
                  <h3 className="step__title">{s.name}</h3>
                  <p className="step__body">{s.body}</p>
                  <code className="pipeline__cmd">{`mahoraga audit ${STAGES[i]}`}</code>
                </Reveal>
              ))}
            </ol>
            <p className="pipeline__caption center">{t.engine.pipeline}</p>
            <ul className="steps steps--keeps">
              {t.engine.keeps.map((k, i) => (
                <Reveal as="li" key={k.title} delay={i * 80}>
                  <h3 className="step__title">{k.title}</h3>
                  <p className="step__body">{k.body}</p>
                </Reveal>
              ))}
            </ul>
          </div>
        </section>

        {/* ── The first probe, replayed ── */}
        <section id="settle" className="band" aria-labelledby="settle-title">
          <div className="band__body band__body--wide">
            <Reveal>
              <h2 id="settle-title" className="h2 center">
                {t.engine.replayTitle}
              </h2>
              <p className="lede center">{t.engine.replayLead}</p>
            </Reveal>
            <div className="exhibit">
              <Replay
                steps={replay.steps}
                tasks={data.probe.tasks}
                cell={`${replay.arm} / ${replay.cond}`}
                breakeven={at.breakeven}
                breakevenLabel={t.replay.breakeven(`$${PRICE.toFixed(2)}`)}
                locale={locale}
              />
            </div>
          </div>
        </section>

        {/* ── The verdict ── */}
        <section id="verdict" className="band screen" aria-labelledby="verdict-title">
          <div className="band__body">
            <Reveal>
              <h2 id="verdict-title" className="h2 center">
                {t.verdict.title}
              </h2>
              <p className="lede center">{t.verdict.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <VerdictDemo demo={vd} locale={locale} initial={PRICE} note={t.verdict.note(entryFor(vd.claim).date, ev.tasks, vd.assumptions.tasks_per_month)} />
            </Reveal>
          </div>
        </section>

        {/* ── Headless ── */}
        <section id="interfaces" className="band" aria-labelledby="interfaces-title">
          <div className="band__body">
            <Reveal>
              <h2 id="interfaces-title" className="h2 center">
                {t.headless.title}
              </h2>
              <p className="lede center">{t.headless.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <CodeTabs
                label={t.headless.title}
                copyLabel={t.run.copy}
                copiedLabel={t.run.copied}
                tabs={[
                  { id: "cli", label: "CLI", code: COMMANDS.map((c) => `$ ${c}`).join("\n"), copy: COMMANDS.join("\n") },
                  { id: "http", label: "HTTP", code: `$ ${httpReq}`, copy: httpReq, out: httpOut },
                  { id: "mcp", label: "MCP", code: mcp, copy: "claude mcp add mahoraga -- mahoraga mcp" },
                ]}
              />
            </Reveal>
            <p className="small center">
              {t.headless.mcpNote} {t.headless.viewer}
            </p>
          </div>
        </section>

        {/* ── Privacy ── */}
        <section id="privacy" className="band" aria-labelledby="privacy-title">
          <div className="band__body">
            <Reveal>
              <h2 id="privacy-title" className="h2 center">
                {t.privacy.title}
              </h2>
            </Reveal>
            <ol className="steps">
              {t.privacy.items.map((s, i) => (
                <Reveal as="li" key={s.title} delay={i * 80}>
                  <h3 className="step__title">{s.title}</h3>
                  <p className="step__body">{s.body}</p>
                </Reveal>
              ))}
            </ol>
            <p className="center after-grid">
              <a className="textlink" href={`${GIT}/blob/main/DATAFLOW.md`} target="_blank" rel="noreferrer">
                {t.privacy.link} <span aria-hidden>↗</span>
              </a>
            </p>
          </div>
        </section>

        {/* ── Run it ── */}
        <section id="run" className="band screen" aria-labelledby="run-title">
          <div className="band__body">
            <Reveal>
              <h2 id="run-title" className="h2 center">
                {t.run.title}
              </h2>
              <p className="lede center">{t.run.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <Run t={t.run} locale={locale} />
            </Reveal>
          </div>
        </section>

        {/* ── Support ── */}
        <section id="support" className="band" aria-labelledby="support-title">
          <div className="band__body">
            <Reveal>
              <h2 id="support-title" className="h2 center">
                {t.support.title}
              </h2>
              <p className="lede center">{t.support.lead}</p>
            </Reveal>
            <div className="support">
              <a className="pill" href={REPO_URL} target="_blank" rel="noreferrer">
                {t.support.star}
              </a>
              <a className="textlink" href={`${GIT}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noreferrer">
                {t.support.contribute} <span aria-hidden>↗</span>
              </a>
              <a className="textlink" href={`${GIT}/issues`} target="_blank" rel="noreferrer">
                {t.support.issues} <span aria-hidden>↗</span>
              </a>
              {SPONSOR_URL && (
                <a className="textlink" href={SPONSOR_URL} target="_blank" rel="noreferrer">
                  {t.support.sponsor} <span aria-hidden>↗</span>
                </a>
              )}
            </div>
          </div>
        </section>

        {/* ── Notes ── */}
        <section className="notes" aria-labelledby="notes-title">
          <h2 id="notes-title" className="notes__title">
            {t.footnotesTitle}
          </h2>
          <ol>
            {notes.map((text, i) => (
              <li key={i} id={`note-${i + 1}`}>
                {text}
              </li>
            ))}
          </ol>
          <p className="notes__source">
            {t.notes.source[0]} <code>docs/site/site.json</code>
            {t.notes.source[1]}
            <a className="textlink" href={`${REPO_URL}/commit/${data.source.commit}`} target="_blank" rel="noreferrer">
              <code>{data.source.commit}</code>
            </a>
            {t.notes.source[2]}
          </p>
        </section>
      </main>

      <footer className="foot">
        <div className="foot__brand">
          <Mark size={40} />
          <p className="foot__made">{t.footer.made}</p>
          <p className="foot__legal">© 2026 Kaito Soeno · {t.footer.license}</p>
        </div>
        <nav className="foot__cols" aria-label="Footer">
          <div>
            <h2>{t.footer.page}</h2>
            <ul>
              <li>
                <a href="#log">{t.nav.log}</a>
              </li>
              <li>
                <a href="#engine">{t.nav.engine}</a>
              </li>
              <li>
                <a href="#run">{t.nav.run}</a>
              </li>
            </ul>
          </div>
          <div>
            <h2>{t.footer.project}</h2>
            <ul>
              <li>
                <a href={REPO_URL} target="_blank" rel="noreferrer">
                  GitHub
                </a>
              </li>
              <li>
                <a href={blob(REPORT)} target="_blank" rel="noreferrer">
                  {t.footer.report}
                </a>
              </li>
              <li>
                <a href={`${GIT}/blob/main/docs/RESULTS.md`} target="_blank" rel="noreferrer">
                  {t.footer.results}
                </a>
              </li>
              <li>
                <a href={`${GIT}/blob/main/DATAFLOW.md`} target="_blank" rel="noreferrer">
                  {t.footer.dataflow}
                </a>
              </li>
              <li>
                <a href={`${GIT}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noreferrer">
                  {t.footer.contributing}
                </a>
              </li>
              <li>
                <a href={`${GIT}/security`} target="_blank" rel="noreferrer">
                  {t.footer.security}
                </a>
              </li>
            </ul>
          </div>
          <div>
            <h2>{t.footer.person}</h2>
            <ul>
              <li>
                <a href={ja ? "/ja/portfolio" : "/portfolio"}>{t.footer.portfolio}</a>
              </li>
              <li>
                <a href={ja ? "/ja" : "/"}>{t.footer.contact}</a>
              </li>
              <li>
                <a href={ja ? "/ja/privacy" : "/privacy"}>{t.footer.privacy}</a>
              </li>
              <li>
                <a href={ja ? "/mahoraga/" : "/mahoraga/ja/"} hrefLang={ja ? "en" : "ja"} lang={ja ? "en" : "ja"}>
                  {t.footer.language}
                </a>
              </li>
            </ul>
          </div>
        </nav>
      </footer>
      <SiteMenu t={t.menu} />
    </>
  );
}
