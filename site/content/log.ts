// The research log, newest first: what each measurement found, what turned out
// to be wrong, and the runs that were voided. Each entry is dated the day the
// finding was established (brain/state/findings.md, docs/report/), and a new
// result is a new entry at the top. As in copy.ts, a measured number is never
// typed here: each one is read from a claim in the engine's export, so a line
// in the log can't disagree with the attempts behind it, and a claim that
// disappears from claims.json breaks the build. Run settings (a 90-minute
// limit, a 131k context) and the figures a correction retracts are not
// measurements of the result, so they are written out.
import { REPO_URL, blob, claim, data, metric, num, pct, type Claim } from "@/lib/data";
import type { Locale } from "./copy";

export type Kind = "running" | "finding" | "correction" | "void" | "method" | "null" | "start";

export interface Receipt {
  label: Record<Locale, string>;
  href: string;
}

export interface Entry {
  id: string; // the anchor: #log-<id>
  date: string; // YYYY-MM-DD
  kind: Kind;
  claims: string[]; // the claims its numbers are read from
  title: Record<Locale, string>;
  body: Record<Locale, string>;
  receipts: Receipt[];
}

/** The worked example's cloud price: $150 a month of API spend over 200 tasks.
 *  An assumption the claims' verdicts share, not a measurement. */
export const PRICE = 0.75;

const REPORT = "docs/report/2026-10-local-coding-ai.md";
const report = (n: number, anchor: string): Receipt => ({
  label: { en: `Report §${n}`, ja: `レポート §${n}` },
  href: `${blob(REPORT)}#${anchor}`,
});
const LEDGER: Receipt = { label: { en: "Findings ledger", ja: "所見台帳" }, href: blob("brain/state/findings.md") };
const RESULTS: Receipt = { label: { en: "RESULTS.md", ja: "RESULTS.md" }, href: blob("docs/RESULTS.md") };
const pr = (n: number): Receipt => ({ label: { en: `PR #${n}`, ja: `PR #${n}` }, href: `${REPO_URL}/pull/${n}` });
/** The per-attempt file a claim was computed from. */
const artifact = (c: Claim): Receipt => {
  const name = c.artifact.split("/").pop() ?? c.artifact;
  return { label: { en: name, ja: name }, href: blob(c.artifact) };
};

const S3 = "3-local-agents-on-real-repositories";
const S4 = "4-when-local-pays";
const S5 = "5-the-hardware-question";
const S6 = "6-what-we-got-wrong-along-the-way";
const S1 = "1-the-local-tier-stopped-getting-new-models";

// The claims the entries read, by what they measured.
const c = {
  laptop20: claim("report_2026_10_laptop_131k_20min"),
  clock: claim("report_2026_10_laptop_vs_studio_request_clock"),
  q35: claim("report_2026_10_studio_qwen35_outside"),
  q35wide: claim("report_2026_10_studio_qwen35_131k_outside"),
  q36: claim("report_2026_10_studio_qwen36_outside"),
  pair: claim("report_2026_10_qwen35_vs_qwen36_131k_outside"),
  q36after: claim("report_2026_10_qwen36_after_release"),
  q35after: claim("report_2026_10_qwen35_after_release"),
  probe: claim("report_2026_10_laptop_qwen35_mahoraga"),
  granite: claim("report_2026_10_laptop_granite_mahoraga"),
  cascade: claim("humaneval_plus_164_reading_judge"),
  rerun: claim("humaneval_plus_164_code_judge"),
};
const n = (x: Claim, key: string) => num(x, key);
const m = (x: Claim, key: string) => metric(x, key);

// Derived once, from the claims above.
const gap = n(c.q36, "resolved") - n(c.q35, "resolved"); // tasks between the two models at their own contexts
const fromContext = n(c.q35wide, "resolved") - n(c.q35, "resolved"); // the share of it a wider window closes
const split = n(c.pair, "only_this") + n(c.pair, "only_versus"); // tasks the two models disagree on
const keep = n(c.cascade, "routed_pass_at_1") / n(c.cascade, "cloud_pass_at_1");
const costShare = n(c.cascade, "routed_cost_per_1k") / n(c.cascade, "cloud_cost_per_1k");
const judgeCost = (n(c.rerun, "routed_cost_per_1k") - n(c.cascade, "routed_cost_per_1k")).toFixed(2);
const price = `$${PRICE.toFixed(2)}`;
const triage = data.verdict_demo.assumptions.triage_minutes; // assumed, and printed with every verdict

export const LOG: Entry[] = [
  {
    id: "2026-10-28-laptop-131k",
    date: "2026-10-28",
    kind: "running",
    claims: [],
    title: { en: "The laptop's own run at 131k is under way.", ja: "ノート本体での 131k の計測が進行中です。" },
    body: {
      en: "The 16 GB laptop is running qwen3.5 on 47 outside tasks, with 90 minutes an attempt and no shorter clock inside the agent.",
      ja: "16 GB のノートで、qwen3.5 が外部リポジトリの 47 タスクに挑んでいます。1 試行は 90 分で、エージェントの中にそれより短い時計はありません。",
    },
    receipts: [report(5, S5)],
  },
  {
    id: "2026-10-28-two-clocks",
    date: "2026-10-28",
    kind: "correction",
    claims: [c.clock.id],
    title: { en: "A clock inside the agent set the laptop's numbers.", ja: "エージェント内部の時計が、ノートの数字を決めていました。" },
    body: {
      en: `On ${n(c.clock, "pairs")} tasks both machines ran with one model file, the laptop solved ${n(c.clock, "pair_resolved")} and the Studio solved ${n(c.clock, "versus_resolved")}. The logs showed aider cutting each long request at 10 minutes, so a request now gets the attempt's whole limit.`,
      ja: `同じモデルファイルで両方のマシンが走らせた ${n(c.clock, "pairs")} タスクのうち、ノートは ${n(c.clock, "pair_resolved")} 件、Studio は ${n(c.clock, "versus_resolved")} 件を解決しました。ログを見ると aider が長いリクエストを 10 分で打ち切っていたため、今は 1 回のリクエストに試行の制限時間をまるごと与えています。`,
    },
    receipts: [report(6, S6), artifact(c.clock), pr(68)],
  },
  {
    id: "2026-10-15-time-limit",
    date: "2026-10-15",
    kind: "correction",
    claims: [c.laptop20.id],
    title: { en: "A 20-minute limit measured the clock.", ja: "20 分の制限時間は、時計を測っていました。" },
    body: {
      en: `The laptop's first night at 131k timed out on ${n(c.laptop20, "timed_out")} of ${n(c.laptop20, "tasks")} attempts, since reading one long prompt takes it over ten minutes. The verdict now refuses to call a stay decided when most failures are timeouts.`,
      ja: `ノートでの 131k の最初の夜は、長いプロンプトを読むだけで 10 分以上かかるため、${n(c.laptop20, "tasks")} 試行中 ${n(c.laptop20, "timed_out")} 件が時間切れになりました。今は失敗の大半が時間切れのとき、判定は「継続」を確定としません。`,
    },
    receipts: [report(6, S6), pr(63)],
  },
  {
    id: "2026-10-14-context",
    date: "2026-10-14",
    kind: "finding",
    claims: [c.q35.id, c.q35wide.id, c.q36.id],
    title: { en: "Most of the hardware gap was the context window.", ja: "ハードウェアの差の大部分は、コンテキストの長さでした。" },
    body: {
      en: `A 131k context took the model that fits 16 GB from ${n(c.q35, "resolved")} to ${n(c.q35wide, "resolved")} of ${n(c.q35wide, "tasks")} outside tasks. The 35B model that needs 24 GB resolves ${n(c.q36, "resolved")}, so the wider window accounts for ${fromContext} of the ${gap} tasks between them.`,
      ja: `16 GB に収まるモデルは、コンテキストを 131k にすると、外部の ${n(c.q35wide, "tasks")} タスクで解決数が ${n(c.q35, "resolved")} 件から ${n(c.q35wide, "resolved")} 件に増えました。24 GB を要する 35B のモデルは ${n(c.q36, "resolved")} 件なので、両者の差 ${gap} 件のうち ${fromContext} 件はコンテキストの広さで説明できます。`,
    },
    receipts: [report(5, S5), artifact(c.q35wide)],
  },
  {
    id: "2026-10-14-same-context",
    date: "2026-10-14",
    kind: "finding",
    claims: [c.pair.id],
    title: { en: "At the same context, the two models are hard to tell apart.", ja: "同じコンテキストでは、2 つのモデルの差は見分けられません。" },
    body: {
      en: `They disagree on ${split} of the ${n(c.pair, "pairs")} tasks, and a ${n(c.pair, "only_this")} to ${n(c.pair, "only_versus")} split is what chance alone would produce (McNemar p = ${m(c.pair, "mcnemar_p")}).`,
      ja: `${n(c.pair, "pairs")} タスク中 ${split} 件で結果が分かれ、${n(c.pair, "only_this")} 対 ${n(c.pair, "only_versus")} という割れ方は偶然だけでも起こる範囲です（McNemar p = ${m(c.pair, "mcnemar_p")}）。`,
    },
    receipts: [report(5, S5)],
  },
  {
    id: "2026-10-13-answer-leak",
    date: "2026-10-13",
    kind: "void",
    claims: [],
    title: { en: "An agent could read the answer, so its attempt was discarded.", ja: "エージェントが答えを読めたため、その試行は破棄しました。" },
    body: {
      en: "The first opencode attempt ran git show on the commit that held the fix, so every task clone is now rebuilt without it. Aider can't run shell commands, so no aider result was affected.",
      ja: "opencode の最初の試行が修正を含むコミットに git show を実行したため、今はすべてのタスクのクローンをその痕跡なしで作り直しています。aider はシェルコマンドを実行できないので、aider の結果に影響はありません。",
    },
    receipts: [report(6, S6), pr(56)],
  },
  {
    id: "2026-10-13-tool-markup",
    date: "2026-10-13",
    kind: "void",
    claims: [],
    title: { en: "One model's replies measured the interface.", ja: "あるモデルの回答は、インターフェースを測っていました。" },
    body: {
      en: "Ornith-1.5 answered aider in tool-call markup that aider can't apply, so those attempts were set aside and never scored.",
      ja: "ornith-1.5 は aider が適用できないツール呼び出しの形式で答えたため、その試行は採点せずに除外しました。",
    },
    receipts: [report(6, S6)],
  },
  {
    id: "2026-10-07-unseen",
    date: "2026-10-07",
    kind: "finding",
    claims: [c.q36after.id, c.q35after.id],
    title: { en: "The larger model also leads on commits it can't have seen.", ja: "大きいモデルは、学習で見られないコミットでも上回りました。" },
    body: {
      en: `On ${n(c.q36after, "tasks")} outside commits made after its release, qwen3.6 resolves ${n(c.q36after, "resolved")} against qwen3.5's ${n(c.q35after, "resolved")}, and the two intervals don't overlap.`,
      ja: `qwen3.6 の公開後に作られた外部の ${n(c.q36after, "tasks")} 件のコミットで、qwen3.6 は ${n(c.q36after, "resolved")} 件、qwen3.5 は ${n(c.q35after, "resolved")} 件を解決し、2 つの区間は重なりません。`,
    },
    receipts: [report(3, S3)],
  },
  {
    id: "2026-10-07-same-tasks",
    date: "2026-10-07",
    kind: "correction",
    claims: [c.q35.id, c.q36.id],
    title: { en: "The same tasks reversed a reading from the day before.", ja: "同じタスクで比べ直すと、前日の読みが逆になりました。" },
    body: {
      en: `The first Studio comparison gave the two models different tasks and concluded that more memory only made attempts faster. On the same ${n(c.q36, "tasks")} tasks the larger model resolved ${n(c.q36, "resolved")} to the smaller model's ${n(c.q35, "resolved")}.`,
      ja: `Studio での最初の比較は 2 つのモデルに別々のタスクを与え、メモリを増やしても試行が速くなるだけだと結論していました。同じ ${n(c.q36, "tasks")} タスクでは、大きいモデルが ${n(c.q36, "resolved")} 件、小さいモデルが ${n(c.q35, "resolved")} 件を解決しました。`,
    },
    receipts: [report(6, S6)],
  },
  {
    id: "2026-10-02-verdict",
    date: "2026-10-02",
    kind: "finding",
    claims: [c.q35.id],
    title: { en: "A developer's time sets the bar for local.", ja: "ローカルの基準を決めるのは、開発者の時間です。" },
    body: {
      en: `At ${price} a cloud task, local pays only once it resolves ${pct(n(c.q35, "breakeven_rate"))} of queued tasks, because each failure costs ${triage} minutes of triage. On a flat subscription, the engine's answer for most people is that they aren't wasting money.`,
      ja: `クラウドが 1 タスク ${price} なら、失敗のたびに確認で ${triage} 分かかるため、ローカルが元を取るには待ち行列の ${pct(n(c.q35, "breakeven_rate"))} を解決する必要があります。定額プランでは、多くの人にとってエンジンの答えは「お金を無駄にしていない」です。`,
    },
    receipts: [report(4, S4)],
  },
  {
    id: "2026-10-02-tasks",
    date: "2026-10-02",
    kind: "method",
    claims: [c.probe.id],
    title: { en: "Only more tasks narrow the interval.", ja: "区間を狭めるのは、タスクの数だけです。" },
    body: {
      en: `An interval counts tasks, so the ${n(c.probe, "tasks")} tasks of the first probe leave it between ${pct(n(c.probe, "rate_lo"))} and ${pct(n(c.probe, "rate_hi"))} however often they rerun. The bench therefore grew to three outside repositories.`,
      ja: `区間はタスクの数で決まるので、最初のプローブの ${n(c.probe, "tasks")} タスクでは、何度繰り返しても ${pct(n(c.probe, "rate_lo"))}〜${pct(n(c.probe, "rate_hi"))} のままです。そこでベンチを外部の 3 つのリポジトリに広げました。`,
    },
    receipts: [report(3, S3)],
  },
  {
    id: "2026-10-02-src-layout",
    date: "2026-10-02",
    kind: "correction",
    claims: [],
    title: { en: "A src-layout bug tested the wrong copy.", ja: "src レイアウトの不具合で、別のコピーをテストしていました。" },
    body: {
      en: "In two repositories the tests imported the original checkout, and the benchmark's own gates rejected every affected task.",
      ja: "2 つのリポジトリでテストが元のチェックアウトを読み込んでいましたが、ベンチマーク自身のゲートが該当するタスクをすべて除外しました。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-09-30-wrong-folder",
    date: "2026-09-30",
    kind: "void",
    claims: [],
    title: { en: "A second agent ran in the wrong folder.", ja: "二つ目のエージェントが、別のフォルダで動いていました。" },
    body: {
      en: "All 17 of its first attempts were voided, and every agent now runs inside an operating-system sandbox.",
      ja: "その最初の 17 試行はすべて無効にし、今はどのエージェントも OS のサンドボックスの中で動かしています。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-09-30-speed",
    date: "2026-09-30",
    kind: "correction",
    claims: [],
    title: { en: "The laptop never generated 30 tokens a second.", ja: "ノートの生成速度は、毎秒 30 トークンではありませんでした。" },
    body: {
      en: "Measured idle, qwen3.5 reaches about 14.5 tokens a second on the laptop, so speed readings now wait for an idle machine.",
      ja: "待機状態で測ると、qwen3.5 はノートで毎秒約 14.5 トークンです。そのため速度の計測は、マシンが空いているときだけ行います。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-09-29-real-commits",
    date: "2026-09-29",
    kind: "finding",
    claims: [c.probe.id],
    title: { en: "About one real edit in four landed.", ja: "実際の編集は、4 回に 1 回ほど成功しました。" },
    body: {
      en: `On ${n(c.probe, "tasks")} of Mahoraga's own commits, aider with qwen3.5 resolved ${n(c.probe, "resolved")}, and the tests caught every failed attempt. A median attempt took ${m(c.probe, "minutes")} minutes, which is why the engine runs as an overnight queue.`,
      ja: `Mahoraga 自身のコミット ${n(c.probe, "tasks")} 件で、aider と qwen3.5 は ${n(c.probe, "resolved")} 件を解決し、失敗した試行はすべてテストが捕まえました。1 試行の中央値は ${m(c.probe, "minutes")} 分で、だからエンジンは夜間のキューとして動きます。`,
    },
    receipts: [report(3, S3), artifact(c.probe)],
  },
  {
    id: "2026-09-29-interfaces",
    date: "2026-09-29",
    kind: "finding",
    claims: [c.granite.id],
    title: { en: "The best model at prompts was the worst at edits.", ja: "プロンプトで最良のモデルが、編集では最下位でした。" },
    body: {
      en: `Granite 4.1 resolved ${n(c.granite, "resolved")} of the same ${n(c.granite, "tasks")} commits, since most of its edits targeted code that wasn't in the file.`,
      ja: `granite 4.1 が同じ ${n(c.granite, "tasks")} 件のコミットで解決できたのは ${n(c.granite, "resolved")} 件で、編集の多くはファイルにないコードを対象にしていました。`,
    },
    receipts: [report(3, S3)],
  },
  {
    id: "2026-09-11-tier",
    date: "2026-09-11",
    kind: "finding",
    claims: [],
    title: { en: "The small local tier stopped getting new models.", ja: "小型のローカル向けモデルは、新しく出なくなりました。" },
    body: {
      en: "From June to September the flagship open models grew past what a 16 GB machine can run, and no smaller release showed a verified gain.",
      ja: "6 月から 9 月にかけて、主要なオープンモデルは 16 GB のマシンでは動かない大きさになり、小型の新作にも検証済みの向上はありませんでした。",
    },
    receipts: [report(1, S1)],
  },
  {
    id: "2026-09-11-second-pass",
    date: "2026-09-11",
    kind: "correction",
    claims: [],
    title: { en: "A second pass corrected the first on the same day.", ja: "2 回目の調査が、同じ日に 1 回目を訂正しました。" },
    body: {
      en: "Granite 4.1's HumanEval+ score was logged as 80.49, which no source supports, and the model card says 79.88. The first pass also called the small tier vacated, although releases had continued without a verified upgrade.",
      ja: "granite 4.1 の HumanEval+ のスコアは 80.49 と記録されていましたが、裏付けがなく、モデルカードの値は 79.88 です。1 回目は小型帯が「空になった」とも書きましたが、検証済みの改善がないだけで、新作は出続けていました。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-09-10-meter",
    date: "2026-09-10",
    kind: "void",
    claims: [],
    title: { en: "The usage meter read zero because the tool was unplugged.", ja: "利用状況の計測がゼロだったのは、ツールが外れていたからでした。" },
    body: {
      en: 'The integration it measured had broken a month earlier, so that window was voided, and a liveness check now tells "not used" from "not there".',
      ja: "測っていた連携は 1 か月前に壊れていたため、その期間は無効にしました。今は「使われていない」と「つながっていない」を見分ける生存確認があります。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-08-12-receipts",
    date: "2026-08-12",
    kind: "method",
    claims: [],
    title: { en: "Every published number is bound to its data.", ja: "公開する数字は、すべてデータに結びつけています。" },
    body: {
      en: "Each figure names the per-attempt file it came from, and CI recomputes it from that file on every change.",
      ja: "どの数字も、もとになった試行ごとのファイルを名指ししていて、CI が変更のたびにそのファイルから計算し直します。",
    },
    receipts: [RESULTS],
  },
  {
    id: "2026-08-04-replication",
    date: "2026-08-04",
    kind: "finding",
    claims: [c.rerun.id, c.cascade.id],
    title: { en: "The cascade's result replicated, and a stronger judge bought nothing.", ja: "カスケードの結果は再現し、強いジャッジは何ももたらしませんでした。" },
    body: {
      en: `An independent rerun of all ${n(c.rerun, "n")} tasks reached the same ${m(c.rerun, "routed_pass_at_1")} pass@1. A stronger judge raised fail-recall from ${m(c.cascade, "judge_fail_recall")} to ${m(c.rerun, "judge_fail_recall")} and cost $${judgeCost} more per thousand tasks without changing quality.`,
      ja: `${n(c.rerun, "n")} タスクすべてを独立に走らせ直しても、pass@1 は同じ ${m(c.rerun, "routed_pass_at_1")} でした。強いジャッジは失敗の検出率を ${m(c.cascade, "judge_fail_recall")} から ${m(c.rerun, "judge_fail_recall")} に上げましたが、品質は変わらず、1,000 タスクあたり $${judgeCost} 高くつきました。`,
    },
    receipts: [RESULTS, artifact(c.rerun)],
  },
  {
    id: "2026-08-03-cascade",
    date: "2026-08-03",
    kind: "finding",
    claims: [c.cascade.id],
    title: {
      en: `A local-first cascade kept ${pct(keep)} of cloud quality at ${pct(costShare)} of the cost.`,
      ja: `ローカル優先のカスケードは、${pct(costShare)} の費用でクラウドの品質の ${pct(keep)} を保ちました。`,
    },
    body: {
      en: `On ${n(c.cascade, "n")} HumanEval+ tasks, a free local model went first and a local judge sent what it rejected to the cloud. The cascade reached ${m(c.cascade, "routed_pass_at_1")} pass@1 at $${m(c.cascade, "routed_cost_per_1k")} per thousand tasks, against ${m(c.cascade, "cloud_pass_at_1")} at $${m(c.cascade, "cloud_cost_per_1k")} for cloud alone.`,
      ja: `HumanEval+ の ${n(c.cascade, "n")} タスクで、まず無料のローカルモデルが答え、ローカルのジャッジが却下したものだけをクラウドに回しました。カスケードは 1,000 タスクあたり $${m(c.cascade, "routed_cost_per_1k")} で pass@1 ${m(c.cascade, "routed_pass_at_1")} に達し、クラウド単体は $${m(c.cascade, "cloud_cost_per_1k")} で ${m(c.cascade, "cloud_pass_at_1")} でした。`,
    },
    receipts: [RESULTS, artifact(c.cascade)],
  },
  {
    id: "2026-08-03-bandit",
    date: "2026-08-03",
    kind: "null",
    claims: [],
    title: { en: "The learning router never beat taking turns.", ja: "学習するルーターは、順番に回す方式に一度も勝てませんでした。" },
    body: {
      en: "On two banks the bandit scored below simple round-robin between the two local models, so the repository claims no gain for it.",
      ja: "2 つのバンクで、バンディットは 2 つのローカルモデルを交互に使う方式を下回ったため、リポジトリはその効果を主張していません。",
    },
    receipts: [RESULTS],
  },
  {
    id: "2026-07-15-heuristic",
    date: "2026-07-15",
    kind: "correction",
    claims: [],
    title: { en: "A heuristic quality score didn't track working code.", ja: "ヒューリスティックな品質スコアは、動くコードを捉えていませんでした。" },
    body: {
      en: "Checked against hidden tests, the score barely followed correctness, so grading moved to running the code against tests.",
      ja: "隠しテストと照らし合わせると、スコアは正しさとほとんど連動していなかったため、採点はコードをテストで実行する方式に移りました。",
    },
    receipts: [LEDGER],
  },
  {
    id: "2026-04-14-start",
    date: "2026-04-14",
    kind: "start",
    claims: [],
    title: { en: "Mahoraga started as a router in simulation.", ja: "Mahoraga は、シミュレーション上のルーターとして始まりました。" },
    body: {
      en: "The first version learned which agent should take each task, and it was tested on synthetic traffic only.",
      ja: "最初の版はどのエージェントにどのタスクを任せるかを学習するもので、試したのは合成のトラフィックだけでした。",
    },
    receipts: [LEDGER],
  },
];

/** The newest entry's date, which the page shows as the log's last update. */
export const UPDATED = LOG[0].date;

/** An entry by its id, for a row or a link that points at one. */
export function entryById(id: string): Entry {
  const e = LOG.find((x) => x.id === id);
  if (!e) throw new Error(`no log entry ${id}`);
  return e;
}

/** The entry that carries a claim's numbers, for a note that points back at it. */
export function entryFor(claimId: string): Entry {
  const e = LOG.find((x) => x.claims.includes(claimId));
  if (!e) throw new Error(`no log entry reads claim ${claimId}`);
  return e;
}

/** The hero chart: the settings the October report compares on the same tasks,
 *  and the run still filling in the row a 16 GB owner needs. */
export const STANDING = {
  rows: [
    { claim: c.q35.id, model: { en: "qwen3.5, 9.7B, 32k context", ja: "qwen3.5（9.7B）、32k コンテキスト" }, fit: { en: "Fits a 16 GB laptop", ja: "16 GB のノートに収まる" } },
    { claim: c.q35wide.id, model: { en: "qwen3.5, 9.7B, 131k context", ja: "qwen3.5（9.7B）、131k コンテキスト" }, fit: { en: "Fits a 16 GB laptop", ja: "16 GB のノートに収まる" } },
    { claim: c.q36.id, model: { en: "qwen3.6, 35B, 131k context", ja: "qwen3.6（35B）、131k コンテキスト" }, fit: { en: "Needs 24 GB or more", ja: "24 GB 以上が必要" } },
  ],
  running: { entry: "2026-10-28-laptop-131k", model: { en: "qwen3.5, 9.7B, 131k context", ja: "qwen3.5（9.7B）、131k コンテキスト" } },
  breakeven: c.q35wide.id, // the claim whose verdict gives the break-even rate at PRICE
  note: c.pair.id, // the claim whose run record the footnote quotes
};

/** Entries grouped by month, newest first, for the page's month headings. */
export function byMonth(entries: Entry[] = LOG): { year: number; month: number; entries: Entry[] }[] {
  const out: { year: number; month: number; entries: Entry[] }[] = [];
  for (const e of entries) {
    const [year, month] = e.date.split("-").map(Number);
    const last = out[out.length - 1];
    if (last && last.year === year && last.month === month) last.entries.push(e);
    else out.push({ year, month, entries: [e] });
  }
  return out;
}
