# 2026-08-03 — The cascade on HumanEval+ (164 external tasks)

## Why this run

Every cascade number so far rested on the 50-task homemade bank. Strong for
falsifiability (paired mutants, CI guard), weak for external validity — "1.000
pass@1" on self-authored tasks is the first thing a skeptic punctures. This run
re-measures the identical live cascade (`orch bench live-route`, no code changes)
on **HumanEval+** (EvalPlus v0.1.10, 164 problems), converted offline into the
verifiable-bank schema by `experiments/build_humaneval_bank.py` (journal
2026-08-03 commit 93d05f7: contract-filtered inputs, oracle outputs computed from
the canonical solution, atol-aware `_meq` comparator, ARG_MAX-safe test scripts,
every reference verified through the real `run_case` path).

## Results (164 prompts, all fresh inference, ~3.4 h wall on the 16 GB M-series)

| Policy (LIVE) | pass@1 | $/1k |
|---|---|---|
| always-cloud (claude-cli/sonnet) | 0.976 (160/164) | $35.97 |
| always-local (granite4.1-8b) | 0.805 (132/164) | $0.00 |
| **routed: granite→judge→cloud** | **0.921 (151/164)** | **$8.47 (76.5% cut)** |

Judge (qwen3.5, prompt+output only, no tests visible): accuracy 0.848,
**fail-recall 0.688** (caught 22 of granite's 32 real failures), 10 wrong answers
served (fp), 15 over-escalations (fn), 37 escalations total (22.6%). Cloud failed
to rescue 3 escalated tasks (HumanEval/32, /132, /163) and missed 4 outright in
its own baseline — sonnet is not a 1.000 oracle on HumanEval+ either.

Tier gradient is clean and monotone:

| Tier | local | routed | cloud | escalations |
|---|---|---|---|---|
| easy (55) | 0.873 | 0.964 | 1.000 | 7 |
| medium (55) | 0.800 | 0.945 | 0.982 | 15 |
| hard (54) | 0.741 | 0.852 | 0.944 | 15 |

## The headline, honestly stated

**Routed retained 94.4% of cloud's verified pass@1 (0.921 vs 0.976) at 23.5% of
the cost, and beat local-only by +11.6 points for $8.47/1k.** Not the 5c story
(exact parity). The difference is entirely the judge's operating point.

## Era-14 vs now: the judge's operating point moved with the failure class

- Homemade 50 (5c): fail-recall **6/6 = 1.000**, zero wrong answers served —
  parity at 22% of cost.
- HumanEval+ 164: fail-recall **22/32 = 0.688**, 10 wrong answers served.

Same judge, same prompt, same posture. What changed is *what a failure looks
like*: the homemade bank's failures were mostly structurally broken outputs the
judge could spot from the prose; granite's HumanEval+ failures are plausible,
compiling, subtly-wrong implementations (off-by-one edge cases, missed spec
clauses). Reading prompt+code and asking "is this correct?" is exactly the regime
where an 8B judge saturates. The 10 missed IDs (10, 22, 25, 93, 125, 126, 127,
134, 145, 154) skew medium/hard-tier string/parsing tasks.

This is the code-domain twin of the Era-18 lesson: judgment-by-reading has a
structural ceiling; the fix is a **tool**. For code the tool is obvious and
better than the text case: the judge writes its *own* test cases from the prompt
(it never sees the hidden bank tests) and executes the candidate against them in
the `execution_gate` sandbox. A generated-test gate would have to reproduce only
one failing input per miss to convert fp→escalation — each one is money (an
escalation), not quality, exactly the 5c economics. That's the next lever on
fail-recall, queued behind P1.

## What this does to the thesis

Thesis A survives contact with an external benchmark, restated: **local-first
routing with a free local judge holds ~94% of cloud quality at ~24% of cloud
cost on HumanEval+** — and the *shape* is robust (big cost cut, small quality
gap, gap fully attributable to judge recall). The 5c parity result stands as the
homemade-bank data point, but the resume/README claim now cites the external
bank first. n_fail=32 also finally gives fail-recall a denominator worth
trusting (5c had 6).

## Cost accounting

Cloud spend this run ≈ $7.29 ($5.90 baseline sweep + $1.39 routed escalations);
judge $0 (local). `run_cloud_always` measured per-task cloud at $0.0360,
consistent with 5c's $0.0477 modulo output-length variance. Logged
mode=live-route, notes "humaneval+ full 164, feat/humaneval-bench, P0 resume
bench". Per-case: `experiments/live_route_humaneval_164.jsonl`.

## Next

- **P1** — bandit vs derived round-robin vs static-best on this 164 bank
  (isolated `HOME` per policy, pass@1 as the only cross-policy metric,
  winnability precheck from the Phase-4 cross before claiming a bandit win).
- **Code-mode tool-judge** — generated-test gate targeting the 10 misses.
- **P3** — one-command repro + CI badge.
