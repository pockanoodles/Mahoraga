# 2026-07-27 — Phase 5d deconfound: a second local judge

## Why
Era 15 (the non-verifiable judge bank) drew its whole discrimination profile from
**one** judge, qwen3.5. That leaves a confound: is "catches stated falsehoods,
blind to quantity/omission, never false-rejects a correct answer" a property of
the *problem* or of *qwen3.5*? The cheapest way to break that — without authoring
new ground truth (which is hands-on) — is to re-judge the identical 30-row bank
with an independent model family. Picked `granite4.1:8b` (IBM Granite lineage,
genuinely distinct from Alibaba's Qwen, already on disk, free). `qwen3:14b` was
rejected as the second judge precisely because it's *same-family* — the weakest
possible deconfound.

## What ran
`orch bench report judge-bank --judge-model granite4.1:8b --judge-egress local`.
No code change — the judge model was already a `--judge-model` flag and verdicts
cache per-model, so qwen3.5's Era-15 cache is untouched. Offline, ~minutes, all
local, $0. (This was the "overnight" candidate; it turned out to be a 5-minute
job, so it ran interactively.)

## Result
granite: **accuracy 0.750, ref-accept 1.000, mutant-catch 0.500 (15/30)** vs
qwen3.5's 0.867 / 1.000 / 0.733.

The deconfound split Era 15 into two halves:

- **Structural (reproduced across families):**
  - `ref-accept = 1.000` on both → neither judge ever falsely rejects a correct
    answer → both **under-escalate** on prose.
  - Quantity (wrong-number) catch = **1/5 on both, exact.** Small local judges
    are blind to wrong-numbers regardless of family.
- **Model-specific (diverged):**
  - Overall catch rate: qwen3.5 0.733 > granite 0.500 — granite is the weaker
    judge, full stop.
  - The Era-15 headline "stated falsehoods 17/17" is a **qwen3.5 property**:
    granite catches only ~9/16 in that zone (misses wrong-fact 3/4,
    inverted-causation 2/2, conflation, off-target).

## Consequences
1. **Tool-augmented judge** (calculator / coverage-check) for quantity- and
   completeness-critical tasks is now **confirmed necessary** — the blind spot
   reproduced independently, it's not a one-model artifact.
2. The routing rule "trust the local judge to gate stated falsehoods" must be
   **scoped to the specific judge**, not generalized to "any local judge."
   **qwen3.5 stays the best single local judge; granite is not a swap-in.**
3. **Ensemble is now an open, testable idea:** granite is weaker overall, but if
   its catches cover any of qwen3.5's *misses*, a "both-accept-else-escalate"
   two-judge gate could raise recall at an escalation-cost. Needs a case-level
   overlap join — the natural next follow-up.

## Caveats
n=30; per-defect cells are 1–5 items, so the *divergence* claims are directional.
The two *exact matches* (ref-accept 1.0, quantity 1/5) are the strong evidence —
and they're the structural ones. Logged to `bench_runs` (mode=judge-bank).
Findings: Era 16.
