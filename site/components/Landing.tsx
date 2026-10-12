// The landing page, top to bottom. One idea per screen (Design 03): the
// question, how it's answered, the verdict, the receipts, the app, the engine,
// privacy, install, and how to help. Every measured number is read from the
// engine's export through lib/data.ts, and its footnote names the artifact.
import type { Dict, Locale } from "@/content/copy";
import { GIT, SPONSOR_URL } from "@/content/channels";
import { hasAsset } from "@/lib/assets";
import { REPO_URL, arm, claim, data, metric, pct } from "@/lib/data";
import { CodeTabs } from "./CodeTabs";
import { Install } from "./Install";
import { LocalNav } from "./LocalNav";
import { Mark } from "./Mark";
import { Replay } from "./Replay";
import { Reveal } from "./Reveal";
import { SiteMenu } from "./SiteMenu";
import { VerdictDemo } from "./VerdictDemo";

const HERO_PRICE = 0.75; // the report's worked example: $150 a month over 200 tasks

const COMMANDS = {
  mine: "mahoraga audit mine ~/code/app",
  doctor: "mahoraga audit doctor ~/code/app --arm aider:qwen3.5:latest",
  run: "mahoraga audit run ~/code/app --arm aider:qwen3.5:latest --until 07:30",
  report: "mahoraga audit report ~/code/app",
  verdict: "mahoraga audit verdict ~/code/app --arm aider:qwen3.5:latest \\\n    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6",
};

export function Landing({ t, locale }: { t: Dict; locale: Locale }) {
  const cascade = claim("humaneval_plus_164_reading_judge");
  const qwen = arm("aider:qwen3.5:latest", "blind");
  const granite = arm("aider:granite4.1:8b", "blind");
  const replay = data.probe.replay;
  const hero = data.verdict_demo.curve.reduce((b, p) =>
    Math.abs(p.cloud_usd_per_task - HERO_PRICE) < Math.abs(b.cloud_usd_per_task - HERO_PRICE) ? p : b,
  );
  const n = data.probe.tasks.length;
  const half = Math.round(((qwen.rate.hi - qwen.rate.lo) / 2) * 100);
  const keep = cascade.metrics.routed_pass_at_1.value / cascade.metrics.cloud_pass_at_1.value;
  const ja = locale === "ja";
  const cfg = cascade.config;

  const highlights = [
    {
      value: metric(cascade, "routed_pass_at_1"),
      text: ja
        ? `ローカル優先のカスケードによる HumanEval+ の pass@1。クラウド単体のスコアの ${pct(keep, 1)} です。`
        : `pass@1 on HumanEval+ with the local-first cascade, ${pct(keep, 1)} of the cloud model's score.`,
      note: 1,
    },
    {
      value: `$${metric(cascade, "routed_cost_per_1k")}`,
      text: ja
        ? `カスケードでの 1,000 タスクあたりの費用。すべてクラウドなら $${metric(cascade, "cloud_cost_per_1k")} です。`
        : `per thousand tasks with the cascade, against $${metric(cascade, "cloud_cost_per_1k")} all-cloud.`,
      note: 1,
    },
    {
      value: ja ? `${qwen.n} 件中 ${qwen.resolved} 件` : `${qwen.resolved} of ${qwen.n}`,
      text: ja
        ? `aider と qwen3.5（9.7B）が ${data.probe.machine} の Mac で解決した、実際のコミットの数です。`
        : `real commits resolved by aider and qwen3.5 (9.7B) on a ${data.probe.machine} Mac.`,
      note: 2,
    },
    {
      value: ja ? `${qwen.median_minutes} 分` : `${qwen.median_minutes} min`,
      text: ja ? `1 試行あたりの中央値。だから監査は夜のあいだに走らせます。` : `median per attempt, which is why the audit runs overnight.`,
      note: 2,
    },
    {
      value: ja ? `${granite.n} 件中 ${granite.resolved} 件` : `${granite.resolved} of ${granite.n}`,
      text: ja
        ? `granite 4.1（8B）の一発での解決数。プロンプトへの回答では最良でも、編集では最下位でした。`
        : `for granite 4.1 (8B) in one shot, our best model at prompts and the worst at edits.`,
      note: 2,
    },
    {
      value: ja ? `±${half} ポイント` : `±${half} points`,
      text: ja
        ? `${qwen.rate.tasks} タスクで残る不確かさ。何度繰り返しても、タスクを増やさない限り狭まりません。`
        : `of uncertainty left by ${qwen.rate.tasks} tasks, however often they rerun.`,
      note: 3,
    },
  ];

  const notes = ja
    ? [
        `HumanEval+（${cascade.metrics.n.value} タスク）、${cfg.date} に計測。ローカルのアームは ${cfg.local_arm}、ジャッジは ${cfg.judge.split(" \u2014 ")[0]}（プロンプトと出力だけを読み、隠しテストは見ない）、クラウドのアームは ${cfg.cloud_arm}。CI で orch bench verify が ${cascade.artifact} から計算し直しています。`,
        `エージェント編集プローブ：Mahoraga 自身の履歴から ${n} 件のコミットを、${data.probe.dates} に ${data.probe.machine} の Mac で aider と Ollama を使って実行。各試行はコミットのテストとフルスイートで採点しました。データは experiments/agent_edit にあります。`,
        `タスク単位で重み付けした解決率と、タスク数で数えた 95% Wilson 区間（agentbench/stats.py）。`,
        `判定は audit/verdict.py によるものです。前提は判定の節に載せた値で、価格は audit/pricing.json の日付つきの観測値です。`,
      ]
    : [
        `HumanEval+ (${cascade.metrics.n.value} tasks), measured ${cfg.date}. Local arm ${cfg.local_arm}, a judge (${cfg.judge.split(" \u2014 ")[0]}) that reads the prompt and the output but never the hidden tests, cloud arm ${cfg.cloud_arm}. Recomputed in CI by orch bench verify from ${cascade.artifact}.`,
        `The agent-edit probe: ${n} commits from Mahoraga's own history, run ${data.probe.dates} on a ${data.probe.machine} Mac with aider and Ollama. Each attempt was graded by the commit's tests and the full suite. Data in experiments/agent_edit.`,
        `Task-weighted resolve rate with a 95% Wilson interval over tasks (agentbench/stats.py).`,
        `Verdicts from audit/verdict.py, with the assumptions listed in the verdict section and prices from the dated observations in audit/pricing.json.`,
      ];

  const httpReq = `curl -s localhost:8000/api/audit/verdict \\
  -H 'Content-Type: application/json' \\
  -d '{"arm": "aider:qwen3.5:latest", "cond": "blind",
       "billing": {"kind": "api", "monthly_usd": ${HERO_PRICE * data.verdict_demo.assumptions.tasks_per_month}},
       "assumptions": {"tasks_per_month": ${data.verdict_demo.assumptions.tasks_per_month}}}'`;
  const r3 = (x: number) => Number(x.toFixed(3));
  const httpOut = JSON.stringify(
    {
      call: hero.call,
      confident: hero.confident,
      breakeven: r3(hero.breakeven),
      monthly_usd: { lo: Math.round(hero.monthly_usd.lo), point: Math.round(hero.monthly_usd.point), hi: Math.round(hero.monthly_usd.hi) },
      evidence: { rate: { point: r3(qwen.rate.point), lo: r3(qwen.rate.lo), hi: r3(qwen.rate.hi), tasks: qwen.rate.tasks } },
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
    <>
      <LocalNav t={t.nav} locale={locale} version={/-dev$/.test(data.source.version) ? null : data.source.version} />
      <main id="top">
        {/* ── Hero ── */}
        <section className="hero" data-hero aria-labelledby="hero-title">
          <div className="hero__stage" aria-hidden />
          <div className="hero__chart">
            <Replay
              steps={replay.steps}
              tasks={data.probe.tasks}
              cell={`${replay.arm} / ${replay.cond}`}
              breakeven={hero.breakeven}
              breakevenLabel={t.hero.breakeven(`$${HERO_PRICE.toFixed(2)}`)}
              locale={locale}
            />
          </div>
          <div className="hero__text">
            <h1 id="hero-title" className="hero__statement">
              {t.hero.statement}
            </h1>
            <p className="hero__sub">{t.hero.sub}</p>
          </div>
          <div className="hero__cta">
            <a href="#install" className="pill">
              {t.hero.cta}
            </a>
            <a href="#question" className="hero__more">
              {t.hero.more}
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 5v14M6 13l6 6 6-6" />
              </svg>
            </a>
          </div>
        </section>

        {/* ── The question ── */}
        <section id="question" className="band screen" aria-labelledby="question-title">
          <div className="band__body">
            <Reveal>
              <h2 id="question-title" className="h2 center">
                {t.question.title}
              </h2>
              <p className="lede center">{t.question.lead}</p>
            </Reveal>
            <ol className="steps">
              {t.question.steps.map((s, i) => (
                <Reveal as="li" key={s.title} delay={i * 80}>
                  <p className="step__n">{String(i + 1).padStart(2, "0")}</p>
                  <h3 className="step__title">{s.title}</h3>
                  <p className="step__body">{s.body}</p>
                </Reveal>
              ))}
            </ol>
          </div>
        </section>

        {/* ── How it works ── */}
        <section id="how" className="band screen" aria-labelledby="how-title">
          <div className="band__body band__body--wide">
            <Reveal>
              <h2 id="how-title" className="h2 center">
                {t.how.title}
              </h2>
              <p className="lede center">{t.how.lead}</p>
            </Reveal>
            <ol className="pipeline">
              {t.how.stages.map((s, i) => (
                <Reveal as="li" key={s.name} delay={i * 70} className="pipeline__stage">
                  <p className="step__n">{String(i + 1).padStart(2, "0")}</p>
                  <h3 className="step__title">{s.name}</h3>
                  <p className="step__body">{s.body}</p>
                  <code className="pipeline__cmd">{`mahoraga audit ${Object.keys(COMMANDS)[i]}`}</code>
                </Reveal>
              ))}
            </ol>
            <p className="pipeline__caption center">{t.how.pipeline}</p>
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
              <VerdictDemo locale={locale} initial={HERO_PRICE} />
            </Reveal>
          </div>
        </section>

        {/* ── Evidence ── */}
        <section id="evidence" className="band screen" aria-labelledby="evidence-title">
          <div className="band__body band__body--wide">
            <Reveal>
              <h2 id="evidence-title" className="h2 center">
                {t.evidence.title}
              </h2>
              <p className="lede center">{t.evidence.lead}</p>
            </Reveal>
            <ul className="highlights">
              {highlights.map((h, i) => (
                <Reveal as="li" key={i} delay={(i % 3) * 80} className="highlight">
                  <p className="highlight__value">
                    {h.value}
                    <a href={`#note-${h.note}`} className="fnref" aria-label={`Note ${h.note}`}>
                      {h.note}
                    </a>
                  </p>
                  <p className="highlight__text">{h.text}</p>
                </Reveal>
              ))}
            </ul>
          </div>
        </section>

        {/* ── Corrections ── */}
        <section id="corrections" className="band" aria-labelledby="corrections-title">
          <div className="band__body">
            <Reveal>
              <h2 id="corrections-title" className="h2 center">
                {t.corrections.title}
              </h2>
              <p className="lede center">{t.corrections.lead}</p>
            </Reveal>
            <ul className="points">
              {t.corrections.items.map((c, i) => (
                <Reveal as="li" key={i} delay={i * 60}>
                  {c}
                </Reveal>
              ))}
            </ul>
            <p className="small center">{t.corrections.source}</p>
          </div>
        </section>

        {/* ── The app ── */}
        <section id="app" className="band screen" aria-labelledby="app-title">
          <div className="band__body band__body--wide">
            <Reveal>
              <h2 id="app-title" className="h2 center">
                {t.app.title}
              </h2>
              <p className="lede center">{t.app.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <figure className="shot">
                <div className="shot__light" aria-hidden />
                {hasAsset("app/verdict-dark.png") ? (
                  <picture>
                    {hasAsset("app/verdict-dark-narrow.png") && <source srcSet="/mahoraga/app/verdict-dark-narrow.png" media="(max-width: 639px)" />}
                    <img src="/mahoraga/app/verdict-dark.png" alt={t.app.shotAlt} className="shot__img" width={2880} height={1800} loading="lazy" decoding="async" />
                  </picture>
                ) : (
                  <div className="shot__pending">{t.app.pending}</div>
                )}
              </figure>
            </Reveal>
            <ul className="steps steps--app">
              {t.app.features.map((f, i) => (
                <Reveal as="li" key={f.name} delay={i * 80}>
                  <h3 className="step__title">{f.name}</h3>
                  <p className="step__body">{f.body}</p>
                </Reveal>
              ))}
            </ul>
            <p className="small center">
              {t.app.demo[0]} <code>mahoraga demo</code> {t.app.demo[1]}
            </p>
          </div>
        </section>

        {/* ── Headless ── */}
        <section id="engine" className="band" aria-labelledby="engine-title">
          <div className="band__body">
            <Reveal>
              <h2 id="engine-title" className="h2 center">
                {t.headless.title}
              </h2>
              <p className="lede center">{t.headless.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <CodeTabs
                label={t.headless.title}
                copyLabel={t.install.copy}
                copiedLabel={t.install.copied}
                tabs={[
                  { id: "cli", label: "CLI", code: Object.values(COMMANDS).filter((_, i) => i !== 3).map((c) => `$ ${c}`).join("\n"), copy: Object.values(COMMANDS).filter((_, i) => i !== 3).join("\n") },
                  { id: "http", label: "HTTP", code: `$ ${httpReq}`, copy: httpReq, out: httpOut },
                  { id: "mcp", label: "MCP", code: mcp, copy: "claude mcp add mahoraga -- mahoraga mcp" },
                ]}
              />
            </Reveal>
            <p className="small center">{t.headless.mcpNote}</p>
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

        {/* ── Install ── */}
        <section id="install" className="band screen" aria-labelledby="install-title">
          <div className="band__body">
            <Reveal>
              <h2 id="install-title" className="h2 center">
                {t.install.title}
              </h2>
              <p className="lede center">{t.install.lead}</p>
            </Reveal>
            <Reveal delay={80}>
              <Install t={t.install} locale={locale} />
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
            {ja ? "数値の出典" : "Numbers from"} <code>docs/site/site.json</code>
            {ja ? "（コミット " : " at commit "}
            <a className="textlink" href={`${REPO_URL}/commit/${data.source.commit}`} target="_blank" rel="noreferrer">
              <code>{data.source.commit}</code>
            </a>
            {ja ? "）。" : "."}
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
            <h2>{t.footer.product}</h2>
            <ul>
              <li>
                <a href="#how">{t.nav.how}</a>
              </li>
              <li>
                <a href="#evidence">{t.nav.evidence}</a>
              </li>
              <li>
                <a href="#app">{t.nav.app}</a>
              </li>
              <li>
                <a href="#install">{t.nav.install}</a>
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
