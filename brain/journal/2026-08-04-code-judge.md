# 2026-08-04 — The code-mode tool-judge (generated-test differential gate)

## Why

Era 19's one soft number: fail-recall 0.688 — 10 wrong answers served on
HumanEval+ because granite's failures are plausible, compiling, subtly-wrong
code, exactly where an 8B reading-judge saturates. The queued fix was the
Era-18 move repeated in the code domain: give the judge a tool that
manufactures the missing hidden test.

## Architecture — differential, not generated asserts

`routing/code_judge.py`. The obvious design (judge writes assert-based tests)
repeats tool-judge v1/v2's exact failure: an 8B model *stating* expected
outputs in its head. So the expected outputs are computed by executed code
instead:

1. **GENERATE** — the judge model writes K=3 independent reference
   implementations + a CASES list of test inputs, from the task prompt ALONE
   (never the candidate, never the bank's hidden tests — `differential_check`'s
   signature cannot receive tests at all).
2. **EXECUTE** — every reference and the candidate run on the pooled inputs in
   the tool_judge sandbox. Expected output per input = executed reference
   consensus (≥2 agree, strict majority).
3. **COMPARE** — deterministic: float-tolerant `literal_eval` of printed
   reprs. No LLM anywhere in the compare path.

Recall-only, enforced structurally (can only return False/None); the wrapper
skips the tool entirely on a base reject (K generations are the cost, and
recall-only makes the tool's output unusable there). Entrypoint derivation is
deterministic from the prompt stub (164/164 extracted on the bank; prompts with
no visible signature abstain by design).

## Replay protocol — the counterfactual was free

Because the P0 run recorded the full always-cloud baseline per row
(`run_cloud_always`), `orch bench report code-judge` replays the new gate over
`live_route_humaneval_164.jsonl` and computes the new routed pass@1 and $/1k
EXACTLY from recorded outcomes — the only inference spent was ~381 local qwen
generations (~2 h wall, $0). Recorded verdict = base judge; tool runs only on
the 127 recorded accepts.

## Result (164-task HumanEval+ recorded run, k=3, min_disagree=2)

| gate | fail-recall | wrong served | over-esc | routed pass@1 | $/1k |
|---|---|---|---|---|---|
| reading-judge (Era 19) | 22/32 = 0.688 | 10 | 15 | 0.921 | $8.47 |
| **+ code-judge** | **25/32 = 0.781** | **7** | 19 | **0.939 (154/164)** | **$10.04** |
| always-cloud | — | — | — | 0.976 | $35.97 |

**Headline movement: 94.4% → 96.2% of cloud quality, cost cut 76.5% → 72.1%.**
+3 catches (split_words, check_if_last_char_is_a_letter, cycpattern_check —
all fp→escalation, all cloud-rescued), 4 added over-escalations, all on
cloud-pass rows: money, not quality. Era-18 economics held exactly.

## The two findings

1. **The tool-judge's recall is bounded by the judge model's own solve rate.**
   qwen3.5 solves only 5/10 of the missed tasks (graded its P1 cross outputs —
   zero inference); the catches came from tasks where fresh references could be
   right. "Solver correctness is the limiter" (Era 18), now quantified in code.
   The lever at ≥32 GB: a stronger reference-writer.
2. **One disagreeing input is noise; two are signal.** Raw gate (any mismatch
   rejects): 12 rejects = 3 catches + 9 false alarms, 6 of the 9 with exactly
   one disagreeing input — including the only quality-losing one (HumanEval/124,
   local-pass + cloud-FAIL). `MIN_DISAGREEMENTS=2` keeps all 3 catches (15/4/2
   disagreements) and drops the N=1 noise. CAVEAT: threshold chosen post-hoc on
   this run (n=12 rejects) — principled (mirrors tool_judge's ≥2-of-K
   consensus) but needs the live confirmation run before it's headline-grade.
   The cache stores raw counts, so `--min-disagree` sweeps are free forever.

## Shipped

`routing/code_judge.py`; `orch bench report code-judge` (offline counterfactual
replay, `::code` cache slot, threshold re-derivation from cached details);
`bench live-route --code-judge` / `bench repro --code-judge` (opt-in live gate;
`RoutedCase.judge_detail` provenance). 33 tests; fast lane 1540+ green.
Branch `feat/code-judge`.

## Next

- **Live confirmation run** — `orch bench repro --code-judge` (fresh granite
  outputs, fresh judge, ~5 h + cloud baseline) to confirm the operating point
  off the calibration data; that run's number is the resume headline.
- Reward-fidelity fix (judge verdict as the bandit's success term) — unchanged
  from Era 20, now with a better judge to feed it.
- Case-generation coverage is the cheap recall lever left at 16 GB: more/
  adversarial generated inputs (boundary sweeps), K=5 on accepts only.
