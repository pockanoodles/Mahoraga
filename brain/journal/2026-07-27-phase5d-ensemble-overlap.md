# 2026-07-27 — Phase 5d: two-judge ensemble overlap join

## Why
Era 16 (second judge, granite) left one thread: granite is the weaker judge, but
if its catches cover qwen3.5's *misses*, a "both-accept-else-escalate" ensemble
raises recall at zero false-escalation cost (both judges have ref-accept 1.0, so
a union gate never wrongly escalates a correct answer). Worth an ensemble? The
join answers it with no new inference — both judges' per-case verdicts were
already cached in `~/.mahoraga-v2/judge_bank_cache.json` from Eras 15/16.

## Method
Pure offline set join. For each of the 30 bank rows: caught = mutant verdict is
`False` (rejected); false-escalate = reference verdict is `False`. Ensemble (union)
catches a mutant if *either* judge rejects; intersection catches only if *both* do.

## Result
| gate | mutant-catch | ref-accept |
|---|---|---|
| qwen3.5 alone | 22/30 = 0.733 | 1.000 |
| granite alone | 15/30 = 0.500 | 1.000 |
| ensemble (union) | 23/30 = 0.767 | 1.000 |
| intersection | 14/30 = 0.467 | — |

- **granite covers exactly 1 qwen3.5 miss** (`instruct-kyoto-two-part`,
  partial-answer). Whole upside: +1 mutant.
- **qwen3.5 covers 8 granite misses.** granite ≈ a strict subset (14/15 of its
  catches are also qwen3.5's). No complementary structure to exploit.
- Union false-escalation = 0 → ensemble is "free but pointless."

**Residual blind spot — 7 mutants neither family catches:**
4× wrong-quantity (deepest-ocean, fastest-land-animal, olympus-mons,
two-red-marbles), 2× subtle-omission (antibiotic-handling, pipes-tank),
1× flawed-reasoning (sailing-upwind).

## Consequences
1. **Don't build the ensemble.** Insufficient diversity; granite is redundant.
2. **qwen3.5 is *the* single local judge** — strictly dominates granite (8 unique
   catches vs 1) at equal, perfect ref-accept.
3. **Tool-augmented judge is now the ONLY remaining lever, proven not asserted.**
   Two independent families both miss all 4 quantity errors and 2/3 omissions —
   no local-judge ensembling closes that. A calculator attacks quantity; a
   coverage/checklist check attacks omission. This is the next build.

## Caveats
n=30, residual cells small (4 quantity / 2 omission / 1 reasoning) — but the
direction is unambiguous: 0 of 4 quantity mutants caught by *either* family. No
code shipped (analysis over the Era-15/16 caches). Findings: Era 17.
