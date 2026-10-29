// The install page: how to get the engine onto a machine and queue a night's
// run on your own repository, the ways to script it once it is there, and how
// to help with it.
import type { Dict, Locale } from "@/content/copy";
import { GIT, SPONSOR_URL } from "@/content/channels";
import { PRICE } from "@/content/log";
import { REPO_URL, curveAt, data } from "@/lib/data";
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
  const vd = data.verdict_demo;
  const at = curveAt(PRICE);
  const httpReq = `curl -s localhost:8000/api/audit/verdict \\
  -H 'Content-Type: application/json' \\
  -d '{"arm": "${ARM}", "cond": "${vd.select.cond}",
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
