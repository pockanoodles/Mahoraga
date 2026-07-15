# 2026-07-15 — Verifiable (execution-based) rewards

## What we did

Reframed the stuck reward-tie thread and gave the reward an objective axis. The
insight up front: the two prior sessions of null A/B results (memory on/off,
+qwen3-14b) weren't findings about memory or the roster — they were a **saturated
ruler**. On a free, mostly-succeeding local roster, success + cost are ~constant
(~0.65 of the reward weight, pinned), speed is weakly/wrongly correlated with
"better," and the one axis that could separate arms — quality — is a heuristic
that Era 7 already showed rewards elaboration, not correctness. You can't detect
an intervention with a metric that can't rank answers.

Kaito chose the **verifiable-rewards** direction: for code/debug, correctness is
*checkable* (run the code against tests), not judged.

## Built (all offline, zero-new-inference, mirroring reweight/quality-replay)

- `experiments/prompts_verifiable.jsonl` — 18 gold prompts (12 code, 6 debug)
  with hidden Python tests. A builder self-validates the ground truth: every test
  must pass on a correct reference and fail on a planted-broken one, or the build
  aborts. (Caught two of my own bad "wrong" solutions that were secretly correct.)
- `routing/verify_replay.py` + `orch bench report verify` — extract code from a
  bench output, run `solution + tests` under python3, report pass@1 per (bucket,
  agent) next to the heuristic quality score on the same outputs, with a Spearman
  rank correlation + an explicit top-inversion callout. 22 tests.
- Fixed a real live-path bug in `extract_code`: on a truncated/unclosed fence it
  returned the raw output including a literal ```python, poisoning "code" for the
  coder role. Now tolerates unclosed fences (rescued 2 qwen3.5 cases: 0.765→0.882).

## The result (Era 9 in findings.md)

Force-explore, 18 prompts × 4 arms (3 roster + gemma4 canary), memory off,
bench_run_id=14; scored in #15-17.

- **Execution separates the arms where the composite reward couldn't:** pass@1
  spans 0.882–1.000 vs the reward tie at 0.78–0.83.
- **The heuristic doesn't track correctness:** Spearman rho=0.40. The *only*
  100%-correct arm (granite) is ranked 3rd of 4 by the heuristic; in the code
  bucket it gets the *lowest* quality score despite a perfect pass rate, and three
  arms with 0.83/0.92/0.92 correctness get an identical q=0.7417. This resolves
  Era 5's open (a)-vs-(b) fork toward **(b): the scorer is structurally blind to
  correctness**, not "the models are genuinely similar."
- **The canary premise was falsified:** gemma4 (long assumed the weakest arm from
  the May *heuristic* bench) is mid-pack on correctness (0.889); qwen3.5 is
  weakest. Evidence the old heuristic ranking was itself untrustworthy.

## Honest limits

n=18 (arm-vs-arm gaps are 1–2 prompts — the *scorer* conclusion is robust, arm
rankings are not, at this N). Python-only. And live organic traffic has no gold
tests, so a live execution *gate* (Piece A) could only check "runs without
crashing" — catches broken code, not incorrect-but-runnable code. Full
correctness signal lives only in the benchmark.

## Left off

- `verify` is now a reusable ground-truth eval harness — arm ranking on demand,
  no human ranking (which Era 7 showed doesn't scale).
- Open decision: wire a live execution gate for code/test/debug (Piece A), and how
  hard it should gate — vs. using the benchmark to periodically evaluate/calibrate.
- gemma4 reverted to disabled; manual serve stopped; the persistent daemon is still
  OFF (untouched from 07-10) — decide whether to resume it.
