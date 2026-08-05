# ADR: The per-prompt oracle gap is not evidence for semantic routing

**Date:** 2026-08-05
**Status:** Accepted
**Supersedes the motivation section of:** `docs/specs/semantic-routing.md` (A1)
**Evidence:** `brain/journal/2026-08-05-route-ceiling.md`, findings Era 24
**Tool:** `routing/route_ceiling.py` — `orch bench report route-ceiling`

## Context

Since Era 20, the roadmap's top routing item has been A1 (semantic-augmented
routing), justified by a single measured quantity: the per-prompt oracle beats
the best static arm by +11.6 pts on HumanEval+, with the arms complementary on
19/20 prompts. Era 23 removed the reward as a confounder and restated the
conclusion more strongly — "the arms aren't separable as arms, the win is
per-prompt, semantic routing is the remaining lever."

That chain has an unexamined link: it assumes the oracle gap is *reachable by
some policy*. It never was checked.

## Decision

**Stop treating the oracle-vs-static gap as evidence of routable skill.** It is
an upper bound that is guaranteed positive under pure noise, and on our data it
is empirically unreachable. A1 is demoted from "the remaining lever" to "an
open question pending one measurement" (below). Judge recall becomes the funded
routing-adjacent lever.

## Rationale

**1. The gap is an identity, not a finding.**

For two arms:

```
oracle - round_robin  ==  split / (2n)
```

`split` = prompts exactly one arm passed. Verified exactly on both banks and
property-tested over random matrices (`test_identity_holds_for_arbitrary_random_matrices`).

Two *identical* models, differing only by sampling seed, produce a positive
oracle gap proportional to how often they disagree. The statistic measures
disagreement. It cannot, even in principle, distinguish complementary
capability from coin flips. Every prior citation of it as motivation was
reading a tautology as a result.

**2. The residual is empirically unpredictable.**

A leave-one-out kNN probe — with full-information neighbours, i.e. strictly
more information than any online learner receives, and using the same
retrieval mechanism episodic memory already ships — fails to beat the best
static arm on either bank under either representation tested:

| bank | representation | pass@1 | Δ best-static | p |
|---|---|---|---|---|
| HumanEval+ 164 | handcraft 9-dim | 0.7805 | +0.0061 | 0.625 |
| HumanEval+ 164 | lexical TF-IDF | 0.7866 | +0.0122 | 0.436 |
| 50-bank | handcraft 9-dim | 0.9400 | −0.0200 | 0.668 |
| 50-bank | lexical TF-IDF | 0.9400 | −0.0200 | 0.663 |

p is a 2000-resample label-permutation test that already prices in the
optimism of selecting the best k. Since the probe is an upper bound, no online
bandit over these representations can do better.

**3. The headroom is somewhere else, and it is measured.**

The same tool places the cascade judge on its achievable frontier: the judge
delivers pass@1 0.9207 at esc-rate 0.2256 / $8.47 per 1k, while an oracle gate
reaches **0.9817 at esc-rate 0.1768 / $6.62 per 1k** — +6.1 pts at *lower*
spend. Cheap text features add exactly nothing on top of the judge verdict
(best Δ 0.0000), so the gain lives inside the judge's own recall. This
independently confirms Era 22's structural finding that recall is bounded by
the judge model's solve rate.

## What would reverse this

Two open measurements, both cheap. Either one coming back positive reopens A1:

1. **The semantic row.** MiniLM could not be probed in the environment where
   this ran (egress policy blocks the model host); the tool reports it as
   `unavailable` rather than guessing. Running `orch bench report
   route-ceiling` on the Mac fills the row with zero inference. A significant
   result there means embeddings see structure TF-IDF doesn't.
2. **K=5 reproducibility on the 39 split prompts.** With one sample per
   (prompt, arm), a split cannot distinguish real per-prompt advantage from
   sampling luck. Re-running only the split prompts 5× per arm (~390 local
   generations) settles it: reproducible winners mean there is skill the text
   probe can't see, and the routing question needs a better feature rather
   than closing.

Until both are answered, the honest statement is "no evidence of learnable
per-prompt arm structure on these banks," not "there is none."

## Consequences

- `docs/specs/semantic-routing.md` §1.2's failure modes remain plausible as
  *retrieval-quality* arguments, but §1.1's "invisible ceiling" framing and the
  oracle-gap motivation should not be cited without this ADR alongside.
- Any future claim of the form "the oracle beats X by Y, therefore routing has
  headroom Y" must be accompanied by a ceiling probe. The tool exists now;
  there is no excuse for the bare citation.
- The resume/README learning line stays architectural, unchanged. This ADR
  removes a false lead rather than producing a new number to cite.
