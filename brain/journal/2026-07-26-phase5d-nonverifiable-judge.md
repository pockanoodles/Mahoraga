# 2026-07-26 — Phase 5d: the judge on non-verifiable tasks (no oracle)

## Why

5a–5c proved local-first routing on **code**, where a hidden-test oracle exists,
so the judge only had to approximate something we could also check. The open
question (findings Era 13–14 caveats): does a free local judge hold where there
is **no oracle** — explain, reason, summarize, factual, instruct — its real job?
You can't score that by running tests, so I built a bank whose ground truth is
established **by construction**.

## The bank (`experiments/prompts_nonverifiable.jsonl`, +`_refs`)

30 rows, 6 each across `explain / factual / reason / summarize / instruct`,
tier-skewed 5 easy / 10 medium / 15 hard. Each row ships a hand-authored correct
`reference` and a subtly-flawed `mutant` carrying exactly one labeled `defect`
(14 defect types). The mutant matches the reference in length, fluency, and
confidence — only substance differs — so a judge can't win on length (the bias
that sank every pre-2026 judge, Era 7). A CI guard enforces the structural
contract, including length parity (mutant within 0.5–2× the reference).

**Three lines of defense on label quality:** subagent drafting → my full
curation (recomputed every `reason` answer, checked every `summarize` mutant
against its embedded passage) → an independent adversarial **blind audit** (a
fresh agent judged reference vs mutant with the labels hidden and A/B shuffled).
The audit agreed on 29/30; the one it flagged (`summarize-battery-electrode`,
where the mutant merely *omitted* a caveat) I hardened into a mutant that
*contradicts* the source, so no label in the bank is arguable.

## Result — free local qwen3.5 judge, `orch bench report judge-bank`

accuracy **0.867** · ref-accept **1.000** · mutant-catch **0.733** · paired 22/30

The catch rate splits cleanly by *kind* of error:

| Error class | catch rate |
|---|---|
| **Commission** (states a falsehood / contradicts the source) | **17/17 = 1.00** |
| Quantity (wrong number/magnitude) | 1/5 = 0.20 |
| Omission / partial (drops a required part) | 0/3 = 0.00 |
| Flawed reasoning (subtle) | 3/4 = 0.75 |

Every mutant that *asserts* something wrong — a false fact, an inverted cause, a
conflation, an overstatement, a summary that adds/inverts a claim, a reply that
admits liability, an answer to the wrong question — was caught, 17 for 17. The
misses are two structured blind spots:

1. **Quantity (1/5).** The judge can't catch a wrong number it doesn't itself
   know or won't recompute: cheetah at 70 km/h (true ~110), Challenger Deep at
   8,850 m (true ~10,900), Olympus Mons at 13 km (true ~22), P(two red) = 5/16
   (true 5/14). It read them as plausible and accepted them.
2. **Omission (0/3).** Errors of what's *missing* — the summary that drops the
   coin-cell caveat, the reply that answers only the weather half and omits
   temple etiquette, the instructions that drop "finish the full course." The
   judge grades what's on the page, finds it fluent and true, and never notices
   a required piece is absent.

And it **never falsely rejected a correct answer** (ref-accept 1.000): as an
escalation gate this judge *under*-escalates (keeps some wrong local answers)
rather than over-escalating — the opposite bias from 5c's code judge, which was
conservative and over-escalated. On non-verifiable prose qwen3.5 is permissive.

## What this means for Thesis A

The answer to "does the local judge hold with no oracle?" is **partially, and
predictably.** A free local judge is trustworthy for escalation when the local
model's failure mode is a *stated* falsehood or contradiction — it catches those
perfectly. It is **not** trustworthy when failure is a wrong quantity or a silent
omission; those need either a stronger judge, a tool (calculator / retrieval /
a coverage check against the task's requirements), or a task-shape where the
answer is checkable. Routing policy implication: gate confidently-wrong-claim
tasks locally; for quantity- and completeness-critical tasks, escalate by
default or add a verifier tool rather than trusting the prose judge.

## Shipped

- `experiments/prompts_nonverifiable.jsonl` + `_refs.jsonl` — 30 labeled pairs,
  by-construction ground truth (force-added past the `experiments/` gitignore,
  same as the verifiable bank).
- `routing/nonverifiable_bank.py` — loader + pure `score()` (accuracy,
  ref-accept, mutant-catch, per-bucket, per-defect).
- `routing/judge_gate.py` — `GENERAL_RUBRIC` + a `rubric=` param on
  `build_judge_goal` / `judge_one` (code rubric stays the default; existing
  callers unaffected).
- `orch bench report judge-bank` — drives the judge over the pairs, `--judge-egress local` (free), verdict cache.
- `tests/orchestrator_v2/test_nonverifiable_bank.py` — CI integrity guard
  (structure + length parity) + scorer unit tests. Suite 1465 green.

## Caveats & next

- n=30, one judge (qwen3.5). The commission/blind-spot split is stark enough to
  trust the shape, but exact rates want a bigger bank and a second local model.
- The bank scores judge *discrimination on authored pairs*, not the judge on a
  local arm's *own* fresh outputs — the natural follow-on is a live non-verifiable
  cascade (5c-style) once we accept the discrimination profile.
- Obvious upgrade: give the judge a calculator/retrieval tool and re-measure the
  quantity blind spot; add a coverage-checking rubric pass for omission.
