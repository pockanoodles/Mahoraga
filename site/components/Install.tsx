// The install page: how to get the engine onto a machine and queue a night's
// run on your own repository, the command for each stage when you drive it by
// hand, and how to help with it. The app, the HTTP API and MCP are documented
// apart (docs/app.md), since no number on the map depends on them.
import type { Dict, Locale } from "@/content/copy";
import { GIT, SPONSOR_URL } from "@/content/channels";
import { REPO_URL } from "@/lib/data";
import { CodeTabs } from "./CodeTabs";
import { GitHubMark } from "./GitHubMark";
import { Reveal } from "./Reveal";
import { Run } from "./Run";
import { Shell } from "./Shell";

// What a 16 GB owner runs: the model the log measures, at the window it measured best at.
const ARM = "aider:qwen3.5:latest@ctx=131072";
const COMMANDS = [
  "mahoraga audit tonight ~/code/app",
  "mahoraga audit mine ~/code/app",
  `mahoraga audit doctor ~/code/app --arm ${ARM}`,
  `mahoraga audit run ~/code/app --arm ${ARM} --timeout 5400 --until 07:30`,
  `mahoraga audit verdict ~/code/app --arm ${ARM} \\\n    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6`,
];

export function Install({ t, locale }: { t: Dict; locale: Locale }) {
  return (
    <Shell t={t} locale={locale} page="install">
      {/* ── Run it ── */}
      <section id="run" className="band screen page-head" aria-labelledby="run-title">
        <div className="band__body">
          <Reveal>
            <h1 id="run-title" className="h2 center">
              {t.run.title}
            </h1>
            <p className="lede center">{t.run.lead}</p>
          </Reveal>
          <Reveal delay={80}>
            <Run t={t.run} locale={locale} />
          </Reveal>
        </div>
      </section>

      {/* ── Each stage by hand ── */}
      <section id="steps" className="band" aria-labelledby="steps-title">
        <div className="band__body">
          <Reveal>
            <h2 id="steps-title" className="h2 center">
              {t.byHand.title}
            </h2>
            <p className="lede center">{t.byHand.lead}</p>
          </Reveal>
          <Reveal delay={80}>
            <CodeTabs
              label={t.byHand.title}
              copyLabel={t.run.copy}
              copiedLabel={t.run.copied}
              tabs={[{ id: "cli", label: "CLI", code: COMMANDS.map((c) => `$ ${c}`).join("\n"), copy: COMMANDS.join("\n") }]}
            />
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
              <GitHubMark />
              {t.nav.github}
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
    </Shell>
  );
}
