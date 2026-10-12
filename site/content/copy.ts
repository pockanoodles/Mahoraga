// Every user-facing string on the landing page, in both languages, behind one
// interface: a missing Japanese line is a type error, not English on /ja.
// Measured numbers are never typed here. Each sentence that carries one takes
// it as an argument from the engine's export (lib/data.ts).
import type { CurvePoint } from "@/lib/data";

export type Locale = "en" | "ja";

export interface Highlight {
  value: string;
  text: string;
  note: number; // footnote number
}

export interface Dict {
  meta: { title: string; description: string };
  nav: { how: string; evidence: string; app: string; install: string; github: string; label: string };
  hero: {
    statement: string;
    sub: string;
    cta: string;
    more: string;
    chartLabel: (n: number) => string;
    axisTasks: string;
    breakeven: (usd: string) => string;
    resolved: (k: number, n: number) => string;
    interval: string;
    replay: string;
  };
  question: { title: string; lead: string; steps: { title: string; body: string }[] };
  how: { title: string; lead: string; stages: { name: string; body: string }[]; pipeline: string };
  verdict: {
    title: string;
    lead: string;
    slider: string;
    perTask: string;
    status: { decided: string; open: string };
    words: Record<CurvePoint["call"], string>;
    means: Record<CurvePoint["call"], string>;
    reason: (p: CurvePoint, engineReason: string) => string;
    facts: { measured: string; breakeven: string; net: string; netNote: (lo: string, hi: string) => string };
    measuredNote: (tasks: number) => string;
    assumed: string;
    assumptions: (a: { tasks: number; share: string; triage: number; hourly: number; watts: number; kwh: number }) => string[];
    note: (tasks: number) => string;
  };
  evidence: { title: string; lead: string };
  corrections: { title: string; lead: string; items: string[]; source: string };
  app: {
    title: string;
    lead: string;
    features: { name: string; body: string }[];
    demo: [string, string];
    shotAlt: string;
    pending: string;
  };
  headless: { title: string; lead: string; mcpNote: string };
  privacy: { title: string; items: { title: string; body: string }[]; link: string };
  install: {
    title: string;
    lead: string;
    then: string;
    steps: string[];
    pending: string;
    copy: string;
    copied: string;
  };
  support: { title: string; lead: string; star: string; contribute: string; issues: string; sponsor: string };
  outcomes: Record<string, string>;
  footnotesTitle: string;
  footer: {
    made: string;
    license: string;
    product: string;
    project: string;
    person: string;
    portfolio: string;
    contact: string;
    contributing: string;
    security: string;
    dataflow: string;
    results: string;
    language: string;
  };
  menu: { menu: string; items: { href: string; label: string }[] };
}

const en: Dict = {
  meta: {
    title: "Mahoraga: is local coding AI worth it for your code?",
    description:
      "Mahoraga replays your repository's history on a local coding agent, grades every attempt with your own tests, and tells you whether to stay on the cloud, split the work, or switch.",
  },
  nav: { how: "How it works", evidence: "Evidence", app: "The app", install: "Install", github: "GitHub", label: "Mahoraga" },
  hero: {
    statement: "Is local coding AI worth it for your code?",
    sub: "Mahoraga runs a local coding agent on your own commits, grades it with your tests, and tells you whether to stay on the cloud, split the work, or switch.",
    cta: "Install Mahoraga",
    more: "or see how it works",
    chartLabel: (n) => `${n} real commits from Mahoraga's history, graded one at a time`,
    axisTasks: "tasks graded",
    breakeven: (usd) => `Break-even at ${usd} a task`,
    resolved: (k, n) => `${k} of ${n} resolved`,
    interval: "95% interval",
    replay: "Replay",
  },
  question: {
    title: "A benchmark made from your own work.",
    lead: "Leaderboards measure someone else's tasks, and vendors rarely tell you to buy less. Mahoraga measures your own.",
    steps: [
      {
        title: "Tasks from your history",
        body: "Each task is one of your commits with the fix removed and the tests kept.",
      },
      {
        title: "Graded by your tests",
        body: "An attempt counts only when its tests pass and nothing else breaks.",
      },
      {
        title: "Measured on your machine",
        body: "Agents run overnight on your hardware, so every number in the verdict is yours.",
      },
    ],
  },
  how: {
    title: "One pipeline, from your history to a verdict.",
    lead: "Every stage is swappable, and the published report and your verdict come from the same code.",
    stages: [
      { name: "Mine", body: "Keep commits whose tests fail before them and pass after." },
      { name: "Check", body: "Confirm the bench works and estimate the nights it needs." },
      { name: "Run", body: "A local agent attempts every task overnight, in a fresh copy." },
      { name: "Grade", body: "The tests go back in, the full suite runs, and the ledger records it." },
      { name: "Decide", body: "The measured rate becomes stay, split, or switch for how you pay." },
    ],
    pipeline: "Source, runner, grader, ledger, verdict",
  },
  verdict: {
    title: "A verdict that shows its working.",
    lead: "Drag the price of a cloud task. The call changes only when the evidence clears the break-even line.",
    slider: "What one cloud task costs you",
    perTask: "a task",
    status: { decided: "Decided", open: "Not yet decided" },
    words: { stay: "Stay", split: "Split", switch: "Switch" },
    means: {
      stay: "Keep this work on the cloud.",
      split: "Send the work that can wait to local.",
      switch: "Move this work to local.",
    },
    reason: (_p, engineReason) => engineReason,
    facts: {
      measured: "Measured",
      breakeven: "Break-even",
      net: "Saving per month",
      netNote: (lo, hi) => `${lo} to ${hi}`,
    },
    measuredNote: (tasks) => `over ${tasks} tasks, 95% interval`,
    assumed: "Assumed, not measured",
    assumptions: (a) => [
      `${a.tasks} cloud coding tasks a month`,
      `${a.share} of them can wait for an overnight queue`,
      `${a.triage} min to triage a failed local attempt, at $${a.hourly}/h`,
      `${a.watts} W while an attempt runs, at $${a.kwh}/kWh`,
    ],
    note: (tasks) =>
      `Computed by the engine from the published probe, for an API user with ${tasks} cloud tasks a month.`,
  },
  evidence: {
    title: "Every number has a receipt.",
    lead: "Each figure is recomputed from committed results, and its footnote says where.",
  },
  corrections: {
    title: "The corrections are published too.",
    lead: "Some numbers were wrong when first written, and each was fixed before it reached a reader.",
    items: [
      "Granite 4.1's HumanEval+ score was first logged as 80.49, which no source supports, and the real value is 79.88.",
      "Local generation was quoted at 30 tokens a second, but the laptop measures about 14.5.",
      "A second agent's first 17 attempts ran in the wrong folder, so they were voided and every agent is now sandboxed.",
      "A src-layout bug tested the wrong copy of two repos, and the benchmark's gates rejected every affected task.",
    ],
    source: "Each correction links to its commit in the October report draft.",
  },
  app: {
    title: "An app, if you want one.",
    lead: "The engine is headless, and the same server ships a small app on top of its API.",
    features: [
      {
        name: "Verdict",
        body: "Change how you pay or what your time is worth, and the call updates.",
      },
      { name: "Repositories", body: "Every task and attempt, with what went wrong when one failed." },
      {
        name: "Runs",
        body: "Each run's manifest, from model digests to the harness commit.",
      },
    ],
    demo: ["Before your first night of runs,", "opens the app on the published probe."],
    shotAlt: "The Mahoraga app open on the verdict for the published probe",
    pending: "App screenshot",
  },
  headless: {
    title: "Built to be scripted.",
    lead: "The CLI, the HTTP API, and the MCP server all reach the same engine, so a scripted verdict matches the app.",
    mcpNote: "Add it to Claude Code or any MCP client to hand the router tasks.",
  },
  privacy: {
    title: "Your code stays on your machine.",
    items: [
      { title: "Read-only", body: "The audit only reads your repo, and every attempt works on a copy." },
      {
        title: "Sandboxed",
        body: "On macOS, agents can write only to their copy and reach only the local model. On Linux they get their own home folder instead.",
      },
      {
        title: "The cloud only when asked",
        body: "The audit sends nothing. The router escalates to Claude only when the local judge rejects an answer, at most 25 times a day.",
      },
    ],
    link: "Read DATAFLOW.md",
  },
  install: {
    title: "Install it your way.",
    lead: "It runs on macOS and Linux with Python 3.12+ and Ollama, and it was built on a 16 GB Mac.",
    then: "Then pull a model and look around.",
    steps: ["Pull the local model the probe used", "Open the app on the published probe", "Mine your own repository"],
    pending: "Ships with the first release",
    copy: "Copy",
    copied: "Copied",
  },
  support: {
    title: "Free and open source.",
    lead: "Mahoraga is MIT-licensed. Run it on your repo, report what breaks, and send fixes.",
    star: "Star on GitHub",
    contribute: "Contributing guide",
    issues: "Open an issue",
    sponsor: "Sponsor",
  },
  outcomes: {
    resolved: "Resolved",
    partial: "Partial",
    "wrong-fix": "Wrong fix",
    "broke-other": "Broke other code",
    "partial+broke": "Partial, broke other code",
    "wrong-file": "Wrong file",
    "no-edit": "No edit",
  },
  footnotesTitle: "Notes",
  footer: {
    made: "An open-source project by Kaito Soeno.",
    license: "MIT License",
    product: "Product",
    project: "Project",
    person: "Kaito Soeno",
    portfolio: "Portfolio",
    contact: "Contact",
    contributing: "Contributing",
    security: "Security",
    dataflow: "Data flow",
    results: "Results",
    language: "日本語",
  },
  menu: {
    menu: "Menu",
    items: [
      { href: "/", label: "Contact" },
      { href: "/portfolio", label: "Portfolio" },
      { href: "/experience", label: "Experience" },
      { href: "/about", label: "About" },
    ],
  },
};

const jaWord: Record<CurvePoint["call"], string> = { stay: "継続", split: "分担", switch: "移行" };

const ja: Dict = {
  meta: {
    title: "Mahoraga：ローカルのコーディング AI は、あなたのコードで割に合うのか",
    description:
      "Mahoraga は、リポジトリの履歴をローカルのコーディングエージェントで再現し、すべての試行をあなた自身のテストで採点して、クラウドを続けるか、仕事を分けるか、乗り換えるかを判定します。",
  },
  nav: { how: "仕組み", evidence: "根拠", app: "アプリ", install: "インストール", github: "GitHub", label: "Mahoraga" },
  hero: {
    statement: "ローカルのコーディング AI は、あなたのコードで割に合うのか。",
    sub: "Mahoraga は、あなたのコミットでローカルのコーディングエージェントを走らせ、あなたのテストで採点し、クラウドを続けるか、分けるか、乗り換えるかを判定します。",
    cta: "Mahoraga をインストール",
    more: "仕組みを見る",
    chartLabel: (n) => `Mahoraga の履歴にある実際のコミット ${n} 件を、1 件ずつ採点`,
    axisTasks: "採点したタスク",
    breakeven: (usd) => `1 タスク ${usd} のときの損益分岐点`,
    resolved: (k, n) => `${n} 件中 ${k} 件を解決`,
    interval: "95% 区間",
    replay: "もう一度",
  },
  question: {
    title: "あなた自身の仕事でつくるベンチマーク。",
    lead: "リーダーボードが測るのは他人のタスクで、ベンダーは「買う量を減らせ」とは言いません。Mahoraga は、あなた自身の仕事を測ります。",
    steps: [
      {
        title: "履歴からつくるタスク",
        body: "各タスクは、あなたのコミットから修正だけを外し、テストを残したものです。",
      },
      {
        title: "あなたのテストで採点",
        body: "テストが通り、他に何も壊れていないときだけ、試行を成功と数えます。",
      },
      {
        title: "あなたのマシンで計測",
        body: "エージェントは手元のマシンで夜に動くので、判定の数字はすべてあなたのものです。",
      },
    ],
  },
  how: {
    title: "履歴から判定まで、ひとつのパイプラインで。",
    lead: "どの段階も差し替えられ、公開レポートとあなたの判定は同じコードから出てきます。",
    stages: [
      { name: "抽出", body: "テストがコミットの前では失敗し、後では通るものだけを残します。" },
      { name: "点検", body: "ベンチが動くかを確かめ、必要な晩数を見積もります。" },
      { name: "実行", body: "ローカルのエージェントが、夜のあいだに新しいコピーで全タスクに挑みます。" },
      { name: "採点", body: "テストを戻してフルスイートを走らせ、結果を台帳に記録します。" },
      { name: "判定", body: "測った解決率を、支払い方に合わせて「継続」「分担」「移行」に変えます。" },
    ],
    pipeline: "ソース、ランナー、採点、台帳、判定",
  },
  verdict: {
    title: "根拠ごと見せる判定。",
    lead: "クラウドの 1 タスクあたりの価格を動かしてください。判定が変わるのは、根拠が損益分岐点を越えたときだけです。",
    slider: "クラウドの 1 タスクあたりの費用",
    perTask: "1 タスク",
    status: { decided: "確定", open: "未確定" },
    words: jaWord,
    means: {
      stay: "この仕事はクラウドのままで問題ありません。",
      split: "待てる仕事は、ローカルに回しましょう。",
      switch: "この仕事は、ローカルに移しましょう。",
    },
    // The engine writes its reasons in English, so the Japanese sentence is
    // built from the same call and the same numbers the engine returned.
    reason: (p) => {
      const lo = usdJa(p.monthly_usd.lo);
      const mid = usdJa(p.monthly_usd.point);
      const hi = usdJa(p.monthly_usd.hi);
      if (p.call === "stay" && p.confident)
        return `区間の楽観的な端でも、ローカルに回して得られる月あたりの差額は ${hi} にとどまります。お金を無駄にしているわけではありません。`;
      if (p.call === "stay")
        return `測定値では月 ${mid} で、まだ割に合いません。ただし区間の楽観的な端では月 ${hi} に届くため、判定は確定していません。`;
      if (p.call === "split" && p.confident)
        return `区間の悲観的な端でも、待てる仕事をローカルに回すと月 ${lo} 以上の差額が出ます。`;
      if (p.call === "split")
        return `月 ${mid} ほどの差額が見込めますが、区間の悲観的な端は月 ${lo} です。頼る前に、もう少し測りましょう。`;
      return `ローカルの解決率と待てる仕事の割合がともに十分高いため、この仕事はローカルに移せます。`;
    },
    facts: {
      measured: "測定値",
      breakeven: "損益分岐点",
      net: "月あたりの節約額",
      netNote: (lo, hi) => `${lo} 〜 ${hi}`,
    },
    measuredNote: (tasks) => `${tasks} タスク、95% 区間`,
    assumed: "測定ではなく、仮定した値",
    assumptions: (a) => [
      `クラウドでのコーディングタスクは月 ${a.tasks} 件`,
      `そのうち ${a.share} は夜間のキューで待てる`,
      `失敗したローカルの試行の確認に ${a.triage} 分（時給 $${a.hourly}）`,
      `試行中の消費電力は ${a.watts} W（1 kWh あたり $${a.kwh}）`,
    ],
    note: (tasks) =>
      `公開済みのプローブを根拠に、月 ${tasks} 件のクラウドタスクを API で使う場合をエンジンが計算しました。`,
  },
  evidence: {
    title: "すべての数字に、出どころがある。",
    lead: "どの数字もコミット済みの結果から計算し直したもので、出どころは脚注にあります。",
  },
  corrections: {
    title: "訂正も、公開しています。",
    lead: "最初に書いた数字が間違っていたことがあり、どれも読者に届く前に直しました。",
    items: [
      "Granite 4.1 の HumanEval+ のスコアを当初 80.49 としていましたが、裏付けがなく、正しくは 79.88 です。",
      "生成速度を毎秒 30 トークンとしていましたが、ノートパソコンでの実測は約 14.5 でした。",
      "二つ目のエージェントは最初の 17 件を誤ったフォルダで実行したため無効にし、今はすべてサンドボックスで動かしています。",
      "src レイアウトの不具合で二つのリポジトリの別コピーをテストしていましたが、ベンチマークのゲートが該当タスクをすべて除外しました。",
    ],
    source: "各訂正は、10 月のレポート草稿で修正コミットにリンクしています。",
  },
  app: {
    title: "必要なら、アプリも。",
    lead: "エンジンはヘッドレスで、同じサーバーにはその API を使う小さなアプリも入っています。",
    features: [
      { name: "判定", body: "支払い方や時間の価値を変えると、判定がその場で更新されます。" },
      { name: "リポジトリ", body: "すべてのタスクと試行を、失敗の理由と一緒に表示します。" },
      { name: "実行記録", body: "各実行のマニフェストを、モデルのダイジェストからハーネスのコミットまで表示します。" },
    ],
    demo: ["最初の一晩を走らせる前に、", "で公開済みのプローブを開けます。"],
    shotAlt: "公開済みプローブの判定を開いた Mahoraga のアプリ",
    pending: "アプリの画面",
  },
  headless: {
    title: "スクリプトから使うための設計。",
    lead: "CLI、HTTP API、MCP サーバーはどれも同じエンジンにつながるので、スクリプトの判定はアプリと一致します。",
    mcpNote: "Claude Code などの MCP クライアントに追加すれば、ルーターにタスクを渡せます。",
  },
  privacy: {
    title: "コードは、手元から出ていきません。",
    items: [
      { title: "読み取りのみ", body: "監査はリポジトリを読むだけで、試行はすべてコピーの上で行います。" },
      {
        title: "サンドボックス",
        body: "macOS では、エージェントが書き込めるのは自分のコピーだけで、通信先はローカルのモデルだけです。Linux では専用のホームフォルダで分離します。",
      },
      {
        title: "クラウドは必要なときだけ",
        body: "監査は何も送りません。ルーターが Claude に回すのは、ローカルのジャッジが回答を却下したときだけで、1 日 25 回までです。",
      },
    ],
    link: "DATAFLOW.md を読む",
  },
  install: {
    title: "好きな方法で、インストール。",
    lead: "Python 3.12 以降と Ollama があれば macOS と Linux で動き、16 GB の Mac で開発しています。",
    then: "モデルを取得して、試してみましょう。",
    steps: ["プローブで使ったローカルモデルを取得する", "公開済みのプローブでアプリを開く", "自分のリポジトリから抽出する"],
    pending: "最初のリリースで公開",
    copy: "コピー",
    copied: "コピーしました",
  },
  support: {
    title: "無料で、オープンソース。",
    lead: "MIT ライセンスです。自分のリポジトリで動かし、壊れたところを報告し、修正を送ってください。",
    star: "GitHub でスターする",
    contribute: "コントリビューションガイド",
    issues: "Issue を開く",
    sponsor: "スポンサーになる",
  },
  outcomes: {
    resolved: "解決",
    partial: "一部のみ",
    "wrong-fix": "誤った修正",
    "broke-other": "他のコードを破損",
    "partial+broke": "一部のみ、他を破損",
    "wrong-file": "別のファイルを編集",
    "no-edit": "編集なし",
  },
  footnotesTitle: "注記",
  footer: {
    made: "Kaito Soeno によるオープンソースプロジェクト。",
    license: "MIT ライセンス",
    product: "製品",
    project: "プロジェクト",
    person: "Kaito Soeno",
    portfolio: "作品",
    contact: "お問い合わせ",
    contributing: "コントリビューション",
    security: "セキュリティ",
    dataflow: "データフロー",
    results: "結果",
    language: "English",
  },
  menu: {
    menu: "メニュー",
    items: [
      { href: "/ja", label: "お問い合わせ" },
      { href: "/ja/portfolio", label: "作品" },
      { href: "/ja/experience", label: "経歴" },
      { href: "/ja/about", label: "プロフィール" },
    ],
  },
};

function usdJa(x: number): string {
  const abs = Math.round(Math.abs(x)).toLocaleString("en-US");
  return x < 0 ? `−$${abs}` : `$${abs}`;
}

export const copy: Record<Locale, Dict> = { en, ja };
