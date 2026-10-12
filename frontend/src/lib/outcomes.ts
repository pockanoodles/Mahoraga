// What happened in an attempt, in one word. The labels and their order are the
// engine's (agentbench/report.py OUTCOMES); this adds what each one means, so
// the app can say it in a sentence wherever it shows the word.

export type Outcome =
  | "resolved"
  | "partial"
  | "wrong-fix"
  | "broke-other"
  | "partial+broke"
  | "wrong-file"
  | "no-edit";

export const OUTCOMES: { key: Outcome; label: string; means: string }[] = [
  { key: "resolved", label: "Resolved", means: "Every target test passes and nothing that passed before broke." },
  { key: "partial", label: "Partial", means: "Some of the target tests pass, but not all of them." },
  { key: "wrong-fix", label: "Wrong fix", means: "It edited the right file, and no target test passes." },
  {
    key: "broke-other",
    label: "Broke other code",
    means: "It fixed the target and broke code elsewhere, which only the full-suite run sees.",
  },
  { key: "partial+broke", label: "Partial, broke other", means: "Some target tests pass, and it broke code elsewhere." },
  { key: "wrong-file", label: "Wrong file", means: "It edited files, but none of the ones the real commit changed." },
  { key: "no-edit", label: "No edit", means: "The agent changed no source file." },
];

const BY_KEY = new Map(OUTCOMES.map((o) => [o.key, o]));

export function outcomeInfo(key: string) {
  return BY_KEY.get(key as Outcome) ?? { key: key as Outcome, label: key, means: "" };
}
