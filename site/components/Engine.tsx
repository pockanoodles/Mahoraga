// The engine page: the pipeline from a repository's history to a verdict, the
// first probe replayed as its evidence settled, the verdict it computes, and
// what stays on the machine. Its numbers come from the same export as the log.
import type { Dict, Locale } from "@/content/copy";
import { PRICE, entryFor } from "@/content/log";
import { GIT } from "@/content/channels";
import { curveAt, data } from "@/lib/data";
import { pagePath } from "@/lib/routes";
import { Notes } from "./Notes";
import { Replay } from "./Replay";
import { Reveal } from "./Reveal";
import { Shell } from "./Shell";
import { VerdictDemo } from "./VerdictDemo";

const STAGES = ["mine", "doctor", "run", "report", "verdict"] as const;

export function Engine({ t, locale }: { t: Dict; locale: Locale }) {
  const vd = data.verdict_demo;
  const at = curveAt(PRICE); // the verdict at the worked example's price, as the log quotes it
  const replay = data.probe.replay;

  return (
    <Shell t={t} locale={locale} page="engine">
      {/* ── The engine ── */}
      <section id="engine" className="band screen page-head" aria-labelledby="engine-title">
        <div className="band__body band__body--wide">
          <Reveal>
            <h1 id="engine-title" className="h2 center">
              {t.engine.title}
            </h1>
            <p className="lede center">{t.engine.lead}</p>
          </Reveal>
          <ol className="pipeline">
            {t.engine.stages.map((s, i) => (
              <Reveal as="li" key={s.name} delay={i * 70} className="pipeline__stage">
                <p className="step__n">{String(i + 1).padStart(2, "0")}</p>
                <h2 className="step__title">{s.name}</h2>
                <p className="step__body">{s.body}</p>
                <code className="pipeline__cmd">{`mahoraga audit ${STAGES[i]}`}</code>
              </Reveal>
            ))}
          </ol>
          <p className="pipeline__caption center">{t.engine.pipeline}</p>
          <ul className="steps steps--keeps">
            {t.engine.keeps.map((k, i) => (
              <Reveal as="li" key={k.title} delay={i * 80}>
                <h2 className="step__title">{k.title}</h2>
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
            <VerdictDemo
              demo={vd}
              locale={locale}
              initial={PRICE}
              note={t.verdict.note(entryFor(vd.claim).date, vd.evidence.rate.tasks, vd.assumptions.tasks_per_month)}
            />
          </Reveal>
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
          <p className="center after-grid policy-links">
            <a className="textlink" href={pagePath(locale, "privacy")}>
              {t.privacy.policy}
            </a>
            <a className="textlink" href={`${GIT}/blob/main/DATAFLOW.md`} target="_blank" rel="noreferrer">
              {t.privacy.link} <span aria-hidden>↗</span>
            </a>
          </p>
        </div>
      </section>

      <Notes t={t} notes={[]} />
    </Shell>
  );
}
