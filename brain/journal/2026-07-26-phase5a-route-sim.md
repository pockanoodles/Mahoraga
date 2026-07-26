# 2026-07-26 — Phase 5a: counterfactual routing-vs-baseline (`route-sim`)

## What happened

Turned Era 11's *projected* routing economics into an *exact* computation, and
locked the strategic frame that Mahoraga's north star is **local-first routing
with cloud escalation** (Thesis A), with the "research platform" as an on-ramp
to routing rather than a second product (see the ADR
`brain/decisions/2026-07-26-thesis-a-research-as-onramp.md`).

Because Phase 4 (`bench_run_id=19`) was force-explore, every arm attempted every
prompt — so we hold a full `{arm × prompt}` matrix. That means the head-to-head
headline needed **no new run**: re-grade the 200 stored outputs against the
hidden tests, join the cloud arm's real per-prompt cost, and compute exactly
what any static routing policy would have scored.

## Shipped

- `backend/orchestrator/routing/route_sim.py` — pure simulation logic:
  `grade_matrix` (re-grades via `verify_replay.run_case`), `load_cloud_costs`
  (`decisions.task_goal` → `task_id` → `task_metrics.cost_usd`, ATTACH across
  the two DBs), `simulate` (baselines + routed cascade), `infer_arms`. The
  escalation gate is an **injectable callable** — default oracle; 5b swaps a
  fallible heuristic/judge gate on the same seam without touching the harness.
- `orch bench report route-sim` — thin CLI in `bench_report.py` (Pareto table,
  `--json`, `--local-first` cascade, logs a `bench_runs` row).
- `tests/orchestrator_v2/test_route_sim.py` — 8 tests incl. the verification-tax
  model (wrong-accept costs quality, wrong-escalate costs money). Suite 1438
  green.

## Result (bench_run_id=19, 50 prompts, real per-prompt cloud cost)

| Policy | pass@1 | $/1k |
|---|---|---|
| always-cloud | 1.000 (50/50) | $49.05 |
| always-local: granite | 0.900 (45/50) | $0 |
| best-of-local (any of 3) | 0.940 (47/50) | $0 |
| **routed: granite→cloud (oracle)** | **1.000** | **$6.30** (esc 5) |
| **routed: granite→qwen3.5→cloud (oracle)** | **1.000** | **$5.33** (esc 4) |

**87.2% cost cut single-arm, 89.1% two-stage — at 100% quality.** Every baseline
reproduces Phase 4 exactly, so the grader + cost join are trustworthy. It's 87%
not the round 90% because the 5 prompts granite missed were *pricier* than the
cloud mean — honest number.

## The reframe that matters

The routed row uses an **oracle** gate (escalate iff local truly failed). In
general that's a ceiling. **But on verifiable/code tasks the oracle is real** —
you run the tests as the live escalation gate — so 87–89% is *shippable today*
for code. The ceiling-vs-reality gap only exists for open-ended tasks with no
executable check; that gap is precisely what 5b measures.

## Proven / not proven

- **Proven:** the opportunity is large and exact — local-first + escalation
  buys 100% of cloud quality at ~11–13% of cloud cost on this bank.
- **Not proven:** (a) a *fallible* gate capturing this on non-verifiable tasks →
  **5b** (verification tax: heuristic gate vs LLM-judge gate on the same seam);
  (b) the *bandit's* arm selection adding value over static "granite first" — on
  a Python-only 2-arm bank the escalation does the work, needs a diverse bank;
  (c) live end-to-end → **5c** (real routed run), only after 5b says the gate
  works.

## Next

- **5b** — inject a heuristic gate and an LLM-judge gate into `route_sim`,
  measure how much of the 87–89% ceiling each captures. The LLM-judge is now
  trivially validatable against this run's 50-row ground truth.
- **5c** — a live routed run once 5b confirms a gate that holds quality.
- Package 5a: `routing/route_sim.py` + CLI + tests are on a feat branch for PR.
