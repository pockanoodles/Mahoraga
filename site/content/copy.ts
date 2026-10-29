// Every user-facing string on the landing page, in both languages, behind one
// interface: a missing Japanese line is a type error, not English on /ja.
// Measured numbers are never typed here. Each sentence that carries one takes
// it as an argument from the engine's export (lib/data.ts). The log's entries
// live in content/log.ts under the same rule.
import type { CurvePoint } from "@/lib/data";
import type { Kind } from "./log";

export type Locale = "en" | "ja";

export interface Dict {
  meta: Record<"research" | "engine" | "install", { title: string; description: string }>;
  nav: { research: string; engine: string; install: string; github: string; label: string; updated: (date: string) => string };
  hero: { eyebrow: string; statement: string; sub: string; cta: string; more: string };
  standing: {
    title: (n: number) => string;
    caption: (n: number) => string;
    count: (k: number, n: number) => string;
    breakeven: (rate: string, price: string) => string;
    row: (model: string, rate: string, k: number, n: number, lo: string, hi: string) => string;
    running: string;
    runningNote: string;
  };
  specimen: {
    title: string;
    lead: string;
    fix: (files: string) => string;
    agent: string;
    resolved: string;
    unresolved: string;
    tests: (pass: number, total: number, broke: number, kept: string) => string;
    minutes: (m: string) => string;
    tokens: (sent: string, recv: string, calls: number) => string;
    machine: (machine: string, date: string) => string;
    picked: string;
  };
  settings: {
    title: string;
    lead: (a: { k32: number; k131: number; k35: number; n: number }) => string;
    label: (n: number, repos: string[]) => string;
    choose: string;
    options: Record<string, { label: string; note: string }>;
    count: (n: number) => string;
    baseline: string;
    change: (gained: number, lost: number, from: string, p: number) => string;
    solved: string;
    missed: string;
    changed: string;
    order: string;
    keys: string;
    hint: string;
    open: string;
    commit: string;
    advice: string;
  };
  log: {
    title: string;
    lead: string;
    kinds: Record<Kind, string>;
    month: (year: number, month: number) => string;
    sources: string;
    ledger: string;
    ledgerLink: string;
    feed: string;
    follow: string;
  };
  engine: {
    title: string;
    lead: string;
    stages: { name: string; body: string }[];
    pipeline: string;
    keeps: { title: string; body: string }[];
    replayTitle: string;
    replayLead: string;
  };
  replay: {
    chartLabel: (n: number) => string;
    axisTasks: string;
    breakeven: (usd: string) => string;
    resolved: (k: number, n: number) => string;
    interval: string;
    replay: string;
  };
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
    note: (date: string, tasks: number, perMonth: number) => string;
    triage: string;
    minutes: (m: number) => string;
    costs: (power: string, time: string) => string;
  };
  headless: { title: string; lead: string; mcpNote: string; viewer: string };
  privacy: { title: string; items: { title: string; body: string }[]; link: string };
  run: {
    title: string;
    lead: string;
    steps: [string, string, string];
    morning: string;
    pending: (names: string) => string;
    copy: string;
    copied: string;
  };
  support: { title: string; lead: string; star: string; contribute: string; issues: string; sponsor: string };
  outcomes: Record<string, string>;
  footnotesTitle: string;
  notes: {
    standing: (a: { dates: string; machine: string; agent: string; artifact: string }) => string;
    verdict: string;
    ref: (n: number) => string;
    source: [string, string, string];
  };
  footer: {
    made: string;
    license: string;
    page: string;
    project: string;
    person: string;
    report: string;
    portfolio: string;
    contact: string;
    privacy: string;
    contributing: string;
    security: string;
    dataflow: string;
    results: string;
    language: string;
  };
  menu: { menu: string; items: { href: string; label: string }[] };
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const en: Dict = {
  meta: {
    research: {
      title: "Mahoraga: local coding AI, measured on real code",
      description:
        "Mahoraga is an open engine that measures local coding agents on real commits and logs every result with the data behind it.",
    },
    engine: {
      title: "Mahoraga: how the engine reaches a verdict",
      description:
        "Mahoraga grades a local agent on your repository's own commits and turns the share it resolves into a stay, split, or switch verdict.",
    },
    install: {
      title: "Mahoraga: install it and run it on your own repository",
      description:
        "Install Mahoraga from source and queue a night's run on your own repository, on macOS or Linux with Python 3.12+ and Ollama.",
    },
  },
  nav: { research: "Research", engine: "Engine", install: "Install", github: "GitHub", label: "Mahoraga", updated: (d) => `Updated ${d}` },
  hero: {
    eyebrow: "Where local stands, October 2026",
    statement: "For most people, the answer is still to keep paying for cloud.",
    sub: "Mahoraga measures local coding AI on real code, and this page is its log. Each attempt is graded by the repository's own tests, and every number here is recomputed from committed data.",
    cta: "Read the log",
    more: "See the engine",
  },
  standing: {
    title: (n) => `Real commits resolved, out of ${n}`,
    caption: (n) =>
      `Each measured row is one setting on the same ${n} commits from more-itertools, click and attrs. Those rows ran on a Mac Studio with the laptop's exact model file for qwen3.5, and the laptop's own run is the last row.`,
    count: (k, n) => `${k} of ${n}`,
    breakeven: (rate, price) => `Local pays above ${rate} at ${price} a cloud task`,
    row: (model, rate, k, n, lo, hi) => `${model}: ${rate} resolved, ${k} of ${n} tasks, 95% interval ${lo} to ${hi}`,
    running: "Running",
    runningNote: "On the 16 GB laptop, through October 31",
  },
  specimen: {
    title: "Each task is a real commit with the fix taken out.",
    lead: "The agent starts from the code as it stood before the fix, and the commit's own tests decide whether its attempt counts.",
    fix: (files) => `The fix the bench took out of ${files}`,
    agent: "What the agent wrote in its place",
    resolved: "Resolved",
    unresolved: "Not resolved",
    tests: (pass, total, broke, kept) =>
      `${pass === total ? (total === 1 ? "The commit's one test now passes" : total === 2 ? "Both of the commit's tests now pass" : `All ${total} of the commit's tests now pass`) : `${pass} of the commit's ${total} tests now pass`}, and ${
        broke === 0 ? `none of the ${kept} tests that passed before broke.` : `${broke} of the ${kept} tests that passed before broke.`
      }`,
    minutes: (m) => `${m} minutes for the attempt`,
    tokens: (sent, recv, calls) => `About ${sent} tokens read and ${recv} written, over ${calls} model ${calls === 1 ? "call" : "calls"}`,
    machine: (machine, date) => `${machine}, the night of ${date}`,
    picked:
      "This is the first task of the laptop's own run at 131k, which is still under way. It was picked by its position and not by its outcome, and one attempt says nothing about the rate.",
  },
  settings: {
    title: "The upgrade that mattered was a setting.",
    lead: ({ k32, k131, k35, n }) =>
      `A 131k context window took the laptop's model from ${k32} to ${k131} of ${n} commits. A 35B model that needs 24 GB or more solved ${k35}, ${
        k35 > k131 ? `which is only ${k35 - k131} more` : k35 === k131 ? "the same number" : `which is ${k131 - k35} fewer`
      }.`,
    label: (n, repos) => `${n} commits from ${andList(repos)}, one square each`,
    choose: "Setting",
    options: {
      "32k": { label: "qwen3.5 at 32k", note: "The bench's default window. A long file overflows it, and Ollama cuts the prompt to fit without an error." },
      "131k": { label: "qwen3.5 at 131k", note: "The same model file, with a window big enough for the files it reads." },
      "35b": { label: "qwen3.6 35B at 131k", note: "A 35B model, which needs 24 GB of memory or more." },
    },
    count: (n) => `of ${n} commits solved`,
    baseline: "Choose a larger window or a larger model to see which commits change.",
    change: (gained, lost, from, p) =>
      `Of the commits only one of the two solved, ${gained || "none"} went to this setting and ${lost || "none"} to ${from}. A paired test gives p ${
        p < 0.001 ? "below 0.001" : `= ${p.toFixed(2)}`
      }, so ${p < 0.05 ? "a split like this would be rare if the two did equally well" : "chance alone could produce a split like this"}.`,
    solved: "Solved",
    missed: "Not solved",
    changed: "Changed with this setting",
    order: "Squares run by repository and then by commit date, so no outcome decides where one sits.",
    keys: "Commits, one square each. Use the arrow keys to step through them.",
    hint: "Point at or tap a square to see its commit and how each setting did on it.",
    open: "Open the commit",
    commit: "Commit",
    advice: "If you run a local agent, check its context window before you buy more memory.",
  },
  log: {
    title: "Every result is in the log, and so is every correction.",
    lead: "Each entry is dated and links to the attempts it came from. A correction also says what was wrong and what changed.",
    kinds: {
      running: "Running",
      finding: "Finding",
      correction: "Correction",
      void: "Voided",
      method: "Method",
      null: "Null result",
      start: "Start",
    },
    month: (y, m) => `${MONTHS[m - 1]} ${y}`,
    sources: "Sources",
    ledger: "The findings ledger in the repository has every experiment since April, including the ones this log leaves out.",
    ledgerLink: "Read the findings ledger",
    feed: "Mahoraga's log",
    follow: "Follow new entries in a feed reader",
  },
  engine: {
    title: "One pipeline, from a repository's history to a verdict.",
    lead: "Every stage can be swapped, and the log and a verdict on your own repository come out of the same code.",
    stages: [
      { name: "Mine", body: "Keep commits whose tests fail before them and pass after." },
      { name: "Check", body: "Confirm the bench works and estimate the nights it needs." },
      { name: "Run", body: "A local agent attempts every task overnight, in a fresh copy." },
      { name: "Grade", body: "The tests go back in, the full suite runs, and the ledger records it." },
      { name: "Decide", body: "The measured rate becomes stay, split, or switch for how you pay." },
    ],
    pipeline: "Source, runner, grader, ledger, verdict",
    keeps: [
      { title: "It runs itself overnight.", body: "Each job pins its model's digest, waits for its window and a quiet model server, and says why it waits." },
      { title: "It mines new commits.", body: "A followed repository is mined again every window, so each new model meets commits made after its release." },
      { title: "It checks its own numbers.", body: "CI recomputes every published number from the committed attempts, so the log can't drift from its data." },
    ],
    replayTitle: "Evidence arrives one task at a time.",
    replayLead: "The first probe, replayed task by task, shows the 95% interval settling only as tasks arrive.",
  },
  replay: {
    chartLabel: (n) => `${n} of Mahoraga's own commits from the first probe, graded one at a time`,
    axisTasks: "tasks graded",
    breakeven: (usd) => `Break-even at ${usd} a task`,
    resolved: (k, n) => `${k} of ${n} resolved`,
    interval: "95% interval",
    replay: "Replay",
  },
  verdict: {
    title: "A verdict that shows its working.",
    lead: "Drag the price of a cloud task, and the call changes only when the evidence clears the break-even line.",
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
    note: (date, tasks, perMonth) =>
      `Computed by the engine on the ${tasks} tasks behind the ${date} entry, for an API user with ${perMonth} cloud tasks a month.`,
    triage: "Minutes to check a failed attempt",
    minutes: (m) => `${m} min`,
    costs: (power, time) => `One local attempt burns about ${power} of electricity, and checking a failed one costs ${time} of your time.`,
  },
  headless: {
    title: "Built to be scripted.",
    lead: "The CLI, the HTTP API and the MCP server all reach the same engine, so a scripted verdict matches the one in the log.",
    mcpNote: "Add it to Claude Code or any MCP client to hand the router tasks.",
    viewer: "The same server also hosts a small local viewer for runs and verdicts.",
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
  run: {
    title: "Run it on your own repository.",
    lead: "The engine runs on macOS and Linux with Python 3.12+ and Ollama, and it was built on a 16 GB Mac.",
    steps: ["Install the engine from source", "Pull the model this log measures", "Queue tonight's run on your repository"],
    morning: "The morning file gives the rate, its interval and the verdict, after asking once how you pay for cloud.",
    pending: (names) => `Installs through ${names} wait for the first release.`,
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
  notes: {
    standing: (a) =>
      `The three measured rows ran from ${a.dates} on a ${a.machine}. Each used ${a.agent}, blind, with one attempt per task. Rates are task-weighted with a 95% Wilson interval over tasks (agentbench/stats.py), and CI recomputes them with orch bench verify from ${a.artifact}.`,
    verdict:
      "The break-even line and the verdicts come from audit/verdict.py. Their assumptions are listed in the verdict section, and their prices are the dated observations in audit/pricing.json.",
    ref: (n) => `Note ${n}`,
    source: ["Numbers from", " at commit ", "."],
  },
  footer: {
    made: "An open-source research project by Kaito Soeno.",
    license: "MIT License",
    page: "Mahoraga",
    project: "Project",
    person: "Kaito Soeno",
    report: "October report",
    portfolio: "Portfolio",
    contact: "Contact",
    privacy: "Privacy",
    contributing: "Contributing",
    security: "Security",
    dataflow: "Data flow",
    results: "Results",
    language: "日本語",
  },
  menu: {
    menu: "Menu",
    // kaitosoeno.com's own menu, in its order (personal-website content/copy.ts NAV).
    items: [
      { href: "/", label: "Contact" },
      { href: "/portfolio", label: "Portfolio" },
    ],
  },
};

const jaWord: Record<CurvePoint["call"], string> = { stay: "継続", split: "分担", switch: "移行" };

const ja: Dict = {
  meta: {
    research: {
      title: "Mahoraga：ローカルのコーディング AI を、実際のコードで測る",
      description:
        "Mahoraga は、ローカルのコーディングエージェントを実際のコミットで測るオープンなエンジンです。結果はすべて、根拠のデータと一緒に記録しています。",
    },
    engine: {
      title: "Mahoraga：エンジンが判定を出すまで",
      description:
        "Mahoraga は、リポジトリ自身のコミットでローカルのエージェントを採点し、解決できた割合を「継続」「分担」「移行」の判定に変えます。",
    },
    install: {
      title: "Mahoraga：インストールして、自分のリポジトリで動かす",
      description:
        "Mahoraga をソースからインストールし、自分のリポジトリで一晩の計測を予約します。macOS か Linux で、Python 3.12 以降と Ollama を使います。",
    },
  },
  nav: { research: "研究", engine: "エンジン", install: "インストール", github: "GitHub", label: "Mahoraga", updated: (d) => `${d} 更新` },
  hero: {
    eyebrow: "ローカルの現在地（2026 年 10 月）",
    statement: "多くの人にとって、答えは今もクラウドに払い続けることです。",
    sub: "Mahoraga は実際のコードでローカルのコーディング AI を測り、このページはその記録です。各試行はそのリポジトリ自身のテストで採点し、ここにある数字はすべてコミット済みのデータから計算し直しています。",
    cta: "記録を読む",
    more: "エンジンを見る",
  },
  standing: {
    title: (n) => `解決した実際のコミット（${n} 件中）`,
    caption: (n) =>
      `測定済みの各行は、more-itertools、click、attrs の同じ ${n} 件のコミットで、ひとつの設定を試した結果です。これらは Mac Studio で走らせたもので、qwen3.5 にはノートと同じモデルファイルを使いました。ノート自身の計測は最後の行です。`,
    count: (k, n) => `${n} 件中 ${k} 件`,
    breakeven: (rate, price) => `クラウドが 1 タスク ${price} なら、${rate} を超えると元が取れる`,
    row: (model, rate, k, n, lo, hi) => `${model}：解決率 ${rate}（${n} 件中 ${k} 件）、95% 区間 ${lo}〜${hi}`,
    running: "実行中",
    runningNote: "16 GB のノートで、10 月 31 日まで",
  },
  specimen: {
    title: "各タスクは、修正を抜き取った実際のコミットです。",
    lead: "エージェントは修正前のコードから始め、その試行を成功と数えるかどうかは、コミット自身のテストが決めます。",
    fix: (files) => `ベンチが ${files} から抜き取った修正`,
    agent: "その代わりにエージェントが書いたもの",
    resolved: "解決",
    unresolved: "未解決",
    tests: (pass, total, broke, kept) =>
      `${pass === total ? (total === 1 ? "コミットのテスト 1 件が通り" : `コミットのテスト ${total} 件がすべて通り`) : `コミットのテスト ${total} 件のうち ${pass} 件が通り`}、${
        broke === 0 ? `もともと通っていた ${kept} 件のテストも一つも壊れていません。` : `もともと通っていた ${kept} 件のうち ${broke} 件が壊れました。`
      }`,
    minutes: (m) => `試行時間 ${m} 分`,
    tokens: (sent, recv, calls) => `モデル呼び出し ${calls} 回、読んだトークン約 ${sent}、書いたトークン約 ${recv}`,
    machine: (machine, date) => `${machine}、${date}の夜`,
    picked: "ノート本体での 131k の計測（まだ進行中）から、結果ではなく順番で選んだ最初のタスクです。1 回の試行からは、解決率については何もわかりません。",
  },
  settings: {
    title: "効いたのは、設定ひとつでした。",
    lead: ({ k32, k131, k35, n }) =>
      `コンテキストウィンドウを 131k に広げると、ノートのモデルが解いたコミットは ${n} 件中 ${k32} 件から ${k131} 件に増えました。24 GB 以上が必要な 35B のモデルは ${k35} 件で、${
        k35 > k131 ? `差は ${k35 - k131} 件だけです` : k35 === k131 ? "同じ件数です" : `${k131 - k35} 件少ない結果です`
      }。`,
    label: (n, repos) => `${repos.join("、")} のコミット ${n} 件（1 マスが 1 コミット）`,
    choose: "設定",
    options: {
      "32k": { label: "qwen3.5・32k", note: "ベンチの既定のウィンドウです。長いファイルは収まらず、Ollama がエラーなしでプロンプトを切り詰めます。" },
      "131k": { label: "qwen3.5・131k", note: "同じモデルファイルに、読むファイルが収まる大きさのウィンドウを与えたものです。" },
      "35b": { label: "qwen3.6 35B・131k", note: "24 GB 以上が必要な 35B のモデルです。" },
    },
    count: (n) => `件を解決（${n} 件中）`,
    baseline: "より大きなウィンドウやモデルを選ぶと、どのコミットが変わるかがわかります。",
    change: (gained, lost, from, p) =>
      `どちらか一方だけが解いたコミットのうち、${gained} 件はこの設定、${lost} 件は ${from} が解きました。${
        p < 0.05
          ? `対応のある検定で p は ${p < 0.001 ? "0.001 未満" : p.toFixed(2)} です。2 つの実力が同じなら、こうした分かれ方はまれです。`
          : `対応のある検定で p = ${p.toFixed(2)} です。こうした分かれ方は、偶然だけでも生じます。`
      }`,
    solved: "解決",
    missed: "未解決",
    changed: "この設定で変わったコミット",
    order: "マスはリポジトリごとに、コミット日の古い順に並べています。結果で並びを決めてはいません。",
    keys: "1 マスが 1 コミットです。矢印キーで順に見られます。",
    hint: "マスにカーソルを合わせるかタップすると、そのコミットと各設定での結果が見られます。",
    open: "コミットを開く",
    commit: "コミット",
    advice: "ローカルのエージェントを使うなら、メモリを買い足す前に、コンテキストウィンドウを確かめてください。",
  },
  log: {
    title: "結果はすべて記録に残し、訂正もすべて残しています。",
    lead: "各エントリには日付と、もとになった試行へのリンクがあります。訂正には、何が間違っていて何を変えたかも書いています。",
    kinds: {
      running: "実行中",
      finding: "結果",
      correction: "訂正",
      void: "無効",
      method: "方法",
      null: "効果なし",
      start: "開始",
    },
    month: (y, m) => `${y} 年 ${m} 月`,
    sources: "出典",
    ledger: "リポジトリの所見台帳には、この記録で省いたものも含めて、4 月以降のすべての実験が残っています。",
    ledgerLink: "所見台帳を読む",
    feed: "Mahoraga の記録",
    follow: "新しい記録をフィードで受け取る（英語）",
  },
  engine: {
    title: "リポジトリの履歴から判定まで、ひとつのパイプラインで。",
    lead: "どの段階も差し替えられ、この記録とあなたのリポジトリの判定は同じコードから出てきます。",
    stages: [
      { name: "抽出", body: "テストがコミットの前では失敗し、後では通るものだけを残します。" },
      { name: "点検", body: "ベンチが動くかを確かめ、必要な晩数を見積もります。" },
      { name: "実行", body: "ローカルのエージェントが、夜のあいだに新しいコピーで全タスクに挑みます。" },
      { name: "採点", body: "テストを戻してフルスイートを走らせ、結果を台帳に記録します。" },
      { name: "判定", body: "測った解決率を、支払い方に合わせて「継続」「分担」「移行」に変えます。" },
    ],
    pipeline: "ソース、ランナー、採点、台帳、判定",
    keeps: [
      {
        title: "夜のあいだに自動で動きます。",
        body: "各ジョブはモデルのダイジェストを固定し、決められた時間帯と空いたモデルサーバーを待ち、待っている理由を表示します。",
      },
      {
        title: "新しいコミットを取り込みます。",
        body: "追跡中のリポジトリは時間帯ごとに抽出し直すので、新しいモデルは公開後のコミットで試されます。",
      },
      {
        title: "自分の数字を確かめます。",
        body: "CI が公開済みの数字をすべてコミット済みの試行から計算し直すので、記録がデータから離れることはありません。",
      },
    ],
    replayTitle: "根拠は、1 タスクずつ積み上がります。",
    replayLead: "最初のプローブを 1 タスクずつ再生すると、95% 区間はタスクが増えるにつれて落ち着いていくことがわかります。",
  },
  replay: {
    chartLabel: (n) => `最初のプローブで使った Mahoraga 自身のコミット ${n} 件を、1 件ずつ採点`,
    axisTasks: "採点したタスク",
    breakeven: (usd) => `1 タスク ${usd} のときの損益分岐点`,
    resolved: (k, n) => `${n} 件中 ${k} 件を解決`,
    interval: "95% 区間",
    replay: "もう一度",
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
    // The engine writes its reasons in English (audit/verdict.py), so the
    // Japanese sentence is built from the same call, the same numbers and the
    // same branches: call and confidence first, then the sign of the saving.
    // A loss is written as a positive cost, never as a negative saving, and
    // the bar is the smallest saving that calls for a split, the larger of $5
    // and 5% of the monthly cloud bill, carried on each curve point.
    reason: (p) => {
      const [lo, mid, hi] = [p.monthly_usd.lo, p.monthly_usd.point, p.monthly_usd.hi];
      const at = `1 タスク $${p.cloud_usd_per_task.toFixed(2)}`;
      const bar = p.bar > 5 ? `クラウドの請求額の 5%（月 ${usdJa(p.bar)}）` : "月 $5";
      if (p.call === "split" && p.confident)
        return `区間の悲観的な端でも、${at} なら、待てる仕事をローカルに回すと月 ${usdJa(lo)} 以上の節約になります。`;
      if (p.call === "split" && lo < 0)
        return `${at} なら、ローカルでおそらく月 ${usdJa(mid)} ほど節約できます。ただし区間の悲観的な端では月 ${usdJa(-lo)} の損になるので、頼る前にもう少し測りましょう。`;
      if (p.call === "split")
        return `${at} なら、ローカルでおそらく月 ${usdJa(mid)} ほど節約できます。ただし区間の悲観的な端では月 ${usdJa(lo)} にとどまり、${bar}に届かないので、頼る前にもう少し測りましょう。`;
      if (p.call === "stay" && p.confident && hi < 0)
        return `お金を無駄にしているわけではありません。${at} では、区間の楽観的な端でも、ローカルのほうがクラウドより月 ${usdJa(-hi)} 高くつきます。`;
      if (p.call === "stay" && p.confident)
        return `お金を無駄にしているわけではありません。${at} では、区間の楽観的な端でもローカルの節約は月 ${usdJa(hi)} にとどまり、分担を勧める最小の節約額である${bar}に届きません。`;
      if (p.call === "stay" && mid < 0)
        return `${at} では、測定した解決率だと、ローカルのほうがクラウドより月 ${usdJa(-mid)} 高くつきます。区間の楽観的な端では、月 ${usdJa(hi)} の節約に届きます。`;
      if (p.call === "stay")
        return `${at} では、測定した解決率でローカルは月 ${usdJa(mid)} ほど節約できますが、分担を勧める最小の節約額である${bar}に届きません。区間の楽観的な端では、月 ${usdJa(hi)} の節約に届きます。`;
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
    note: (date, tasks, perMonth) =>
      `${date} のエントリの ${tasks} タスクを根拠に、月 ${perMonth} 件のクラウドタスクを API で使う場合をエンジンが計算しました。`,
    triage: "失敗した試行の確認にかかる時間",
    minutes: (m) => `${m} 分`,
    costs: (power, time) => `ローカルの試行 1 回の電気代は約 ${power} で、失敗した試行を確認する時間は、時給に換算すると ${time} です。`,
  },
  headless: {
    title: "スクリプトから使うための設計。",
    lead: "CLI、HTTP API、MCP サーバーはどれも同じエンジンにつながるので、スクリプトで出した判定はこの記録と一致します。",
    mcpNote: "Claude Code などの MCP クライアントに追加すれば、ルーターにタスクを渡せます。",
    viewer: "同じサーバーには、実行記録と判定を見るための小さなローカルビューアーもあります。",
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
  run: {
    title: "あなたのリポジトリで動かす。",
    lead: "Python 3.12 以降と Ollama があれば macOS と Linux で動き、16 GB の Mac で開発しています。",
    steps: ["エンジンをソースからインストールする", "この記録で測っているモデルを取得する", "今夜の実行をキューに入れる"],
    morning: "翌朝のファイルには、解決率とその区間、判定が入ります。クラウドの支払い方は、最初に一度だけ尋ねます。",
    pending: (names) => `${names} でのインストールは、最初のリリースで公開します。`,
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
  notes: {
    standing: (a) =>
      `測定済みの 3 行は、${a.dates} に ${a.machine} で走らせたもので、どれも ${a.agent} をブラインドで使い、各タスク 1 試行でした。解決率はタスク単位で重み付けし、95% Wilson 区間はタスク数で数えています（agentbench/stats.py）。CI で orch bench verify が ${a.artifact} から計算し直しています。`,
    verdict: "損益分岐点の線と判定は audit/verdict.py によるもので、前提は判定の節に載せた値、価格は audit/pricing.json の日付つきの観測値です。",
    ref: (n) => `注 ${n}`,
    source: ["数値の出典", "（コミット ", "）。"],
  },
  footer: {
    made: "Kaito Soeno によるオープンソースの研究プロジェクト。",
    license: "MIT ライセンス",
    page: "Mahoraga",
    project: "プロジェクト",
    person: "Kaito Soeno",
    report: "10 月のレポート",
    portfolio: "作品",
    contact: "お問い合わせ",
    privacy: "プライバシー",
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
    ],
  },
};

function usdJa(x: number): string {
  const abs = Math.round(Math.abs(x)).toLocaleString("en-US");
  return x < 0 ? `−$${abs}` : `$${abs}`;
}

/** "a, b and c", without the serial comma, as the page's English writes lists. */
function andList(xs: string[]): string {
  return xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

export const copy: Record<Locale, Dict> = { en, ja };
