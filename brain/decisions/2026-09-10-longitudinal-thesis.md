# ADR 2026-09-10 — The thesis becomes longitudinal; the bandit research line is parked

## Status
Accepted (2026-09-10).

## Context

Mahoraga has a replicated, verifiable result: on HumanEval+ the local → judge →
cloud cascade reaches **0.921 pass@1 at $8.47/1k against $35.97/1k always-cloud
(76.5% cut, 94.4% of cloud quality)**, identical across two independent full-bank
runs, with every published figure recomputable from committed artifacts in ~1s
via `orch bench verify` running in CI. That part is done and defensible.

Three things force a direction decision now.

**1. The routing thesis has been tested three times and has not produced a
result.** Era 20 found LinUCB never beat round-robin. Era 23 fixed the reward
(judge verdict as correctness coefficient, r 0.12 → 0.98+) and the null
persisted — which *exonerated* the reward and left "the arms are not separable
as arms on these banks." A1 semantic routing was then named the remaining lever
four times (Era 23, Era 24, 08-05, 08-11) on the reasoning that the +11.6-pt
oracle gap lives per-prompt, where a 9-dim lexical context vector cannot see it.

This session established that **A1 is already built and already evaluated.**
`MEMORY_MODE_SEMANTIC` is the shipped default; the measured aggregate gain is
**+0.35 reward at ~0.25σ** on stationary workloads. The only clean win requires
per-bucket LinUCB and semantic together, on synthetic oracle-labelled
benchmarks. The lever that was being saved for last has largely been pulled.

**2. The dogfooding claim was never measurable.** The delegation funnel's first
reading is 0.0% over 44 sessions — but the MCP integration broke on 2026-08-11
21:33, eight hours before the hook began recording, and stayed broken all
month. The window is void as evidence. See
`brain/journal/2026-09-10-dormancy-break.md`.

**3. The strategic question is no longer "does routing beat static," it is
"does local-first still pay at all."** Frontier models get cheaper; local models
get better; the two move at different rates, and the cascade's entire value
depends on the gap between them staying exploitable. Nobody has measured that
gap moving. Mahoraga is unusually well placed to: it has a verified August
measurement and the machinery to re-run it identically.

Constraint on all of it: the resume-bearing window closes mid-October (BCF
online round). This is a flagship project for the next couple of months, framed
as a research artifact, by someone studying business rather than CS — so the
deliverable has to be legible to a non-specialist reader without being
softened.

## Decision

1. **The thesis becomes longitudinal.** The headline question is *"does
   local-first routing still pay as the frontier moves?"* — re-measure the
   August cascade result with current-generation local models and current
   frontier pricing, same bank, same method. The output is a **trend across two
   dated measurements**, not a point.

2. **Park the bandit research line.** It stays in the tree as architecture,
   honestly labelled — nothing is deleted or archived away. But no further
   effort goes into making the bandit beat a baseline during this window. Three
   attempts, two nulls and one within-noise directional result, is enough
   evidence that the separation is not there on these banks.

3. **Reposition the public face around the cascade.** The README currently opens
   with "learns which agent to use for each task" while `docs/RESULTS.md`
   states the bandit is not a result. The cascade carries the numbers; the
   README should lead with it. The bandit is described as architecture, in the
   same words RESULTS.md already uses.

4. **The deliverable is a decision framework, not just a benchmark.** A
   break-even model — at what task volume, task mix, and price point does the
   cascade pay for itself — layered on the measurements. This is the part that
   is distinctly *this author's* contribution rather than a reimplementation,
   and the part that survives interview scrutiny from a business audience.

5. **Restart the dogfooding clock at 2026-09-10** and treat the prior window as
   void. Any delegation claim measures from today forward, with a liveness
   check so a broken integration can never again be indistinguishable from
   non-use.

6. **Publish the answer even if it is unfavourable.** If the measurement shows
   the local-first advantage eroding, that is the result and it gets published
   in `docs/RESULTS.md` alongside the existing unflattering findings (the code
   judge's "+9.6 pts recall for +$6.27/1k and zero pass@1"). A measurement
   system rigorous enough to detect its own thesis weakening is the stronger
   artifact.

## Consequences

- **A1 is closed as an implementation item.** What remains is a narrow
  validation question — semantic retrieval against the real 2-arm roster on real
  traffic — which is downstream of dogfooding traffic existing, and is not
  scheduled for this window.
- **Q6 (episodic memory on/off at larger N), the distance-weighted episodic α
  spec, and the gamma full sweep grid are formally deprioritized**, not
  abandoned. All three are bandit-layer questions in a project whose measured
  value now sits at the cascade layer. The `findings.md` backlog should say so
  rather than listing them as in-progress.
- **The 16 GB machine is now a first-class constraint on the headline result,
  not a footnote.** The mid-2026 MoE trend (30B-A3B class, ~19 GB+) is locked
  out by memory, not compute. If the longitudinal answer is "local-first is
  losing ground," the honest qualifier is "on 16 GB" — and that itself is a
  finding about who the cascade serves.
- Cross-repo serving becomes the relevant scope for any dogfooding work: the
  funnel shows work spread across nine repos with Mahoraga at 2% of actions.
- Two measurement gaps are now blocking rather than nice-to-have: **delegation
  quality** (rate alone cannot distinguish success from silently discarded
  output) and an **integration liveness check**.

## Revisit if

- A local model lands that fits 16 GB and materially closes the gap to
  frontier — that would make the routing-layer question interesting again,
  because two genuinely comparable arms is the condition under which
  per-prompt separation could exist.
- Hardware changes (the deferred 32 GB / 3090 homelab). At ≥32 GB both judge
  and arm stay resident, `thorough` becomes viable as a default, and the
  deepseek-r1 arm unblocks — all three of which alter the cascade's economics
  enough to require re-measurement.
- Dogfooding traffic accumulates to the point where real-traffic routing data
  exists at a scale the banks never provided. The bandit question reopens on
  *that* distribution, which is heterogeneous in a way HumanEval+ is not.
