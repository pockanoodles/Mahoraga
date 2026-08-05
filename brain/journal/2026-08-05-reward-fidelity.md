# 2026-08-05 — Reward fidelity: judge verdict as the success term (Eras 20 → 23)

## Why

Era 20 diagnosed the P1 routing null: the composite reward's success term was
the execution gate's "ran without crashing", which saturates at ~1.0 on a
mostly-compiling local roster while true pass@1 sat at ~0.77. With success and
cost pinned, latency was the only gradient, so cold-start LinUCB correctly
learned the wrong thing — chase the faster arm. The fix chosen then: make the
success term correctness-faithful using the free local judge, which the
code-judge work (Era 22) had just made meaningfully better (recall 0.688 →
0.781).

## What shipped

**PR #34 — the core.** `TaskOutcome.correctness: float | None` scales the
success term (`w_s * c`) in `RewardCalculator.compute`. Design decisions:

- **`None` ≡ 1.0 ≡ legacy, bit-for-bit** — the change is provably inert
  wherever the judge doesn't run (regression-tested to exact equality).
- **Exec gate stays the hard floor** — `success=False → reward 0.0` regardless
  of judge opinion; "doesn't run" is ground truth, the judge is opinion.
- **Inline invocation after `elapsed` capture** in `/api/task`, so judge
  latency never pollutes the speed term. Async/post-hoc was rejected: the
  observe() sequencing (double-run alt, metrics, drift pacer, state save)
  would need a background-ordering refactor to save seconds that only matter
  interactively.
- **`MAHORAGA_REWARD_JUDGE=off|on|code`** (default on, mirroring
  `MAHORAGA_EXEC_GATE`); `code` layers the recall-only differential check on a
  base accept, exactly the `route_one` pattern including crash-to-abstain.
  `MAHORAGA_REWARD_JUDGE_MODEL` defaults to qwen3.5 — the sole proven local
  judge (Era 17).
- **Judge errors can only abstain** — `judge_correctness` never raises;
  any failure → `None` → legacy reward + detail string.
- **Buckets = `EXEC_GATE_BUCKETS`** — the judge rubric is only measured on
  code-like tasks; drifting the two surfaces apart would silently change what
  "success" means.
- Decision log migration: `correctness`, `judge_cost`, `judge_detail` columns;
  the OLS reward-weight learner's success regressor becomes the correctness
  value, making w_s identifiable for the first time.

**PR #35 — the proof.** `orch bench report reward-judge`: zero-LLM-inference
replay. Environment = the recorded P1 cross (re-graded per (prompt, arm) via
`verify_replay.run_case`); scoring = the real `RewardCalculator` under four
variants (legacy / oracle / synthetic judge at recall 0.688, FPR 0.114 and
0.781, 0.144); policy = fresh in-memory LinUCB over 20 shuffled orderings,
with round-robin / static / per-prompt-oracle baselines from the same matrix.

## The result (reported exactly — two of three pass criteria failed)

| variant | pass@1 | disc-acc (39) | reward↔pass r |
|---|---|---|---|
| legacy | 0.7808 | 0.540 | 0.119 |
| oracle | 0.7668 | 0.481 | 0.995 |
| judge-plain | 0.7677 | 0.485 | 0.980 |
| judge-code | 0.7732 | 0.508 | 0.985 |

Baselines: round-robin 0.7713 · static granite 0.7744 · static qwen 0.7683 ·
per-prompt oracle 0.8902.

- Legacy reproduces Era 20 (disc-acc ≈ coin flip; its 0.7808 is latency-luck,
  not learning) — the diagnosis replicates. **PASS.**
- Oracle-reward LinUCB does NOT beat round-robin. **FAIL — and that's the
  finding.** On the 50-bank the mechanism is visible: the arm reward leader
  flips from granite (legacy latency artifact, reward gap 0.0216) to qwen, the
  truly better arm, under oracle (gap 0.0024) — but a 0.002–0.024 reward gap
  is below what cold-start LinUCB separates in 50–164 pulls, and judge noise
  (transmission ≈ recall−FPR ≈ 0.57/0.64) attenuates it further.

## The interpretation

Era 20 couldn't distinguish "broken ruler" from "no arm-level signal." Era 23
fixed the ruler — reward now measures correctness (r 0.12 → 0.98+) — and the
null persists. So the arms genuinely aren't separable *as arms* on these
banks; the +11.6-pt oracle gap lives per-prompt (arms complementary 19/20),
invisible to the lexical 9-dim context vector. **Semantic routing (the A1
spec) is the remaining routing lever, and the reward is now ruled out as a
confounder.** The live reward-judge still pays for itself: production bandit
state stops training on "ran without crashing."

Resume consequence, unchanged from Era 20: still no honest "bandit beats X"
number; the learning line stays architectural. The honest new sentence is
"reward measures execution-verified correctness, not crash-freedom."

## Also fixed on the way

Latent `NameError` in `code_judge_cmd`'s fresh-check path
(`verdict_effective` unassigned on cache misses) — masked in Era 22 by a warm
cache; would have crashed the planned K=5 sweep on row 1. First CLI-level
regression test for the command; verified the test fails without the fix.

## Shipped / state

PRs #34 (merged, 27 new tests) and #35 (merged, 11 new tests + NameError fix).
Suite 1581 green. Overnight in parallel: the live `bench repro --code-judge`
confirmation run (attempt #2, after the OverflowError fix in PR #33 and the
sleep/caffeinate lesson). Next: confirmation numbers → headline decision, K=5
case-coverage sweep, then A1 semantic routing.
