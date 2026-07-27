# 2026-07-27 — Phase 5d: the tool-augmented judge

## Why
Era 17's overlap join proved the quantity/omission blind spot is structural
across local judge families — adding a second model (granite) caught 0 of 4
quantity mutants, so no local-judge *ensemble* closes it. One lever left: give
the judge a TOOL. This session built and validated the first tool: a
compute-check for computable-answer tasks.

## Architecture
`routing/tool_judge.py`. For a task whose answer is a computable quantity:
1. **Solver tool** — the judge model emits a self-contained Python program that
   prints `ANSWER: <value>`, run in the `execution_gate` subprocess sandbox. A
   manufactured hidden test for a task that has none.
2. **Compare** — the executed number is checked against the candidate's answer.
3. **Recall-only override** — may flip a base verdict accept→reject (catch a
   wrong number the plain judge missed) but NEVER reject→accept, and abstains on
   any solver failure. Protects Era-16's ref-accept = 1.0.

## The three iterations — the bottleneck walked down the chain
The hard part was never *computing* the answer; it was reliably comparing it to
the candidate's prose. Every LLM placed in that gap reintroduced judgment noise.

- **v1 — LLM "do they agree?" call.** Broke ref-accept (0.933): the comparator
  rejected an exact-correct `0.357` reference for "approximately correct but lacks
  precision." Pedantic about rounding.
- **v2 — LLM "extract the number" call.** Broke again (~0.92) on the *same* row:
  the extractor misread the reference's `0.357` as `0.3`.
- **v3 — deterministic, no LLM on the candidate side.** Solver hardened with
  **self-consistency** (≥2 of 5 runs must agree, else abstain — a single shot was
  ~1/3 reliable); candidate parsed deterministically and the computed answer
  checked against its **last-K** numbers (rtol 2%). Designed offline against the
  real bank texts first: an intermediate like "3/12" spuriously contains a final
  answer of `3`, so *all*-number membership under-catches — the conclusion's last
  few numbers don't. Cleanly separated all three computable rows with zero
  inference before a single live call.

## Result (v3, full 30-row bank, `--tool`, local/free)
- accuracy 0.867 → **0.900**; mutant-catch 0.733 → **0.800**; wrong-quantity
  1/5 → **2/5**; ref-accept **1.000** this run.
- Caught the computable errors the plain judge AND the granite ensemble missed:
  `two-red-marbles` (used 4/8 not 4/7) and `pipes-tank` (dropped the drain). The
  3 factual-lookup quantities correctly still escalate.

## The caveat and the reframe
The focused reason+instruct run exposed the real limiter: on one pass the solver
computed `pipes-tank` as `-12` (drain sign error) *consistently enough to pass
consensus*, false-rejecting the correct reference. Self-consistency stops
*random* flakiness, not a *systematically* wrong 8B solver.

**Reframe (5c economics):** ref-accept = 1.0 is a bank-discriminator metric. In
the live routing gate a tool false-reject is an **over-escalation to cloud** (which
returns the right answer) — 5c's accepted "verification tax = money, zero quality
loss," not a served wrong answer. As a gate the tool nets +2 real catches for a
rare needless escalation — a clear win. Solver correctness, not the compare
design, is the limiter.

## Shipped
`routing/tool_judge.py`; `judge_gate.run_text()` (factored, behavior-preserving);
`orch bench report judge-bank --tool` (opt-in, local-only, own cache slot). 16
tests; suite 1481 green. Findings: Era 18.

## Next
- Corroborate the solver before overriding (second framing / stronger model), or
  make disagreement an escalate-signal rather than a hard reject.
- A live non-verifiable cascade (5c-style) using the tool-judge as the gate.
- The omission blind spot (subtle-omission still 0.5) — a coverage/checklist tool.
- Productize `route_one` into `executor.py`.
