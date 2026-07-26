# ADR 2026-07-26 — Thesis A (local-first routing) is the north star; the research platform is an on-ramp, not a second product

## Status
Accepted (2026-07-26).

## Context

The local-model landscape moved (new fine-tunes tuned for 16 GB machines), which
raised a product question: should Mahoraga become a **local-model research
platform** where users audition/benchmark their own model teams and get data —
the way we use it internally?

That vision clashes with Mahoraga's existing thesis. There are two, and they
take **opposite stances on cloud**:

- **Thesis A — routing:** you need local *and* cloud; an online bandit routes
  per task, escalates the fraction local can't solve, and the ledger proves it's
  cheaper at ~cloud quality. Cloud is a first-class citizen — it's what makes the
  cost story provable and interesting.
- **Thesis B — local platform:** find the best local team, escape cloud. Cloud
  is the enemy you route around.

Two risks made the choice matter: (1) the routing thesis is **unproven** —
Phase 4 was force-explore, measuring per-arm quality/cost, not that routing beats
always-cloud/always-local; a pivot to the platform risks never proving the moat;
(2) a **founder trap** — the research platform is compelling because it's how
*we* (Persona B, power users) use the tool, but Mahoraga's stated user is
Persona A ("can't/won't orchestrate"), who wants a working team handed to them,
not a benchmark to run.

## Decision

1. **Keep Thesis A as the north star.** Local-first routing with cloud
   escalation. Cloud stays in the roster — it is the denominator that makes the
   economics claim provable. (Kaito: "cloud is always going to beat local —
   quality, speed, we pay for it, it better.")
2. **The research surface is the on-ramp, not a product:** the funnel is
   **audition → roster → route**. Auditioning models on a task bank produces a
   roster and *seeds the bandit's priors* (replacing hand-tuned `capabilities:`
   in `agents.yaml` with measured ones); routing then learns live within that
   roster. Research feeds routing instead of competing with it.
3. **Three tiers, one product:** Persona A gets **curated presets** (the output
   of audition runs we do) and never benchmarks; Persona A+ gets an
   "audition on my tasks" wizard; Persona B gets the raw **lab mode** (banks,
   cost reports, force-explore) gated behind an advanced flag — same machinery,
   not the front door.
4. **Prove routing before the lab becomes any kind of headline.** Run the
   routed-vs-baseline head-to-head first. (Started immediately — see 5a below.)

## Consequences

- Cloud (`claude-cli`) is not disposable; it stays a real arm for the economics
  story even while the default serving roster is local-only.
- The Phase-4 machinery (verifiable bank, cost ledger, force-explore, pass@1)
  is re-cast as the cold-start for routing, not throwaway analysis.
- Immediate work: **5a** (`orch bench report route-sim`) computed the routing
  ceiling exactly from the run-19 matrix — 87–89% cost cut at 100% quality on
  verifiable tasks (see `brain/journal/2026-07-26-phase5a-route-sim.md`). **5b**
  measures the verification tax of a real gate; **5c** is the live routed run.
- We consciously avoid building "promptfoo-with-a-bandit": enumeration of models
  is not the product; **curation** ("here are 3 that fit your RAM and won on your
  tasks") is.

## Revisit if
Persona A demand for self-service auditioning turns out to dwarf demand for
hands-off routing — then the tier weighting shifts, though Thesis A (cloud in the
roster) still holds.
