// Every user-facing string on the landing page, in both languages, behind one
// interface: a missing Japanese line is a type error, not English on /ja.
// Measured numbers are never typed here. Each sentence that carries one takes
// it as an argument from the engine's export (lib/data.ts). The log's entries
// live in content/log.ts under the same rule.
import type { CurvePoint } from "@/lib/data";
import type { Kind } from "./log";

export type Locale = "en" | "ja";

export interface Dict {
  meta: Record<"research" | "engine" | "install" | "privacy", { title: string; description: string }>;
  nav: { research: string; engine: string; install: string; github: string; label: string; updated: (date: string) => string };
  hero: { questionLabel: string; question: string; eyebrow: string; statement: string; sub: string; cta: string; more: string };
  // The map (ADR 2026-11-08): one row per kind of work, local and cloud on the
  // same tasks. Its numbers arrive as arguments from the claims, as everywhere.
  map: {
    title: string;
    lead: string;
    head: { work: string; local: string; cloud: string; caught: string };
    work47: (n: number) => string;
    workAfter: (n: number) => string;
    work92: (n: number) => string;
    laptop: (k: number, n: number, rate: string, lo: string, hi: string) => string;
    studio35: (k: number, n: number, rate: string, lo: string, hi: string) => string;
    count: (k: number, n: number) => string;
    opus: (k: number, n: number, usd: string) => string;
    notRun: string;
    no: string;
    needsSpent: (rate: string) => string;
    needsAssumed: (rate: string, price: string) => string;
    runningTitle: string;
    running: string[];
    rule: string;
    source: string;
    sourceLink: string;
  };
  standing: {
    title: string;
    // n: the commits the settings rows share; own: the laptop run's share of
    // them; versus: what the Studio resolved on that same share.
    caption: (n: number, own: number, versus: number) => string;
    count: (k: number, n: number) => string;
    breakeven: (rate: string, price: string) => string;
    row: (model: string, rate: string, k: number, n: number, lo: string, hi: string) => string;
    running: string;
    runningNote: (since: string) => string;
  };
  specimen: {
    title: string;
    lead: string;
    fix: (files: string) => string;
    agent: string;
    noEdits: string;
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
    lead: (a: { k32: number; k131: number; k35: number; n: number; bar: string; price: string }) => string;
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
  byHand: { title: string; lead: string };
  privacy: { title: string; items: { title: string; body: string }[]; link: string; policy: string };
  run: {
    title: string;
    lead: string;
    steps: [string, string, string];
    morning: string;
    pending: (names: string) => string;
    later: (names: string, n: number) => string;
    copy: string;
    copied: string;
  };
  support: { title: string; lead: string; contribute: string; issues: string; sponsor: string };
  outcomes: Record<string, string>;
  footnotesTitle: string;
  notes: {
    standing: (a: {
      dates: string; machine: string; agent: string; artifact: string;
      own: { dates: string; machine: string; limit: string; artifact: string };
    }) => string;
    verdict: string;
    ref: (n: number) => string;
    source: [string, string, string];
  };
  footer: {
    made: string;
    license: string;
    affiliation: string;
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
    app: string;
    language: string;
  };
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const en: Dict = {
  meta: {
    research: {
      title: "Mahoraga: where local AI has caught up with the cloud",
      description:
        "Mahoraga is a long-term study that measures local and cloud AI on the same real commits, grades them with each repository's own tests, and dates every result.",
    },
    engine: {
      title: "Mahoraga: how each row is measured",
      description:
        "Each row is real commits graded by the repository's own tests, with local and cloud on the same tasks. The same pipeline gives a verdict on your own repository.",
    },
    install: {
      title: "Mahoraga: replicate it on your own repository",
      description:
        "Install Mahoraga from source and queue a night's run on your own repository, on macOS or Linux with Python 3.12+ and Ollama.",
    },
    privacy: {
      title: "Mahoraga: privacy policy",
      description: "What Mahoraga sends off your computer, where it goes, and how to stop it.",
    },
  },
  nav: { research: "Research", engine: "Method", install: "Replicate", github: "GitHub", label: "Mahoraga", updated: (d) => `Updated ${d}` },
  hero: {
    questionLabel: "The question",
    question: "Where has local AI caught up with the cloud?",
    eyebrow: "Where local stands, November 2026",
    statement: "Local hasn't caught up on any coding work measured so far.",
    sub: "Mahoraga is a long-term study of local against cloud AI on the same real commits. Each attempt is graded by the repository's own tests, and every number here is recomputed from committed data.",
    cta: "Read the log",
    more: "See the method",
  },
  map: {
    title: "One row for each kind of work, with both sides on the same tasks.",
    lead: "Local has caught up on a row when even the low end of its 95% interval clears the break-even line, and no row has crossed yet.",
    head: { work: "Work", local: "Local, best measured", cloud: "Cloud, same tasks", caught: "Caught up?" },
    work47: (n) => `Real commits from attrs and more-itertools (${n} tasks)`,
    workAfter: (n) => `The ${n} of those committed after June 2026, past both models' training data`,
    work92: (n) => `Real commits from attrs, more-itertools and click (${n} tasks)`,
    laptop: (k, n, rate, lo, hi) => `aider and qwen3.5 (9.7B) on a 16 GB MacBook: ${k} of ${n}, ${rate} (95% interval ${lo} to ${hi})`,
    studio35: (k, n, rate, lo, hi) => `aider and qwen3.6 (35B) on a 64 GB Mac Studio: ${k} of ${n}, ${rate} (95% interval ${lo} to ${hi})`,
    count: (k, n) => `${k} of ${n}`,
    opus: (k, n, usd) => `Claude Code and Opus 5.5: ${k} of ${n}, at ${usd} a task`,
    notRun: "Not run yet",
    no: "No",
    needsSpent: (rate) => `No. Local needs ${rate} at what the cloud spent.`,
    needsAssumed: (rate, price) => `No. Local needs ${rate} at an assumed ${price} a cloud task.`,
    runningTitle: "Being measured now",
    running: [
      "Claude Code driving qwen3.5 on the Mac Studio, so both sides use the same agent and whatever gap remains belongs to the model.",
      "Sonnet 5.5 and Haiku 5.5 on the same tasks as Opus 5.5, so each row can say which cloud tier local has caught up with.",
      "New commits mined every night, so each new model meets work made after its release.",
    ],
    rule: "Coding comes first because a commit grades itself with its tests. Another kind of work joins the map only when it has a check just as mechanical, and real tasks behind it.",
    source: "Every number in the table is recomputed in CI from the committed attempts.",
    sourceLink: "Read the report",
  },
  standing: {
    title: "Real commits resolved",
    caption: (n, own, versus) =>
      `The first three rows are one setting each on the same ${n} commits from more-itertools, click and attrs, run on a Mac Studio with the laptop's exact model file for qwen3.5. The last row is the laptop's own run on the ${own} of those commits from more-itertools and attrs, and on the same ${own} the Studio resolved ${versus}.`,
    count: (k, n) => `${k} of ${n}`,
    breakeven: (rate, price) => `Local pays above ${rate} at the measured ${price} a cloud task`,
    row: (model, rate, k, n, lo, hi) => `${model}: ${rate} resolved, ${k} of ${n} tasks, 95% interval ${lo} to ${hi}`,
    running: "Running",
    runningNote: (since) => `On the 16 GB laptop, since ${since}`,
  },
  specimen: {
    title: "Each task is a real commit with the fix taken out.",
    lead: "The agent starts from the code as it stood before the fix, and the commit's own tests decide whether its attempt counts.",
    fix: (files) => `The fix the bench took out of ${files}`,
    agent: "What the agent wrote in its place",
    noEdits: "The agent changed nothing.",
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
      "This is the first task of the laptop's own run at 131k. It was picked by its position and not by its outcome, and one attempt says nothing about the rate.",
  },
  settings: {
    title: "Most of the gap was the context window.",
    lead: ({ k32, k131, k35, n, bar, price }) =>
      `On a 64 GB Mac Studio, a 131k window took the laptop's 9.7B model from ${k32} to ${k131} of ${n} commits, near a 35B model's ${k35}. All three fall well short of the ${bar} that local needs to pay for itself at the measured ${price} a cloud task.`,
    label: (n, repos) => `${n} commits from ${andList(repos)}, one square each`,
    choose: "Setting",
    options: {
      "32k": { label: "qwen3.5 at 32k", note: "This is the bench's default window, and when a long file overflows it, Ollama cuts the prompt to fit without returning an error." },
      "131k": { label: "qwen3.5 at 131k", note: "This is the same model file, given a window big enough for the files it reads." },
      "35b": { label: "qwen3.6 35B at 131k", note: "This 35B model needs 24 GB of memory or more, so a 16 GB laptop can't run it." },
    },
    count: (n) => `of ${n} commits solved`,
    baseline: "Choose a larger window or a larger model to see which commits change.",
    change: (gained, lost, from, p) =>
      `Compared with ${from}, ${gained === 0 ? "no commits" : gained === 1 ? "1 commit" : `${gained} commits`} went from unsolved to solved and ${lost || "none"} went the other way. A paired test gives p ${
        p < 0.001 ? "below 0.001" : `= ${p.toFixed(2)}`
      }, so ${p < 0.05 ? "a split this uneven would be rare if the two settings did equally well" : "chance alone could produce a split like this"}.`,
    solved: "Solved",
    missed: "Not solved",
    changed: "Changed with this setting",
    order: "Squares run by repository and then by commit date, so no outcome decides where one sits.",
    keys: "Commits, one square each. Use the arrow keys to step through them.",
    hint: "Point at or tap a square to see its commit and how each setting did on it.",
    open: "Open the commit",
    commit: "Commit",
    advice:
      "If you run a local agent, check its context window before you buy more memory. On a 16 GB laptop the larger window is much slower, but the laptop's own run solved about as many of the same commits as the Studio.",
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
    title: "One pipeline, from a repository's history to a row of the map.",
    lead: "The map's rows and a verdict on your own repository come out of the same code, and every stage can be swapped.",
    stages: [
      { name: "Mine", body: "Keep commits whose tests fail before them and pass after, then grade the real fix like an attempt." },
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
  byHand: {
    title: "Each step has its own command.",
    lead: "The tonight command does the whole setup at once. Each stage also runs on its own when you want to drive it by hand.",
  },
  privacy: {
    title: "Your code stays on your machine.",
    items: [
      { title: "Read-only", body: "The audit only reads your repo, and every attempt works on a copy." },
      {
        title: "Sandboxed",
        body: "Local agents can write only to their copy and can't reach past this machine, under sandbox-exec on macOS and bubblewrap on Linux.",
      },
      {
        title: "The cloud only when asked",
        body: "Local agents send nothing, and the cloud arm runs only when you pass --allow-cloud. The router escalates to Claude only when a local answer fails to run or the local judge rejects it, at most 25 times a day.",
      },
    ],
    link: "Read DATAFLOW.md",
    policy: "Read the privacy policy",
  },
  run: {
    title: "Run it on your own repository.",
    lead: "The engine runs on macOS and Linux with Python 3.12+ and Ollama, and it was built on a 16 GB Mac.",
    steps: ["Install the engine from source", "Pull the model this log measures", "Queue tonight's run on your repository"],
    morning: "The morning file gives the rate, its interval and the verdict, after asking once how you pay for cloud.",
    pending: (names) => `Installs through ${names} wait for the first release.`,
    later: (names, n) => `${names} ${n === 1 ? "comes" : "come"} later.`,
    copy: "Copy",
    copied: "Copied",
  },
  support: {
    title: "Free and open source.",
    lead: "Mahoraga is MIT-licensed. Run it on your repo, report what breaks, and send fixes.",
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
      `The first three rows ran from ${a.dates} on a ${a.machine}, and the laptop's row ran from ${a.own.dates} on a ${a.own.machine} with ${a.own.limit} an attempt. Each used ${a.agent}, blind, with one attempt per task. Rates are task-weighted with a 95% Wilson interval over tasks (agentbench/stats.py), and CI recomputes them with orch bench verify from ${a.artifact} and ${a.own.artifact}.`,
    verdict:
      "The break-even line and the verdicts come from audit/verdict.py. The line is priced at what Claude Code on Claude Opus 5.5 spent, at API list prices, on the tasks the laptop solved (report §4). The verdicts' assumptions are listed in the verdict section, and the plan prices are the dated observations in audit/pricing.json.",
    ref: (n) => `Note ${n}`,
    source: ["Numbers from", " at commit ", "."],
  },
  footer: {
    made: "An open-source research project by Kaito Soeno.",
    license: "MIT License",
    affiliation: "Mahoraga is not affiliated with Anthropic, OpenAI, Alibaba, IBM or Ollama. Product names belong to their owners.",
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
    app: "App and router",
    language: "日本語",
  },
};

const jaWord: Record<CurvePoint["call"], string> = { stay: "継続", split: "分担", switch: "移行" };

const ja: Dict = {
  meta: {
    research: {
      title: "Mahoraga：ローカルの AI はどこでクラウドに追いついたか",
      description:
        "Mahoraga は、同じ実際のコミットでローカルとクラウドの AI を測り、各リポジトリ自身のテストで採点して、結果に日付をつけて記録する長期の研究です。",
    },
    engine: {
      title: "Mahoraga：各行の測り方",
      description:
        "各行は、リポジトリ自身のテストで採点した実際のコミットで、ローカルとクラウドを同じタスクで測ります。同じパイプラインで、あなたのリポジトリの判定も出せます。",
    },
    install: {
      title: "Mahoraga：自分のリポジトリで再現する",
      description:
        "Mahoraga をソースからインストールし、自分のリポジトリで一晩の計測を予約します。macOS か Linux で、Python 3.12 以降と Ollama を使います。",
    },
    privacy: {
      title: "Mahoraga：プライバシーポリシー",
      description: "Mahoraga がコンピューターの外へ何を送り、どこへ届くのか、そしてそれを止める方法。",
    },
  },
  nav: { research: "研究", engine: "方法", install: "再現", github: "GitHub", label: "Mahoraga", updated: (d) => `${d} 更新` },
  hero: {
    questionLabel: "問い",
    question: "ローカルの AI は、どこでクラウドに追いついたのか。",
    eyebrow: "ローカルの現在地（2026 年 11 月）",
    statement: "これまで測ったコーディングの仕事では、まだどこでも追いついていません。",
    sub: "Mahoraga は、同じ実際のコミットでローカルとクラウドの AI を比べ続ける長期の研究です。各試行はそのリポジトリ自身のテストで採点し、ここにある数字はすべてコミット済みのデータから計算し直しています。",
    cta: "記録を読む",
    more: "方法を見る",
  },
  map: {
    title: "仕事の種類ごとに 1 行、両者を同じタスクで測っています。",
    lead: "ローカルが追いついたと言えるのは、95% 区間の下端でさえ損益分岐線を越えたときで、まだ越えた行はありません。",
    head: { work: "仕事", local: "ローカル（最良の計測）", cloud: "クラウド（同じタスク）", caught: "追いついたか" },
    work47: (n) => `attrs と more-itertools の実際のコミット（${n} タスク）`,
    workAfter: (n) => `そのうち 2026 年 6 月より後にコミットされた ${n} タスク（両モデルの学習データより後）`,
    work92: (n) => `attrs、more-itertools、click の実際のコミット（${n} タスク）`,
    laptop: (k, n, rate, lo, hi) => `16 GB の MacBook で aider と qwen3.5（9.7B）：${n} 件中 ${k} 件、${rate}（95% 区間 ${lo}〜${hi}）`,
    studio35: (k, n, rate, lo, hi) => `64 GB の Mac Studio で aider と qwen3.6（35B）：${n} 件中 ${k} 件、${rate}（95% 区間 ${lo}〜${hi}）`,
    count: (k, n) => `${n} 件中 ${k} 件`,
    opus: (k, n, usd) => `Claude Code と Opus 5.5：${n} 件中 ${k} 件、1 タスク ${usd}`,
    notRun: "まだ測っていません",
    no: "いいえ",
    needsSpent: (rate) => `いいえ。クラウドが実際に使った額では、ローカルに ${rate} が必要です。`,
    needsAssumed: (rate, price) => `いいえ。クラウドのタスクを 1 件 ${price} と仮定すると、ローカルに ${rate} が必要です。`,
    runningTitle: "いま測っているもの",
    running: [
      "Mac Studio で Claude Code に qwen3.5 を動かさせ、両側のエージェントをそろえます。それでも残る差は、モデルの差です。",
      "Opus 5.5 と同じタスクで Sonnet 5.5 と Haiku 5.5 も測り、ローカルがどのクラウドの層に追いついたかを各行で言えるようにします。",
      "新しいコミットを毎晩掘り出し、新しいモデルがリリース後の仕事にも出会うようにします。",
    ],
    rule: "コーディングから始めるのは、コミットが自分のテストで採点できるからです。ほかの種類の仕事は、同じくらい機械的な確認方法と実際のタスクがそろったときにだけ、この表に加わります。",
    source: "表の数字はすべて、コミット済みの試行から CI で計算し直しています。",
    sourceLink: "レポートを読む",
  },
  standing: {
    title: "解決した実際のコミット",
    caption: (n, own, versus) =>
      `上の 3 行は、more-itertools、click、attrs の同じ ${n} 件のコミットで、ひとつずつ設定を試した結果です。これらは Mac Studio で走らせたもので、qwen3.5 にはノートと同じモデルファイルを使いました。最後の行はノート自身の計測で、そのうち more-itertools と attrs の ${own} 件が対象です。同じ ${own} 件で、Studio は ${versus} 件を解決しました。`,
    count: (k, n) => `${n} 件中 ${k} 件`,
    breakeven: (rate, price) => `クラウドの実測 1 タスク ${price} なら、${rate} を超えると元が取れる`,
    row: (model, rate, k, n, lo, hi) => `${model}：解決率 ${rate}（${n} 件中 ${k} 件）、95% 区間 ${lo}〜${hi}`,
    running: "実行中",
    runningNote: (since) => `16 GB のノートで、${since}から`,
  },
  specimen: {
    title: "各タスクは、修正を抜き取った実際のコミットです。",
    lead: "エージェントは修正前のコードから始め、その試行を成功と数えるかどうかは、コミット自身のテストが決めます。",
    fix: (files) => `ベンチが ${files} から抜き取った修正`,
    agent: "その代わりにエージェントが書いたもの",
    noEdits: "エージェントは何も変更しませんでした。",
    resolved: "解決",
    unresolved: "未解決",
    tests: (pass, total, broke, kept) =>
      `${pass === total ? (total === 1 ? "コミットのテスト 1 件が通り" : `コミットのテスト ${total} 件がすべて通り`) : `コミットのテスト ${total} 件のうち ${pass} 件が通り`}、${
        broke === 0 ? `もともと通っていた ${kept} 件のテストも一つも壊れていません。` : `もともと通っていた ${kept} 件のうち ${broke} 件が壊れました。`
      }`,
    minutes: (m) => `試行時間 ${m} 分`,
    tokens: (sent, recv, calls) => `モデル呼び出し ${calls} 回、読んだトークン約 ${sent}、書いたトークン約 ${recv}`,
    machine: (machine, date) => `${machine}、${date}の夜`,
    picked: "ノート本体での 131k の計測から、結果ではなく順番で選んだ最初のタスクです。1 回の試行からは、解決率については何もわかりません。",
  },
  settings: {
    title: "差の大半は、コンテキストウィンドウでした。",
    lead: ({ k32, k131, k35, n, bar, price }) =>
      `64 GB の Mac Studio で、ノートが使う 9.7B のモデルにウィンドウを 131k まで与えると、解いたコミットは ${n} 件中 ${k32} 件から ${k131} 件に増え、35B のモデルの ${k35} 件に迫りました。3 つとも、クラウドの実測 1 タスク ${price} のときにローカルの元が取れる ${bar} には遠く届きません。`,
    label: (n, repos) => `${repos.join("、")} のコミット ${n} 件（1 マスが 1 コミット）`,
    choose: "設定",
    options: {
      "32k": { label: "qwen3.5・32k", note: "ベンチの既定のウィンドウです。長いファイルがはみ出すと、Ollama はエラーを返さずにプロンプトを切り詰めます。" },
      "131k": { label: "qwen3.5・131k", note: "同じモデルファイルに、読むファイルが収まる大きさのウィンドウを与えたものです。" },
      "35b": { label: "qwen3.6 35B・131k", note: "24 GB 以上のメモリが必要な 35B のモデルで、16 GB のノートでは動きません。" },
    },
    count: (n) => `件を解決（${n} 件中）`,
    baseline: "より大きなウィンドウやモデルを選ぶと、どのコミットが変わるかがわかります。",
    change: (gained, lost, from, p) =>
      `${from} と比べると、${gained} 件が未解決から解決に変わり、${lost} 件が逆に変わりました。${
        p < 0.05
          ? `対応のある検定で p は ${p < 0.001 ? "0.001 未満" : p.toFixed(2)} です。2 つの実力が同じなら、ここまで偏った分かれ方はまれです。`
          : `対応のある検定で p = ${p.toFixed(2)} です。この程度の分かれ方は、偶然だけでも生じます。`
      }`,
    solved: "解決",
    missed: "未解決",
    changed: "この設定で変わったコミット",
    order: "マスはリポジトリごとに、コミット日の古い順に並べています。結果で並びを決めてはいません。",
    keys: "1 マスが 1 コミットです。矢印キーで順に見られます。",
    hint: "マスにカーソルを合わせるかタップすると、そのコミットと各設定での結果が見られます。",
    open: "コミットを開く",
    commit: "コミット",
    advice:
      "ローカルのエージェントを使うなら、メモリを買い足す前に、コンテキストウィンドウを確かめてください。16 GB のノートでは大きなウィンドウはずっと遅くなりますが、ノート本体での計測は、同じコミットを Studio とほぼ同じ数だけ解きました。",
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
    title: "リポジトリの履歴から表の 1 行まで、ひとつのパイプラインで。",
    lead: "表の各行とあなたのリポジトリの判定は同じコードから出てきて、どの段階も差し替えられます。",
    stages: [
      { name: "抽出", body: "テストがコミットの前では失敗し、後では通るものだけを残し、本物の修正も試行と同じように採点します。" },
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
  byHand: {
    title: "各段階に、それぞれのコマンドがあります。",
    lead: "tonight コマンドは準備を一度に済ませます。手で動かしたいときは、各段階を単独でも実行できます。",
  },
  privacy: {
    title: "コードは、手元から出ていきません。",
    items: [
      { title: "読み取りのみ", body: "監査はリポジトリを読むだけで、試行はすべてコピーの上で行います。" },
      {
        title: "サンドボックス",
        body: "ローカルのエージェントが書き込めるのは自分のコピーだけで、このマシンの外には通信できません。macOS では sandbox-exec、Linux では bubblewrap で閉じ込めます。",
      },
      {
        title: "クラウドは必要なときだけ",
        body: "ローカルのエージェントは何も送らず、クラウドのアームは --allow-cloud を付けたときだけ動きます。ルーターが Claude に回すのは、ローカルの回答が動かないか、ローカルのジャッジが却下したときだけで、1 日 25 回までです。",
      },
    ],
    link: "DATAFLOW.md を読む",
    policy: "プライバシーポリシーを読む",
  },
  run: {
    title: "あなたのリポジトリで動かす。",
    lead: "Python 3.12 以降と Ollama があれば macOS と Linux で動き、16 GB の Mac で開発しています。",
    steps: ["エンジンをソースからインストールする", "この記録で測っているモデルを取得する", "今夜の実行をキューに入れる"],
    morning: "翌朝のファイルには、解決率とその区間、判定が入ります。クラウドの支払い方は、最初に一度だけ尋ねます。",
    pending: (names) => `${names} でのインストールは、最初のリリースで公開します。`,
    later: (names) => `${names} は後日対応します。`,
    copy: "コピー",
    copied: "コピーしました",
  },
  support: {
    title: "無料で、オープンソース。",
    lead: "MIT ライセンスです。自分のリポジトリで動かし、壊れたところを報告し、修正を送ってください。",
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
      `上の 3 行は ${a.dates} に ${a.machine} で、ノートの行は ${a.own.dates} に ${a.own.machine} で、1 試行 ${a.own.limit} で走らせたものです。どれも ${a.agent} をブラインドで使い、各タスク 1 試行でした。解決率はタスク単位で重み付けし、95% Wilson 区間はタスク数で数えています（agentbench/stats.py）。CI で orch bench verify が ${a.artifact} と ${a.own.artifact} から計算し直しています。`,
    verdict: "損益分岐点の線と判定は audit/verdict.py によるものです。線の価格は、ノートが解いたタスクで Claude Opus 5.5 の Claude Code が API の定価で使った額です（レポート §4）。判定の前提は判定の節に載せた値、プランの価格は audit/pricing.json の日付つきの観測値です。",
    ref: (n) => `注 ${n}`,
    source: ["数値の出典", "（コミット ", "）。"],
  },
  footer: {
    made: "Kaito Soeno によるオープンソースの研究プロジェクト。",
    license: "MIT ライセンス",
    affiliation: "Mahoraga は Anthropic、OpenAI、Alibaba、IBM、Ollama のいずれとも提携していません。製品名は各所有者に帰属します。",
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
    app: "アプリとルーター",
    language: "English",
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
