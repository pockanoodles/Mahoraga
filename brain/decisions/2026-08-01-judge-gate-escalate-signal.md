# ADR 2026-08-01 — The live judge gate is an escalate-signal, not a reject; and it ships off by default

## Status
Accepted (2026-08-01).

## Context

Phases 5a–5d proved Thesis A as a *bench*. `orch bench live-route` ran the
local→judge→cloud cascade on fresh inference and got **1.000 pass@1 at $10.54/1k
vs always-cloud's $47.66** (findings Era 14). But the whole proof lived in
`routing/live_route.py`, reachable only from the CLI: **no `/api/task` caller
could invoke the thing we had proven.** The research arc had outrun the product
by four phases. Productizing `route_one` was on the "next" list since 5c.

Two facts about the judge forced the design:

1. **It is fallible in both directions, and which direction depends on the task
   shape.** On code it is conservative (Era 14: recall 6/6, but 4 of 50 correct
   answers needlessly escalated). On prose it is permissive (Era 15: ref-accept
   1.000, mutant-catch 0.733). The tool-augmented variant can false-reject
   outright when the solver is *systematically* wrong (Era 18: `pipes-tank`
   computed as −12, rejecting a correct reference).
2. **The bench framing hides a live hazard.** In a bench, a judge reject just
   routes a row to the cloud column. On the serving path, the executor's
   failure path ends in `TaskStatus.blocked` — so a naive wiring would let a
   judge false-reject turn a *correct answer the system already had* into a
   blocked task. That converts the verification tax from money into quality,
   which is precisely the trade 5c showed we don't have to make.

## Decision

**1. A judge verdict is a routing signal, never a task failure.** The gate is
wired into the executor's escalation path, not its failure path. Three
invariants enforce it, all pinned by tests:

- **A reject escalates.** It is recorded as `error_code="judge_rejected"` on an
  escalated attempt; `TASK_BLOCKED` is never emitted on its account.
- **The judge is not consulted when escalation is impossible.** With nowhere to
  escalate, a reject could do nothing but block a task the validator already
  passed. Checking `should_escalate` *before* paying for the judge call makes
  the harmful case unreachable and saves the latency.
- **The escalated-from answer is kept as a floor.** If the escalation target
  then fails outright — or every capable worker is exhausted — the executor
  serves the original answer instead of blocking (`_serve_judge_fallback`).

The worst case is therefore the *measured* one: Era 14's verification tax, paid
as a needless escalation, with no quality downside.

**2. The gate ships off by default** (`MAHORAGA_JUDGE_GATE=on` to enable) —
the opposite default from the execution gate, deliberately. The exec gate only
rewrites the bandit's reward; this one changes **which answer the caller gets**
and adds an LLM call to every task. Bank measurements (n=50 code, n=30 prose)
don't justify that blast radius on organic traffic until it has live hours.

**3. A failed judge *call* abstains** — a deliberate deviation from
`route_one`, which escalates on every non-True verdict. That is right for a
bench, where a broken judge should not let a run score as clean. On the serving
path it would turn a dead Ollama into a blanket reroute of all traffic. An
unparseable verdict from a judge that *did* reply still escalates, matching 5c.

**4. The reject is reported to the reward path.** The bandit attributes every
task to `selected_agent`, and a judge-rejected task usually still *completes*
via the escalation target — so scoring it as a success would reinforce the exact
output the gate rejected. A side-channel (`pop_judge_gate`, mirroring
`pop_task_metrics`) carries the verdict to `app.py`, which splits `success`
(what the caller got, unchanged) from `bandit_success` (what the arm earned).

## Consequences

- Enabling the gate on a **local-only roster** escalates local→local, not
  local→cloud. That is still useful — 5a showed qwen3.5 recovers 1 of granite's
  5 misses for free — but the 77.9% cost-cut headline is the *cloud*-escalation
  number and must not be quoted for a local-only configuration.
- Self-judging (judge arm == producing arm) is possible on a 2-arm roster and is
  weaker evidence than anything Era 14/15 measured. It logs a warning rather
  than refusing, since refusing would silently disable the gate.
- Splitting `success` from `bandit_success` means the decision log and
  `task_metrics.success` now record the judge's view, not the HTTP outcome, for
  judge-rejected tasks. Any analysis joining those columns to "did the caller
  get an answer" needs to read `status` instead.
- The tool-augmented judge (Era 18) is **not** wired in yet: its 5-sample solver
  consensus is too slow for an inline request path. The escalate-signal framing
  this ADR establishes is what makes it safe to add later — a solver bug becomes
  a needless escalation rather than a rejected correct answer.

## Revisit if
Live traffic shows the gate escalating a large fraction of tasks (the Era-14
conservative operating point generalizing badly to organic prompts), or the
verification tax in latency proves worse than the quality it buys. Both are
measurable once the gate has hours on real traffic — which is the next step.
