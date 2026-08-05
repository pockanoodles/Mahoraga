# 2026-08-05 — Route ceiling: the oracle gap is not a lever (Era 24)

## Why

Three eras have pointed at the same number. Era 20 measured a +11.6-pt gap
between the per-prompt oracle and the best static arm on HumanEval+ and called
it "the quantified motivation for semantic routing." Era 23 fixed the reward,
watched the null survive, and concluded the arms aren't separable *as arms* —
so the win must be per-prompt, so A1 semantic routing is the last lever.

Every one of those steps assumed the oracle gap is *reachable*. Nobody checked.
Before spending an era building A1, this asks the prior question: **how much of
that gap can any router actually capture?**

## What the gap actually is

For a two-arm cross the oracle-over-round-robin gap is an algebraic identity:

```
oracle - round_robin  ==  split / (2n)
```

where `split` is the number of prompts exactly one arm passed. Verified exactly
on both banks (HumanEval+ 0.1189 = 39/328; 50-bank 0.0400 = 4/100), and
property-tested over random matrices.

This reframes three eras of interpretation. The gap does not measure
complementary skill — it measures *disagreement*, and it is guaranteed positive
whenever two arms ever disagree, **including two identical models whose
disagreements are pure sampling noise**. Citing it as motivation for a routing
feature was reading a tautology as a finding.

## The experiment

`routing/route_ceiling.py` + `orch bench report route-ceiling`. Zero inference;
runs off the committed P1 cross and P0 cascade artifacts.

**Probe design — deliberately generous.** Leave-one-out kNN over the prompt
text, where every neighbour contributes its *full-information* outcome (both
arms observed). That is strictly more than any online learner ever sees, so the
probe is an upper bound: if it can't beat the best static arm, no bandit over
the same representation can. It is also the same retrieval mechanism episodic
memory already runs, so the number is the ceiling of machinery we actually ship
rather than of a classifier we'd never deploy.

**Null.** Permute outcome rows across prompts — destroys any text↔winner
association, preserves the marginal pass rates exactly. p = fraction of 2000
shuffles whose best-k probe matches or beats the observed one, so the optimism
of picking the best k is already priced in.

## Results

**A. Arm-selection ceiling — NOT-DETECTABLE, both banks.**

HumanEval+ (164 prompts, 107 all-pass / 18 none-pass / 39 split; best static
0.7744):

| representation | best k | pass@1 | Δ best-static | p |
|---|---|---|---|---|
| handcraft (9-dim TaskContext) | 20 | 0.7805 | +0.0061 | 0.625 |
| lexical (TF-IDF 1+2-gram) | 5 | 0.7866 | +0.0122 | 0.436 |
| semantic (MiniLM) | — | not run | — | — |

50-bank (50 prompts, 4 split, best static 0.9600): both probes 0.9400
(−0.0200), p ≈ 0.66.

Neither representation clears the best static arm at any k with a p-value that
means anything. **The 39 split prompts are not predictable from the prompt.**

**B. Escalation ceiling — JUDGE-SUFFICIENT, and this is where the headroom is.**

Recorded P0 cascade (164 rows, always-local 0.8049, always-cloud 0.9756 @
$35.97/1k):

- judge: esc-rate 0.2256, fail-recall 0.6875 (22/32), 15 over-escalations,
  **pass@1 0.9207 @ $8.47/1k**
- oracle gate: **pass@1 0.9817 @ esc-rate 0.1768, $6.62/1k**

**+6.1 pts of pass@1 available at *lower* cost than the judge already spends.**

Text features add nothing on top of the judge verdict (best Δ = 0.0000, and
lexical actively hurts at −0.0122); without the verdict they collapse to
recall 0.25–0.28. The judge verdict is a sufficient statistic for this decision
— the remaining gain is in the judge's own recall, not in re-ranking its output.

## What this changes

**A1 semantic routing is demoted, not from taste but from measurement.** The
target it was aimed at is a tautology, and the residual is unpredictable from
the prompt under both representations tested. Building a 384-dim retrieval
upgrade to capture it would have been an era spent on noise.

**The cascade gate is promoted.** Two independent lines now converge on judge
recall: Era 22 ("tool-judge recall is bounded by the judge model's own solve
rate") and this ceiling (+6.1 pts at equal-or-lower spend, entirely inside the
judge's recall). That is the lever.

## Honest limits

1. **MiniLM was not tested.** This container's egress policy blocks
   huggingface.co, so the semantic probe reports `unavailable` rather than
   guessing. It is one command on the Mac, where the weights are already
   cached: `orch bench report route-ceiling`. TF-IDF and MiniLM share most of
   the topical signal on HumanEval-shaped prompts, so a large divergence would
   be surprising — but "surprising" is not "measured", and A1 should not be
   closed out until that row is filled in.
2. **One sample per (prompt, arm).** A split prompt cannot distinguish "this
   arm is genuinely better here" from "this arm got lucky this once." The
   probe's null result is consistent with both. The decisive experiment is
   cheap and targeted: **re-run the 39 split prompts K=5× per arm** (390 local
   generations). If the winner is reproducible, there is skill the probe simply
   can't see from text; if it isn't, the gap is sampling variance and the
   question is closed for good.
3. The probe is kNN, not every possible learner. A representation that encodes
   something kNN can't exploit would be missed — though with p ≈ 0.44 and
   0.63, there is not much to miss.

## Shipped

`routing/route_ceiling.py` (representation-pluggable probes, the identity
check, the oracle-gate frontier, both verdicts), `orch bench report
route-ceiling`, 22 tests. The tests plant a *known* signal in one synthetic
cross and pure exchangeable noise in another and require the analyzer to
separate them — so the machinery is validated against ground truth, not just
against whatever the recorded data happens to say.

Test suite 1604. Bugs the tests caught while being written: the frontier was a
quota rather than a cap (non-monotone, and not an upper envelope), rows with no
recorded cloud answer were being scored as cloud failures, and the
NOT-DETECTABLE summary crashed when the permutation test was disabled.

## Next

1. Fill in the semantic row on the Mac (one command, zero inference).
2. K=5 reproducibility run on the 39 split prompts — closes the arm-routing
   question either way.
3. Judge recall as the funded lever: it now has a measured ceiling (+6.1 pts at
   equal spend) instead of a hunch.
