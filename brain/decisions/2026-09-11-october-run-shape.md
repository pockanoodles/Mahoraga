# The October run measures two axes, because the frontier consolidated out of the 16 GB tier

**Date:** 2026-09-11
**Status:** accepted
**Supersedes the contingency clause in:** `brain/decisions/2026-09-10-longitudinal-thesis.md`
**Evidence:** findings ledger Era 31

## Context

The longitudinal thesis (2026-09-10) made one item blocking: survey local models
released since ~June 2026 that fit 16 GB. The answer decided whether weeks 2–3
were a real two-point trend or a flat line. The written contingency was binary —
**if a better local arm shipped**, re-measure with it; **if not**, hold the local
arm fixed and make the trend purely pricing-side.

The scout ran 2026-09-11 (three parallel passes, cross-validated, every
load-bearing quant size verified against Ollama `/tags` or the Hugging Face
blobs API). Full detail in Era 31. The short version:

- **No *verified* Python-synthesis upgrade for 16 GB shipped in the window.**
  The flagship lines consolidated upward, out of the class: Qwen 3.6/3.8 ship no
  member under 17 GB; `qwen3-coder`'s smallest is 19 GB; Meta's line became
  "Muse" at 17 GB minimum; no Phi-5, no StarCoder3, no open Mistral ≤14B. Aion
  1.0 and Muse Spark verified not shipped.
  **Corrected same day:** "the size class was vacated" was too strong — it
  conflated *no verified upgrade* with *no releases*. IBM shipped granite 4.2
  8B and 3B (2026-08-25), `ornith-ai` shipped a 9B (2026-08-18), Microsoft
  shipped Fara1.5-4B (2026-07-17). The slot is occupied and newer than our
  incumbents; nothing in it has a verified synthesis advantage. See Era 31's
  correction block and Era 32.
- **The MoE memory rejection re-confirmed against the new generation** —
  Laguna XS 2.1 (33B-A3B) 20 GB, `granite4.2:30b` 18 GB, `qwen3.6:35b-a3b`
  23–24 GB. Total params must be resident; active-param marketing does not
  change the memory bill.
- **The Python-synthesis leaderboards are frozen.** EvalPlus's `results.json`
  holds 125 models whose newest are DeepSeek-V3 and Qwen2.5-Coder-32B — zero
  2026 models, no Granite at any size. LiveCodeBench's board and Aider polyglot
  hold no model ≤14B. "X beats granite on Python synthesis" is currently
  unfalsifiable from published data.
- **One in-window release matters:** `granite4.2:8b` (2026-08-25, Apache 2.0) —
  a post-train of the *same* 4.1 base with a thinking toggle, byte-identical
  footprint (5.35 GB Q4_K_M, same 40 layers / hidden 4096 / vocab 100352).
  IBM published HumanEval+ **79.88** for 4.1-8b (the 80.21 often seen is the
  "Eval+ Avg"; an earlier note of 80.49 was wrong) and **nothing** for 4.2-8b.

## Decision

**The October run measures two axes, not one.**

1. **Local-side axis: granite 4.1-8b → 4.2-8b.** Same bank, same method, same
   memory budget, zero roster restructuring. This is a genuine second local
   point, not a substitute for one — it just happens to be nearly free.
2. **Pricing-side axis: the same bank re-costed at current frontier prices**,
   recorded as dated data rather than a constant in code.

The contingency's "pricing-side only" fallback is therefore **not** what
happens. It was written for a world where the local tier had no new point at
all, and that turned out to be one release too pessimistic.

**Three supporting calls:**

- **The bank stays fixed at HumanEval+ 164.** Identical method across the two
  dated measurements is the entire value of a longitudinal study; changing the
  instrument would forfeit it. But **HumanEval+ saturation is now a stated
  limitation rather than an unexamined assumption** — K2-Horizon-**0.9B**
  reports 79.9 on it, tying granite-4.1-8b at a tenth the size while scoring
  ~36 points lower on LiveCodeBench v6. The bank cannot distinguish the top of
  this field. Say so; do not fix it by swapping banks mid-study.
- **`granite4.2:8b` is pulled now, not in week 2.** 5.3 GB, no memory-budget
  change, and having it resident de-risks the run. Pulled 2026-09-11.
- ~~**`ornith-1.5:9b` is a stretch arm, not a committed one.**~~ **AMENDED
  later the same day — see "Scope amendment" below.** The original reasoning
  stands on the merits (contested vendor number, Qwen3.5 lineage so correlated
  with the existing arm), but Era 32 changed what the run is *for*.

## Scope amendment (2026-09-11, after the tier map — Era 32)

Two cheap local experiments are promoted into week 2. Both are local-only and
cost **zero dollars**.

**1. `ornith-1.5:9b` becomes a committed W2 arm, not a stretch arm.** Its
justification changed. As a roster candidate it was weak — a contested vendor
number on a correlated lineage, worth running only with time to spare. As an
input to a **hardware purchase** it is the single highest-leverage measurement
available: it claims SWE-bench Verified 70.6 at 5.78 GB, which is Laguna-XS
class (a 20 GB MoE) at a quarter of the memory. If it replicates, this 16 GB
machine already sits at the 24 GB tier, the 16→24 GB delta collapses from +23
to ~+8 points, and **32 GB unified becomes the better buy than a used 3090.**
A ~1-hour local run that informs an ~$800 decision is not a stretch item.

The distrust is retained and sharpened in the roster comment: Ornith
RL-trains the *scaffold* alongside the solution rollouts, so 70.6 measures
model + learned scaffold and is **not protocol-comparable** to granite's
plain-harness figure. Running it on our harness is precisely what makes the
comparison protocol-matched. Using the Ollama tag despite its ~922 MB vision
projector — the leaner HF text-only GGUF is noted as the fallback, but tag
simplicity beats 0.8 GB for a first head-to-head that still fits in 16 GB.

**2. `granite4.1-guardian:8b-q4_K_M` gets measured as the judge.** 5.1 GB,
Apache 2.0, a *purpose-built* verification model at the incumbent judge's exact
footprint. The reason this is not a fishing expedition: Eras 15–17 established
that local judges catch stated falsehoods but miss omissions and wrong
quantities **structurally, across two model families** — and detecting exactly
that class of defect is what a guardian model is trained for. The existing
30-row non-verifiable bank already measures that blind spot, so this is a
`--judge-model` swap and ~5 minutes, the same shape as the Era-16 deconfound.

**Guardrail on the second one:** the judge is inside the longitudinal
comparison. If the guardian wins, it does **not** get swapped into the October
cascade run — that would change two variables at once and forfeit the
method-identical property the whole study rests on. It is measured now and
adopted, if at all, *after* the two-axis run lands. Era 17's finding that
qwen3.5 is the sole local judge stays in force for the study.

Both arms are registered in `agents.yaml` as `enabled: false`, the established
convention for bench-only arms (as `claude-cli` is).

### Consolidation: the local-side axis and the hardware question are ONE run

Decided 2026-09-11, on Kaito's call, after the arithmetic was checked properly.

The first framing had ornith as a separate ~1-hour experiment. That was wrong on
cost: 164 prompts at ~20 s is **~55 min per arm**, so ornith-vs-granite-4.1
alone is ~1.8 h, and three pairwise runs come to ~5.5 h.

But the October local-side axis is *already* a granite 4.1-vs-4.2 run over the
same bank. So it becomes **one force-explore pass with three arms** —
`ollama:granite4.1-8b`, `ollama:granite4.2-8b`, `ollama:ornith-1.5-9b` — over
HumanEval+ 164. 492 tasks, ~3–3.5 h, and it yields both results at once:

1. the **longitudinal local-side point** (4.1 → 4.2), and
2. the **hardware answer** (does ornith's claimed 70.6 survive a
   protocol-matched harness?).

**The consolidation is not just cheaper, it is methodologically better.** Era 24
measured the local arm wobbling ±3 points across two fresh runs on the same
bank, and Era 31's whole problem is that vendors report on incomparable
harnesses. Running all three arms in one session puts them on the same bank, the
same judge configuration, the same thermal conditions and the same decoding
settings — so the *differences* between them are not confounded by run-to-run
variance. Three separate runs would have re-introduced exactly the variance the
study is trying to measure against.

A useful consequence: the ornith comparison inherits the anchor's method for
free, which is the thing that makes it citable at all. Ornith's own 70.6 is
model + learned scaffold; ours will be plain-harness, same as granite's.

**Discipline is the plan's existing long-run ritual, non-negotiable** — AC
power, lid open, `caffeinate -i -w <PID> &` immediately after launch,
`nohup` plus a persistent monitor on the PID, incremental per-case JSONL flush,
and `ollama list` before starting. A prior ~5 h run lost 2 h to lid-close sleep,
and harness background tasks have been killed mid-run. This is a
start-it-and-leave-the-machine-alone job, not something to launch mid-session.

The guardian judge measurement stays **separate and independent** — ~5 minutes
on the existing 30-row non-verifiable bank, no interaction with the cascade run,
and per the guardrail above it does not enter the October run regardless of
outcome.

## Consequences

**The scout result is itself a publishable part of the thesis answer, and it
arrived at zero inference cost.** The headline question is "does local-first
routing still pay as the frontier moves?" In this window the frontier moved
**up and out of** the size class that fits consumer hardware — it did not make
the local tier obsolete, it stopped serving it. A thesis about routing between a
free local tier and a paid frontier tier becomes *more* load-bearing, not less,
when the gap between tiers widens and no fittable model arrives to close it.
That is a claim about market structure, sourced from public release data, and it
belongs in the week-4 break-even framework as a stated trend rather than an
aside.

**A second, sharper consequence: the comparison we are about to run does not
exist anywhere else.** Because IBM switched benchmark suites between 4.1 and
4.2, and because EvalPlus's leaderboard has recorded no 2026 model at all, a
4.1-vs-4.2 HumanEval+ number on this harness would be the only
protocol-matched Python-synthesis comparison of the two in existence. This
harness is already vendor-validated — the local band 0.774–0.805 brackets IBM's
own 79.88 almost exactly — so that output is genuinely citable rather than
merely internal.

**Risk accepted.** granite 4.2 is not a foregone improvement. No published
number says 4.2 beats 4.1 on synthesis, the only positive signal is a
non-code LMArena delta (1318.4 vs 1292.1), and the local arm's own run-to-run
variance is ±3 points. A flat or negative local-side result is a legitimate
outcome and gets published as one — per the standing rule, an unfavourable
finding reported with its variance bound stated is the stronger artifact.

**Explicitly not re-opened by this decision:** the bandit research line stays
parked, and a genuinely stronger local arm — the one thing that could un-park
it by making the arms separable — demonstrably did not ship.
