# 2026-09-11 — the frontier left the building, and the README stopped arguing with itself

Week 1 of the longitudinal plan. Two jobs: answer the blocking question that
gates weeks 2–3, and make the public face of the repo stop contradicting its own
results page.

## The blocking question, answered: no

"Has a better 16 GB-fittable local arm shipped since ~June 2026?" **No** — and
the shape of the no is more interesting than the answer. Three parallel research
passes, cross-validated, with every load-bearing quant size verified against
Ollama `/tags` or the Hugging Face blobs API rather than recalled. Full record:
findings Era 31. Decision: `brain/decisions/2026-09-11-october-run-shape.md`.

**The flagship lines consolidated upward, out of the class.** Qwen 3.6 and 3.8 ship no
member under 17 GB. `qwen3-coder`'s smallest build is 19 GB. Meta's Llama line
became "Muse" with a 17.31 GB floor. No Phi-5, no StarCoder3, no open Mistral
≤14B, every in-window Nvidia LM ≥30 B. And the MoE memory rejection from July
re-confirmed against the new generation: Laguna XS 2.1 (33B-A3B) is 20 GB,
`granite4.2:30b` is 18 GB, `qwen3.6:35b-a3b` is 23–24 GB. "3B active" still
markets like a small model and still costs like a 30B one to hold resident.

**The finding that outran the question: the Python-synthesis leaderboards are
frozen.** EvalPlus's own `results.json` holds 125 models whose newest entries
are DeepSeek-V3 and Qwen2.5-Coder-32B — zero 2026 models, no Granite at any
size. LiveCodeBench's official board and Aider polyglot contain nothing ≤14B.
IBM switched suites mid-line: granite-4.1-8b published HumanEval+ **79.88**;
granite-4.2-8b publishes **none**. So "model X beats granite on Python
synthesis" is, right now, *unfalsifiable from published data.* That is not a gap
in the research — it is a gap in the field, and it happens to be exactly the gap
a local harness fills.

**One release matters, and it is nearly free:** `granite4.2:8b`, 2026-08-25,
Apache 2.0 — a post-train of the *same* 4.1 base with a thinking toggle, at a
byte-identical footprint (same 40 layers, hidden 4096, vocab 100352; 5.35 GB
Q4_K_M). Pulled today; `ollama show` confirms 8.8B, Q4_K_M, thinking capable,
5.3 GB. No memory-budget change, no roster restructuring.

**So the contingency resolved better than it was written.** The plan's binary was
"new arm → local-side trend, no new arm → pricing-side trend." Reality is a
third branch: the October run gets **both** axes, because granite 4.2 is a real
local-side second point that happens to cost nothing to adopt. And because IBM
changed suites, a 4.1-vs-4.2 HumanEval+ number on this harness would be **the
only protocol-matched Python-synthesis comparison of the two in existence** —
citable rather than internal, since the harness is already vendor-validated (our
local band 0.774–0.805 brackets IBM's own 79.88).

**Two things to carry, both of which argue for the verifier layer.** Ornith-1.5-9B
and K2-Horizon-7B both claim SWE-bench Verified *exactly* 70.6, while
contamination-controlled SWE-rebench puts Qwen3.6-**27B** at 31.2 — a model
three times larger scoring less than half. And IFM disclosed that a K2-7B run
reached 82 by downloading reference solutions from GitHub, then self-corrected
Terminal-Bench 70.2 → 66.9. Good disclosure, and a ready citation for why this
repo grades against hidden tests rather than trusting reported numbers.

**One warning aimed at our own instrument: HumanEval+ is saturated.**
K2-Horizon-**0.9B** reports 79.9 on it — tying granite-4.1-8b at a tenth the
size while scoring ~36 points lower on LiveCodeBench v6. The only ≤14B models
anywhere that exceed 79.88 are *2024-era* Qwen2.5-Coder (7B at 84.1 for 4.7 GB).
The bank stays fixed — identical method across two dated measurements is the
entire value of a longitudinal study — but its ceiling is now a published
limitation instead of an unexamined assumption.

**And the scout result is itself part of the thesis answer.** The question is
"does local-first routing still pay as the frontier moves?" In this window the
frontier moved **up and out of** the class of models that fit consumer hardware.
It did not make the local tier obsolete; it stopped serving it. A thesis about
routing between a free local tier and a paid frontier tier gets *more*
load-bearing when the gap widens and nothing fittable arrives to close it. Zero
inference cost, straight from public release data, and it belongs in the week-4
break-even framework as a stated trend.

## The README stopped arguing with its own results page

The repo's opening line claimed Mahoraga "learns which agent to use for each
task" while `docs/RESULTS.md:113` said the bandit "does not [improve routing],
measurably." Self-contradiction on the public face, and the worst possible
own-goal for a project whose differentiator is rigour.

Fixed by promotion and demotion rather than by softening:

- **The lede now describes the cascade**, in the plain terms a non-specialist
  reads: run the free local model, have a free local judge check the answer, pay
  the frontier model only on a rejection. Followed by a "what this is, in 30
  seconds" section — problem, mechanism, measured result — because the primary
  readers are not CS specialists.
- **"How it works" is now explicitly two tiers of unequal weight.** The cascade
  is the tier that carries the numbers and comes first, with both escalation
  triggers and both test-pinned invariants. Agent selection follows under a
  heading that says what it does *not* claim, in the same words RESULTS.md uses.
- **The mermaid diagram now shows the escalation path** — it previously drew the
  bandit loop only, so the diagram omitted the mechanism every published figure
  comes from.
- **Limitations gained two bullets and lost nothing:** the bandit non-result
  stated outright, and 16 GB promoted to a first-class qualifier on every
  headline figure rather than a footnote about the dev machine.
- **Motivation now states what came back.** The research gap it was built to
  probe is kept, followed by the honest outcome: online selection *between arms*
  did not pay, and the tier below selection did. The interesting question turned
  out to be not *which model* but *whether this one's answer is good enough* —
  which is a better sentence than the one it replaced.

`orch bench verify` stays green: all 2 claims, 20 metrics, recomputed from
committed per-case results. The rewrite moved prose, not numbers.

**The bandit research line is now marked parked where a reader would look for
it** — `docs/specs/research-protocol.md` carries a status block on Q6 and a
"status of the bandit research line" section recording all three attempts, the
reward-fidelity result that exonerated the reward and relocated the finding
(*the two local arms are not separable as arms*), and what would un-park it.
Q6-at-larger-N, distance-weighted episodic α and the gamma sweep grid are marked
deprioritized with reasons, so they read as decisions rather than as a stalled
queue.

## The ledger caught up

`brain/state/findings.md` stopped at Era 24 with three sessions unrecorded.
Eras 25–30 back-filled, split by *finding* rather than by session, plus Era 31
for today's scout.

Era 25 is the one that had never been written: the **K=5 case-coverage sweep**
from 2026-08-05, whose inference was paid for but never read out. Recovered two
independent ways — the `bench_runs` ledger row (id=40) and a fresh offline
replay off the warm K=5 cache, 0.33s, zero inference — and they agree
digit-for-digit: fail-recall 22/32 → **27/32 (0.844)** at K=5 versus 25/32
(0.781) at K=3, wrong-answers-served 10 → 5, over-escalations 15 → 23, projected
routed 0.945 @ $11.41/1k. **Recorded as a replay projection and deliberately not
queued for live confirmation** — the K=5-vs-K=3 delta sits inside the variance
band that killed the retired 0.939 package, and the code judge already costs
~265s/task on this machine, which makes K-scaling moot on the serving path. The
operational gotcha is recorded too: a K≠3 sweep always needs its own cache file,
because the `::code` cache key ignores K.

## State

- **PR #41** — the resource meter (its own journal entry, `2026-09-11-resource-meter.md`).
- **PR #42** — this: README reposition, research-protocol parking, findings
  Eras 25–31, the October-run ADR.
- **PR #40** — yesterday's ADR, open and mergeable.
- **PR #38** — a stray one-word web edit (`Mahoraga` → `My Mahoraga`) in the
  README lede, open since Aug 6. The line it edits no longer exists after this
  reposition. Left open rather than closed unilaterally.
- Backend up (PID 1448, ~10.8h uptime, 2 arms, 1321 decisions); `granite4.2:8b`
  resident.

## Open

- **`current_state.md` has no 2026-09-11 header yet.** It is modified on PR #40's
  branch, and adding a section here would have collided. Once #40 and #41 land,
  it needs one section covering the scout verdict and the two-axis October run.
- **The dogfooding clock is running but the funnel still cannot distinguish
  "tool available and not used" from "tool unreachable."** That liveness check is
  the one week-1 item still outstanding, and it is the thing that made the Aug 12
  → Sep 10 reading void. It wants a small change to `funnel_report.py`, which PR
  #41 also touches — so it is sequenced after #41 lands rather than stacked on it
  (the phantom-merge lesson).
- **`qwen3:14b` is still on disk** (9.3 GB, dropped as an arm 2026-07-26).
  Deliberately not deleted today; there is 178 GB free, so reclaiming it is not
  urgent, and it is the only ≥14B local model on the machine if a comparison
  point is ever wanted.
