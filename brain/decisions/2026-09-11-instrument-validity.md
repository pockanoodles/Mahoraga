# Two instruments, and a contamination probe that renaming would not have given us

**Date:** 2026-09-11
**Status:** accepted
**Amends:** `brain/decisions/2026-09-10-longitudinal-thesis.md` (scope), `brain/decisions/2026-09-11-october-run-shape.md` (adds the probe)
**Evidence:** findings Era 31 (HumanEval+ saturation)

## Context

Era 31 established that HumanEval+ is saturated at the top of the field — a 0.9B
model reports 79.9 on it, tying granite-4.1-8b at a tenth the size. That raised
the obvious question: if the bank is going obsolete, what do we measure on?

**The diagnosis had to be corrected before the fix could be chosen.** Saturation
is not what threatens this study. Our own operating band is local 0.774–0.805,
routed 0.921, cloud 0.976 — a 17–20 point local↔cloud gap with plenty of
resolution in the middle, which is exactly where the cascade works. What
saturation compresses is the *numerator of "94.4% of cloud"*, since cloud fails
only 4 of 164. Real, but narrow.

**The actual threat to a longitudinal claim is contamination.** HumanEval is
2021 and sits in every training set. If granite 4.2's post-training absorbed
more leaked solutions than 4.1's, then "4.2 > 4.1" is leakage rather than
capability — which is precisely the inference weeks 2–3 exist to make. And the
cost axis is immune: dollars per 1k tasks does not care whether an answer was
memorised.

## Decisions

### 1. HumanEval+ stays the anchor. Not negotiable.

It is what makes two dated points comparable, it is externally authored (which
is what closed the "who wrote your 50 tasks?" hole), and it is what
`bench verify` binds the published claims to. Swapping the instrument mid-study
forfeits the entire longitudinal argument. Its saturation and its single-task-shape
limits are *published limitations*, not reasons to change it.

### 2. A contamination probe on the anchor — and renaming is the wrong probe

The first design was mechanical renaming: rename the entrypoint and parameters,
keep semantics identical, see whether pass@1 holds. **Rejected, and the reason
is the load-bearing part of this decision.**

Contamination means the model has learned HumanEval/0's solution. Renaming
breaks exact-match *retrieval*, but a model that genuinely internalised the
solution will still emit it under a new name — and emitting the right body *is
the correct behaviour*. A pure rename therefore cannot separate memorisation
from synthesis; a model that passes the renamed bank has told us nothing. Worse,
if the new names are unnatural (`f_0042`), a drop measures confusion rather than
contamination, and the probe reads backwards.

**The probe has to change the required semantics so that the memorised answer is
wrong.** Adjust the spec in the docstring — an inclusive bound becomes
exclusive, a return convention inverts, an edge case gains a required
behaviour — and edit the canonical solution to match. Expected outputs are then
recomputed mechanically by executing the *edited* solution over the *same*
inputs, so the input distribution is held fixed and only the oracle moves.

Now the reading is sharp: a model reciting the memorised body **fails**, and only
a model that actually read the spec passes. It doubles as an instruction-following
measurement, which is independently worth having.

### 3. The validity gate: a variant only counts if the memorised answer fails it

Encoded as a build-time invariant rather than a review convention. For every
variant, the builder requires:

- **Gate A** — the variant's own reference passes the variant's tests.
- **Gate B** — the **original** canonical solution *fails* the variant's tests.

Gate B is the whole probe. If the original solution still passes, the spec edit
did not bite on this input set, the variant cannot discriminate a memorised
answer, and it is **rejected at build time**. A variant that silently fails to
discriminate would produce a reassuring null and quietly retire a live question —
the same failure mode as the funnel's 0.0%, one level up.

Tooling: `experiments/build_humaneval_variants.py`, reusing
`build_humaneval_bank.py`'s existing oracle path (`_compute_cases`,
`_make_tests`, `_verify`) rather than reimplementing it.

### 4. Scope: n≈30–40 hand-authored variants, not all 164

If memorisation is doing the work, the effect is large, not marginal — n≈30–40
resolves it. Authoring 164 semantic variants would also risk changing difficulty
non-uniformly, which would confound the very comparison it exists to protect.
Authoring follows the pattern the non-verifiable bank used: draft → curation →
independent adversarial audit.

**The probe needs only the local arm.** No judge, no cloud, no escalation — it
asks one question, "does granite's pass@1 survive a spec edit?" So it costs
~35 minutes of local inference per arm per condition and **zero dollars**. This
is much cheaper than it first looked.

### 5. A second instrument in week 3: a small verifiable edit bank

**This reopens an item the 2026-09-10 ADR parked** ("repo-context / cross-repo
serving as a build — real, but bigger than this window; the natural next
window"). Reopened deliberately, on Kaito's call, because new evidence landed
after that ADR was written: the funnel's exclusion breakdown shows **1302 of
1617 recorded actions were `edit-in-place`** — "change this function, in this
file, in this repo." No Python-synthesis bank of any vintage measures that task
shape.

Scope is deliberately small: **n≈30–50 verifiable edit tasks** — an existing
file, a requested change, hidden tests — run through the cascade alongside
HumanEval+. Contamination-proof by construction, since the files are authored
against our own repos. This is *not* the full repo-context serving build; it is
an instrument, and it stays an instrument.

### 6. Week 4 reports quality retention as a range across instruments

A break-even model with a sensitivity band on the quality parameter is stronger
than one with a point estimate from a single bank — and it is the business-side
read rather than the CS-side read. Retention also gets reported **alongside
absolute routed pass@1**, because a fixed local tier against a rising frontier
makes the *ratio* decay even while the economics improve. The ratio is the wrong
summary statistic for a widening-gap world; publishing both prevents a
misreading that the method is failing when it is not.

## Consequences

**Week shape, revised.** W2 = the two-axis longitudinal run + the contamination
probe (cheap, local-only). W3 = the edit bank, authored and run. W4 = the
break-even framework over two instruments with a retention band. W5 unchanged —
land it, `claims.json`, `bench verify` green.

**Schedule risk is real and accepted.** W3 previously held slack; it now holds an
authoring task. The mitigation is that the longitudinal run itself is mostly
wall-clock rather than attention, and the probe is ~35 minutes. If W3 slips, the
edit bank ships as an instrument with its numbers and without a break-even
column of its own — the anchor still carries the framework.

**What this does not reopen.** The bandit research line stays parked. The edit
bank is an *instrument*, not repo-context serving; building the arms a repo is
still next-window work.

## Open

A proposed amendment to the thesis sentence is **not** taken in this decision and
is Kaito's call. The current framing — *"does local-first routing still pay as
the frontier moves?"* — concedes decay in the title. Era 31 supports a stronger
one: the frontier moved up and out of consumer hardware, which makes local-first
routing structurally *more* necessary, with the break-even model saying exactly
when that stops being true. Same measurements either way; it changes the framing,
not the work.
