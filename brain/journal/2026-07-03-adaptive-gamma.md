# Session — 2026-07-03 (part 2) — Adaptive per-arm gamma shipped

## What happened

- Fixed the two pre-existing test failures (stale MCP health-check mock;
  env-coupled per-bucket stats test) — commit e4fb058. Suite green for the
  first time in weeks.
- Implemented adaptive per-arm gamma (gamma-spec items 1–5) via TDD, then
  ran a 27-agent adversarial review workflow on the diff before committing.
  The review confirmed **six distinct defects** in the first implementation
  — three high-severity math bugs invisible to short-horizon single-bucket
  tests (per-bucket cold-start death spiral, constant-reward variance
  collapse, noise-floor inversion where stable arms forgot FASTER than
  baseline). All fixed, with regression tests for each. Full deviation
  rationale: [ADR](../decisions/2026-07-03-adaptive-gamma.md).
- Built the drift-injection ablation (exp 6 in `orch benchmark ablation`)
  with dynamic-oracle regret and pick-share metrics; 10-seed τ sweep chose
  the shipped default τ=0.5.

## Results (seed=42 artifact, committed)

| Variant | Final regret | After changepoint | Re-adopts recovered arm |
|---|---|---|---|
| global γ=0.98 | 12.85 | 6.60 | partially |
| adaptive γ | 11.73 | 6.15 | never |
| adaptive γ + recovery | **11.64** | **5.56** | yes |

10-seed sweep: adaptive beats global at every τ in [0.25, 1.5].

## Lessons

- The adversarial-review-before-commit pattern earned its cost: my own
  tests passed while three high-severity math bugs sat in the diff. The
  review agents found them with numeric repros against the real class.
- The spec's recovery nudge was unimplementable as written because our
  dLinUCB only discounts the pulled arm (idle uncertainty never grows).
  The empirical tell: identical ablation curves before the fix.

Commits: e4fb058, 36d2083, 9634466, 54c0ec3, 8a3f8c3. Daemon restarted on
the new code; live rollout is warmup-guarded (state file has no gamma keys).
