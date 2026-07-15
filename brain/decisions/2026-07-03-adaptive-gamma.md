# ADR: Adaptive per-arm gamma — implementation deviations from spec

**Date:** 2026-07-03
**Status:** Accepted
**Spec:** `docs/specs/gamma-spec.md`

## Context

Implemented spec items 1–5: per-arm adaptive discount in
`LinUCBPerBucketRouter`, drift-injection ablation (`_exp6`), τ sweep. A
27-agent adversarial review of the first implementation confirmed six
distinct defects (three high-severity); the shipped version fixes all of
them. The deviations below are load-bearing — future readers should treat
this ADR, not the spec's pseudocode, as the description of what runs.

## Deviations from the spec (and why)

1. **Warmup is per-(bucket, arm), not per-arm.** The model being scored is
   the bucket-local θ. With a per-arm counter, a converged arm's FIRST pull
   in a new bucket scored a cold θ and slammed the shared γ to ~γ_min in
   every bucket (repro: γ 0.9949 → 0.9322 off one task) — the exact death
   spiral the warmup guard exists to prevent. Cold-cell errors never reach
   the EMA; cold-cell updates apply the global decay. Per-bucket pull
   counters persist in the state file (missing → conservative re-warmup).

2. **The mapping is centered on the noise floor: γ = γ_min +
   (γ_max−γ_min)·exp(−max(0, E−1)/τ).** With variance normalization, a
   converged arm's E equilibrates at ≈1 *by construction* (prediction error
   = irreducible reward noise). The spec's mapping exp(−E/τ) put stable
   noisy arms at γ≈0.963 < 0.98 forever — stable arms forgot FASTER than
   the pre-feature baseline, inverting the headline behavior. Now E≤1 →
   γ_max (stable arms keep confidence); only excess above the floor reads
   as drift.

3. **Variance floor (1e-3) + per-observation cap (25 ≈ 5σ).** Under
   constant rewards the variance EMA decays geometrically toward 0 while
   ridge-bias error decays slower — eps_norm grew ~1.09ⁿ and drove the most
   stable arm to γ_min around pull ~150. The spec's 1e-8 epsilon is orders
   of magnitude below realistic err² and protects nothing. The cap bounds
   any single unlucky task's influence.

4. **Recovery decays the error EMA itself, not a γ side-table.** The
   spec's §3 nudge ("γ_a += rate·(γ_default − γ_a) for unpulled arms") is
   inert in this codebase: applied γ is recomputed from the EMA on every
   pull, so a nudged bookkeeping value is never read. Deeper cause: our
   dLinUCB discounts only the pulled arm's matrices, so an idle arm's
   uncertainty never grows — the spec's premise. Decaying pred_error_ema
   (rate 0.01/round) makes drift-detection memory fade, which the applied
   γ actually reflects. Ablation confirms the fixed recovery variant is the
   only one that re-adopts a recovered arm (post-share 0.10 vs 0.00).

5. **All γ bookkeeping is scaled by the off-policy weight w.** Quarantine
   probes arrive with w=1e-3; unweighted, they drove the EMA and warmup at
   full strength. EMA blend rate is (1−β)·w; pull counters accumulate w.

6. **τ default = 0.5 on the excess scale** (spec's raw-scale 0.05 predates
   normalization). 10-seed drift sweep: adaptive beats global at every τ in
   [0.25, 1.5], monotone toward small τ (13.72–14.30 vs global 14.38);
   0.5 chosen one step in from the grid edge. Committed seed-42 artifact:
   adaptive+recovery 11.64 vs global 12.85 final regret (−9.4%),
   after-changepoint 5.56 vs 6.60.

## Consequence

Rollout is warmup-guarded: the live state file has no gamma keys, so every
(bucket, arm) cell re-warms over its next 10 real pulls at γ=0.98 before
adaptation begins. `adaptive_gamma=False` gives the exact pre-feature
baseline (used by the ablation). Remaining spec items: full sweep grid with
detection/recovery metrics (item 6), distance-weighted episodic α and
blended OLS reward transition (items 7–8, separate specs).
