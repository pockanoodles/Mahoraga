# 2026-07-26 — Phase 5c: the cascade, run live end-to-end

## What happened

5a computed the routing ceiling and 5b the verification tax — both by **replaying**
the run-19 force-explore matrix (stored outputs re-graded, stored cloud costs
re-joined, zero new inference). 5c closes the loop: it runs the whole cascade on
**fresh inference**. `orch bench live-route` takes each gold prompt, runs granite
live, has the free local qwen3.5 judge decide correct/incorrect from prompt+output
alone (the production posture — no hidden tests), and escalates to `claude-cli`
live only on a fail verdict. Every served answer is then graded against the hidden
tests. Nothing is read from disk.

## Results (50 prompts, all fresh)

| Policy (LIVE) | pass@1 | $/1k |
|---|---|---|
| always-cloud (claude-cli) | 1.000 (50/50) | $47.66 |
| always-local (granite) | 0.880 (44/50) | $0.00 |
| **routed: granite→judge→cloud** | **1.000 (50/50)** | **$10.54 (77.9% cut)** |

Judge: accuracy 0.920, **fail-recall 6/6 = 1.000**. It caught every one of granite's
6 real failures (all cloud-recovered), served no wrong answer (fp=0), and
over-escalated 4 correct answers (fn=4). 10 escalations total.

**Thesis A, proven live: local-first routing with a free local judge retained
100% of cloud's verified pass@1 at 22% of the cost.** The live cross-check (sum of
served grades / charged costs) matched the simulator's routed line exactly, so the
in-process cascade and the `route_sim` aggregation agree.

## The honest 5c-vs-5b difference

- **5b (replay):** routed 0.960 @ $6.10/1k. Judge recall 3/5 — under-escalated,
  leaked 2 wrong answers, so cheaper but not perfect quality.
- **5c (live):** routed 1.000 @ $10.54/1k. Judge recall 6/6 — over-escalated 4
  correct answers, so perfect quality at higher spend.

The live judge sat at a **more conservative operating point**. Why: it judged
*fresh* granite outputs, not run-19's stored ones (and qwen3.5 verdicts carry
their own nondeterminism). The verification tax showed up live as **money, not
quality** — ~$0.19 on 4 needless cloud calls. I'll take that: 100% pass@1 beats
saving four cents. always-cloud measured $0.0477/task, matching Phase-4's $0.0491
— cost capture is stable across runs.

## Why this architecture (not a throwaway script)

`routing/live_route.py` is the reusable cascade, not a one-off harness:
- `route_one` — the live local→judge→cloud policy, grading each step.
- `to_matrix` — folds live cases into the exact shape `route_sim.simulate` wants,
  so 5b's aggregation (and its `gate_cost_per_task` accounting) runs **unchanged**
  on fresh data. The offline and live paths share one aggregator.
- `load_arms` — builds the arms faithfully from `agents.yaml` (options, max_ctx,
  extra_payload), so the local arm behaves exactly as the configured roster arm.

`route_one` is the exact primitive a serving-path productization would call: wire
it into `executor.py`'s local-verdict seam (replacing the heuristic
`validate_*_output` at the ollama branch) and the gate becomes a live serving
feature. The proof and the reusable component are the same code.

## Shipped

- `routing/live_route.py` (`route_one`, `to_matrix`, `load_arms`, `RoutedCase`).
- `orch bench live-route` — preflight for the vanished-models gotcha, honest full
  always-cloud baseline by default (`--escalate-only` to spend less), per-case
  JSONL output, live cross-check against the simulator.
- 9 tests (fake workers + real grading + cost accounting + yaml load); suite 1455.
- Experiment spend: $2.38 total cloud (50 baseline calls); the routed policy alone
  would spend $0.53 (10 escalations, judge free). Logged mode=live-route.

## Also: recovered a phantom merge

PR #23 (5b judge-gate) showed "merged" on GitHub but had landed on the orphaned
#22 branch, never on `main` — `judge_gate.py` was absent from trunk. Cherry-picked
the clean commit and merged PR #24 to restore it. Stacked-PR lesson: retarget a
child PR's base to `main` before merging the parent, or merge child-first.

## Next

- **Harder / larger bank** — 6 failures / 50 is still a small fail class; recall
  needs more failures to trust the exact number.
- **A non-verifiable-task bank** — the judge's real job. Everything through 5c is
  code with hidden-test ground truth; on those you could just run the tests. The
  open question is whether the local judge holds on tasks where no oracle exists.
- **Productize into serving** — wire `route_one` into `executor.py` so the gate is
  a live `/api/task` feature, not only a bench command.
