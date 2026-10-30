// Mahoraga's privacy policy, both languages. It sits beside copy.ts rather than
// inside it because it is a document, not interface strings, and it changes on
// its own schedule. It restates DATAFLOW.md (at the repository root) for
// visitors, so a change to what Mahoraga sends belongs in both places, and
// UPDATED moves with it. kaitosoeno.com/privacy covers the portfolio's contact
// form and links here.
//
// Japanese is authored, not translated line by line.
import type { Locale } from "./copy";
import { GIT } from "./channels";

export interface LedgerRow {
  where: string;
  what: string;
  why: string;
}

export interface PolicySection {
  id: string;
  heading: string;
  paras: string[];
  ledger?: LedgerRow[];
  after?: string[];
  links?: { label: string; href: string }[];
}

export interface PolicyDoc {
  title: string;
  lead: string;
  updated: string;
  heads: LedgerRow;
  sections: PolicySection[];
}

export const UPDATED = "2026-10-29";

const EMAIL = "soenokaito1@gmail.com";
const LINKS = {
  anthropic: "https://www.anthropic.com/legal/privacy",
  cloudflare: "https://www.cloudflare.com/privacypolicy/",
  dataflow: `${GIT}/blob/main/DATAFLOW.md`,
};

const en: PolicyDoc = {
  title: "Privacy",
  lead: "What Mahoraga sends off your computer, where it goes, and how to stop it.",
  updated: "Last updated 29 October 2026",
  heads: { where: "Where", what: "What", why: "Why" },
  sections: [
    {
      id: "short",
      heading: "The short version",
      paras: [
        "Mahoraga runs on your own computer, with no account, no telemetry and no server of mine behind it. The audit (mining your commits, running the local agent, grading with your tests and writing the verdict) sends nothing off your machine, because the model runs in Ollama on the same computer.",
        "Nothing you do in Mahoraga reaches me, so I hold nothing of yours to sell, share or use to train models.",
      ],
    },
    {
      id: "sends",
      heading: "What can leave your computer",
      paras: [
        "The router is the one part that can send a task to the cloud. When a local answer fails its check, the router can re-run that prompt on Anthropic, up to 25 times a day, and the prompt may contain your code. This escalation is on by default but needs the claude command or an Anthropic API key, and setting MAHORAGA_CASCADE=off keeps everything local.",
      ],
      ledger: [
        { where: "Anthropic, when the router escalates", what: "The prompt of a task the local answer failed, which may include code", why: "To get a working answer when the local model couldn't" },
        { where: "Anthropic, only if you turn on the claude backend", what: "The mission text and the task's output", why: "To plan and check work with a cloud model" },
        { where: "Telegram or Brave Search, only if you add their keys", what: "The messages or queries you route through them", why: "For the features that use them" },
        { where: "Ollama's registry, Hugging Face and GitHub", what: "A request for a model or a benchmark file, with no task data", why: "To download what you asked to install" },
      ],
      after: [
        "What Anthropic does with a prompt is set by your own agreement with Anthropic, so read their terms before you point Mahoraga at code you can't share.",
      ],
      links: [{ label: "Anthropic's privacy policy", href: LINKS.anthropic }],
    },
    {
      id: "page",
      heading: "This page",
      paras: [
        "This page is a static site that Cloudflare serves on kaitosoeno.com. It has no forms, cookies, analytics or third-party requests. Like any web host, Cloudflare sees the ordinary details of each request, such as your IP address and browser.",
      ],
      links: [{ label: "Cloudflare's privacy policy", href: LINKS.cloudflare }],
    },
    {
      id: "choices",
      heading: "Your choices",
      paras: [
        "Everything Mahoraga records (tasks, attempts, verdicts, and the router's memory and logs) stays in ~/.mahoraga-v2 on your computer until you delete it. DATAFLOW.md in the repository lists every connection in more detail.",
        "Mahoraga isn't directed at children under 13. If this policy changes, the date at the top changes with it.",
      ],
      links: [{ label: "DATAFLOW.md", href: LINKS.dataflow }],
    },
    {
      id: "contact",
      heading: "Contact",
      paras: [
        `Mahoraga is made by Kaito Soeno, an individual in Boston, Massachusetts. Questions about this policy go to ${EMAIL}.`,
      ],
    },
  ],
};

const ja: PolicyDoc = {
  title: "プライバシーポリシー",
  lead: "Mahoraga がコンピューターの外へ何を送り、どこへ届くのか、そしてそれを止める方法をまとめています。",
  updated: "最終更新 2026年10月29日",
  heads: { where: "送信先", what: "内容", why: "目的" },
  sections: [
    {
      id: "short",
      heading: "要点",
      paras: [
        "Mahoraga はご自身のコンピューターで動き、アカウント、テレメトリ、私が運営するサーバーはありません。監査（コミットからのタスク作成、ローカルエージェントの実行、テストによる採点、判定の作成）では、モデルが同じコンピューター上の Ollama で動くため、何もマシンの外に送りません。",
        "Mahoraga での利用内容が私に届くことはないため、販売や共有、モデルの学習に使えるものを私は何も持っていません。",
      ],
    },
    {
      id: "sends",
      heading: "コンピューターの外へ出るもの",
      paras: [
        "クラウドへタスクを送ることがあるのは、ルーターだけです。ローカルの回答がチェックを通らなかった場合、ルーターはそのプロンプトを1日25回まで Anthropic で再実行することがあり、プロンプトにはコードが含まれることがあります。この再実行は初期状態で有効ですが、claude コマンドか Anthropic の API キーが必要で、MAHORAGA_CASCADE=off にするとすべてローカルに留まります。",
      ],
      ledger: [
        { where: "Anthropic（ルーターが再実行したとき）", what: "ローカルの回答が失敗したタスクのプロンプト（コードを含むことがあります）", why: "ローカルモデルで解けなかったときに正しい回答を得るため" },
        { where: "Anthropic（claude バックエンドを有効にした場合のみ）", what: "ミッションの文面とタスクの出力", why: "クラウドのモデルで作業を計画し、確認するため" },
        { where: "Telegram または Brave Search（キーを設定した場合のみ）", what: "それらを経由させたメッセージや検索語", why: "それらを使う機能のため" },
        { where: "Ollama のレジストリ、Hugging Face、GitHub", what: "モデルやベンチマークファイルのダウンロード要求（タスクのデータは含みません）", why: "インストールを指示したものを取得するため" },
      ],
      after: [
        "Anthropic がプロンプトをどう扱うかはご自身と Anthropic との契約で決まるため、共有できないコードに使う前に同社の規約をご確認ください。",
      ],
      links: [{ label: "Anthropic のプライバシーポリシー", href: LINKS.anthropic }],
    },
    {
      id: "page",
      heading: "このページ",
      paras: [
        "このページは、Cloudflare が kaitosoeno.com で配信している静的なサイトです。フォーム、Cookie、アクセス解析、外部へのリクエストはありません。ほかのウェブサイトと同じく、Cloudflare は IP アドレスやブラウザーなど、リクエストに含まれる通常の情報を受け取ります。",
      ],
      links: [{ label: "Cloudflare のプライバシーポリシー", href: LINKS.cloudflare }],
    },
    {
      id: "choices",
      heading: "データの管理",
      paras: [
        "Mahoraga が記録するもの（タスク、試行、判定、ルーターの記憶とログ）は、削除するまでご自身のコンピューターの ~/.mahoraga-v2 に残ります。接続先の詳細は、リポジトリの DATAFLOW.md にすべて記載しています。",
        "Mahoraga は13歳未満の方を対象としていません。このポリシーを変更した場合は、冒頭の日付も更新します。",
      ],
      links: [{ label: "DATAFLOW.md", href: LINKS.dataflow }],
    },
    {
      id: "contact",
      heading: "お問い合わせ",
      paras: [
        `Mahoraga は、米国マサチューセッツ州ボストン在住の個人、添野開仁が制作しています。このポリシーに関するご質問は ${EMAIL} までお送りください。`,
      ],
    },
  ],
};

export const policy: Record<Locale, PolicyDoc> = { en, ja };
