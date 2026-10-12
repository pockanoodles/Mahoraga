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
  menu: { menu: string; close: string; items: { href: string; label: string }[] };
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
    sub: "Mahoraga replays your repository's history on a local coding agent and checks every attempt against your own tests. The verdict tells you whether to stay on the cloud, split the work, or switch.",
    cta: "Install Mahoraga",
    more: "or see how it works",
    chartLabel: (n) => `A replay of ${n} real commits from Mahoraga's own history, graded one at a time`,
    axisTasks: "tasks graded",
    breakeven: (usd) => `Break-even at ${usd} a task`,
    resolved: (k, n) => `${k} of ${n} resolved`,
    interval: "95% interval",
    replay: "Replay",
  },
  question: {
    title: "A benchmark made from your own work.",
    lead: "Leaderboards measure someone else's tasks on someone else's hardware, and a vendor will rarely tell you that you don't need its product. Mahoraga measures yours.",
    steps: [
      {
        title: "Tasks from your history",
        body: "Each task is one of your own commits with the fix taken out and the tests left in, so the agent works on problems your codebase really had.",
      },
      {
        title: "Graded by your tests",
        body: "An attempt counts only when the commit's tests pass and nothing that passed before has broken, which the full suite checks every time.",
      },
      {
        title: "Measured on your machine",
        body: "Agents run overnight on the hardware you already own, so the solve rate, the speed and the electricity in the verdict are all yours.",
      },
    ],
  },
  how: {
    title: "One pipeline, from your history to a verdict.",
    lead: "Each stage can be swapped out, and the verdict reads only from the ledger, so the published report and your own verdict come out of the same code.",
    stages: [
      { name: "Mine", body: "Keep the commits whose own tests fail before them and pass after them." },
      { name: "Check", body: "Confirm the bench is ready and estimate how many nights an answer will take." },
      { name: "Run", body: "A local agent attempts every task overnight, each time in a fresh copy of the repository." },
      { name: "Grade", body: "The tests are put back, the full suite runs, and the ledger records what happened and why." },
      { name: "Decide", body: "The measured rate becomes stay, split or switch, priced for the way you pay." },
    ],
    pipeline: "Source, runner, grader, ledger, verdict",
  },
  verdict: {
    title: "A verdict that shows its working.",
    lead: "Move the price of a cloud task and the break-even line slides across the measured interval. The call only changes when the evidence clears the line.",
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
      `Every call, rate and dollar figure here was computed by the engine for an API user with ${tasks} cloud tasks a month, using the published probe as evidence.`,
  },
  evidence: {
    title: "Every number has a receipt.",
    lead: "Each figure below is recomputed from committed per-case results, and the footnote under it says exactly where it came from.",
  },
  corrections: {
    title: "The corrections are published too.",
    lead: "Several numbers in this project were wrong when they were first written, and each one was corrected in the repository before it reached a reader.",
    items: [
      "Granite 4.1's HumanEval+ score was first recorded as 80.49, a figure that appears in no primary source, and the correct value is 79.88.",
      "The local model was described as generating 30 tokens a second, but measured idle on the laptop it reaches about 14.5.",
      "All 17 attempts from the first run of a second agent were voided because it ran in the wrong directory, and every agent now runs in a sandbox.",
      "A src-layout bug quietly tested the wrong copy of two repositories, and the benchmark's own gates rejected every affected task instead of scoring it.",
    ],
    source: "From the October report draft, where each correction links to its commit.",
  },
  app: {
    title: "An app, if you want one.",
    lead: "The engine runs headless from a terminal, and the same server ships a small app that reads the same API on your machine.",
    features: [
      {
        name: "Verdict",
        body: "Change how you pay, how much work can wait and what your time is worth, and the call is recomputed as you go.",
      },
      { name: "Repositories", body: "Every task and every attempt, with what went wrong whenever an attempt failed." },
      {
        name: "Runs",
        body: "Each run's manifest, from the machine and the model digests to the agent versions and the harness commit.",
      },
    ],
    demo: ["To look around before a night of runs,", "opens the app on the published probe."],
    shotAlt: "The Mahoraga app open on the verdict for the published probe",
    pending: "App screenshot",
  },
  headless: {
    title: "Built to be scripted.",
    lead: "The CLI, the HTTP API and the MCP server are three ways into the same engine, so the verdict you script is the verdict the app shows.",
    mcpNote: "Add it to Claude Code or any MCP client, and the router takes tasks from your agent.",
  },
  privacy: {
    title: "Your code stays on your machine.",
    items: [
      { title: "Read-only", body: "The audit only reads your repository, and every attempt happens in a separate copy of it." },
      {
        title: "Sandboxed",
        body: "On macOS, agents run in an operating-system sandbox that can write only to their own copy and reach only the local model server. On Linux they get their own home directory and permission settings instead.",
      },
      {
        title: "The cloud only when asked",
        body: "The audit sends nothing anywhere. The router escalates a task to Claude only when the local judge rejects its answer, at most 25 times a day, and MAHORAGA_CASCADE=off keeps every task on the machine.",
      },
    ],
    link: "Read DATAFLOW.md",
  },
  install: {
    title: "Install it your way.",
    lead: "Mahoraga runs on macOS and Linux with Python 3.12 or newer and Ollama, and it was built on a 16 GB Mac.",
    then: "Then pull a model and look around.",
    steps: ["Pull the local model the probe used", "Open the app on the published probe", "Mine your own repository"],
    pending: "Ships with the first release",
    copy: "Copy",
    copied: "Copied",
  },
  support: {
    title: "Free and open source.",
    lead: "Mahoraga is MIT-licensed and built in the open, so the most useful things you can do are run it on your own repository, report what breaks, and send a fix.",
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
    close: "Close",
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
    sub: "Mahoraga は、リポジトリの履歴をローカルのコーディングエージェントで再現し、すべての試行をあなた自身のテストで採点します。そのうえで、クラウドを続けるか、仕事を分けるか、乗り換えるかを判定します。",
    cta: "Mahoraga をインストール",
    more: "仕組みを見る",
    chartLabel: (n) => `Mahoraga 自身の履歴から実際のコミット ${n} 件を再現し、1 件ずつ採点した結果`,
    axisTasks: "採点したタスク",
    breakeven: (usd) => `1 タスク ${usd} のときの損益分岐点`,
    resolved: (k, n) => `${n} 件中 ${k} 件を解決`,
    interval: "95% 区間",
    replay: "もう一度",
  },
  question: {
    title: "あなた自身の仕事でつくるベンチマーク。",
    lead: "リーダーボードが測るのは、他人のタスクを他人のハードウェアで動かした結果です。そしてベンダーが「この製品はあなたには不要です」と言うことは、まずありません。Mahoraga が測るのは、あなたの仕事です。",
    steps: [
      {
        title: "履歴からつくるタスク",
        body: "各タスクは、あなた自身のコミットから修正だけを取り除き、テストを残したものです。エージェントは、そのコードベースで実際に起きた問題に取り組みます。",
      },
      {
        title: "あなたのテストで採点",
        body: "コミットのテストが通り、以前は通っていたテストが一つも壊れていないときだけ、その試行を成功と数えます。確認には毎回フルスイートを使います。",
      },
      {
        title: "あなたのマシンで計測",
        body: "エージェントは手元のハードウェアで夜のあいだに動きます。判定に使う解決率も、速度も、電気代も、すべてあなたの環境の数字です。",
      },
    ],
  },
  how: {
    title: "履歴から判定まで、ひとつのパイプラインで。",
    lead: "どの段階も差し替えられます。判定は台帳だけを読むので、公開レポートの判定とあなたの判定は、同じコードから出てきます。",
    stages: [
      { name: "抽出", body: "自身のテストが、そのコミットの前では失敗し、後では通るコミットだけを残します。" },
      { name: "点検", body: "ベンチの準備が整っているかを確かめ、答えが出るまでに何晩かかるかを見積もります。" },
      { name: "実行", body: "ローカルのエージェントが、夜のあいだにすべてのタスクに挑みます。毎回、リポジトリの新しいコピーで動きます。" },
      { name: "採点", body: "テストを元に戻してフルスイートを走らせ、何が起きたかを理由とともに台帳へ記録します。" },
      { name: "判定", body: "測った解決率を、あなたの支払い方に合わせて「継続」「分担」「移行」のいずれかに変えます。" },
    ],
    pipeline: "ソース、ランナー、採点、台帳、判定",
  },
  verdict: {
    title: "根拠ごと見せる判定。",
    lead: "クラウドの 1 タスクあたりの価格を動かすと、損益分岐点の線が測定区間の上を横切ります。判定が変わるのは、根拠がその線を越えたときだけです。",
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
      `判定、解決率、金額はすべて、公開済みのプローブを根拠に、月 ${tasks} 件のクラウドタスクを API で使う利用者を想定してエンジンが計算したものです。`,
  },
  evidence: {
    title: "すべての数字に、出どころがある。",
    lead: "以下の数字はどれも、コミット済みのケースごとの結果から計算し直したものです。脚注に、それぞれの出どころを書いています。",
  },
  corrections: {
    title: "訂正も、公開しています。",
    lead: "このプロジェクトでは、最初に書いた数字が間違っていたことが何度かあります。どれも、読者に届く前にリポジトリで訂正しました。",
    items: [
      "Granite 4.1 の HumanEval+ のスコアは当初 80.49 と記録していましたが、この数字はどの一次資料にもなく、正しくは 79.88 です。",
      "ローカルモデルの生成速度を毎秒 30 トークンと書いていましたが、ノートパソコンのアイドル時に測ると約 14.5 トークンでした。",
      "二つ目のエージェントの最初の実行では、作業ディレクトリを誤っていたため 17 件の試行をすべて無効にしました。現在はすべてのエージェントをサンドボックスで動かしています。",
      "src レイアウトの不具合で、二つのリポジトリでは別のコピーを黙ってテストしていました。ベンチマーク自身のゲートが、影響を受けたタスクを採点せずにすべて除外しました。",
    ],
    source: "10 月のレポート草稿より。各訂正には、その修正コミットへのリンクがあります。",
  },
  app: {
    title: "必要なら、アプリも。",
    lead: "エンジンはターミナルからヘッドレスで動きます。同じサーバーには、手元で同じ API を読む小さなアプリも入っています。",
    features: [
      { name: "判定", body: "支払い方、待てる仕事の割合、自分の時間の価値を変えると、その場で判定を計算し直します。" },
      { name: "リポジトリ", body: "すべてのタスクとすべての試行を、失敗した試行は何がまずかったのかと一緒に表示します。" },
      { name: "実行記録", body: "各実行のマニフェストを表示します。マシンやモデルのダイジェストから、エージェントのバージョン、ハーネスのコミットまで記録されています。" },
    ],
    demo: ["一晩走らせる前に試すなら、", "で公開済みのプローブを開けます。"],
    shotAlt: "公開済みプローブの判定を開いた Mahoraga のアプリ",
    pending: "アプリの画面",
  },
  headless: {
    title: "スクリプトから使うための設計。",
    lead: "CLI、HTTP API、MCP サーバーは、同じエンジンへの三つの入り口です。スクリプトで出した判定と、アプリに表示される判定は同じものです。",
    mcpNote: "Claude Code などの MCP クライアントに追加すると、エージェントからルーターへタスクを渡せます。",
  },
  privacy: {
    title: "コードは、手元から出ていきません。",
    items: [
      { title: "読み取りのみ", body: "監査はリポジトリを読むだけで、試行はすべて別のコピーの中で行います。" },
      {
        title: "サンドボックス",
        body: "macOS では、エージェントは OS のサンドボックスの中で動き、書き込めるのは自分のコピーだけ、通信できるのはローカルのモデルサーバーだけです。Linux では、専用のホームディレクトリと権限設定で分離します。",
      },
      {
        title: "クラウドは必要なときだけ",
        body: "監査は何も外部へ送りません。ルーターがタスクを Claude に回すのは、ローカルのジャッジが回答を却下したときだけで、上限は 1 日 25 回です。MAHORAGA_CASCADE=off にすれば、すべてのタスクが手元に残ります。",
      },
    ],
    link: "DATAFLOW.md を読む",
  },
  install: {
    title: "好きな方法で、インストール。",
    lead: "Mahoraga は、Python 3.12 以降と Ollama があれば macOS と Linux で動きます。開発には 16 GB の Mac を使っています。",
    then: "モデルを取得して、試してみましょう。",
    steps: ["プローブで使ったローカルモデルを取得する", "公開済みのプローブでアプリを開く", "自分のリポジトリから抽出する"],
    pending: "最初のリリースで公開",
    copy: "コピー",
    copied: "コピーしました",
  },
  support: {
    title: "無料で、オープンソース。",
    lead: "Mahoraga は MIT ライセンスで、公開の場で開発しています。いちばん助かるのは、自分のリポジトリで動かしてみること、壊れたところを報告すること、そして修正を送ることです。",
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
    close: "閉じる",
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
