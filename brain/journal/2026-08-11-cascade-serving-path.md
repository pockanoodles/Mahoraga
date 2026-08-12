# 2026-08-11 — the cascade is on the serving path

## What prompted it

A product question, not a research one: what is Mahoraga *for*, to someone who
isn't us. The honest answer exposed an inversion. The part shipped as a product
— bandit routing between two local arms — is a diagnosed null (Era 20/23: never
beat round-robin). The part that works — the local→judge→cloud cascade, 0.921
pass@1 at 23.5% of always-cloud cost (Era 19) — was a *benchmark command*. The
README said so in its own limitations list.

Kaito's framing sharpened it: he is the user, and his Claude Code token burn is
the pain. "We should get the wire going."

## The finding that made it small

`route_one` was never the thing to port. It is bench-shaped: it takes the bank's
hidden tests, grades every step with `run_case`, and runs the cloud arm on
kept-local prompts to measure a baseline. None of that exists for organic
traffic.

**The gate was already running in production.** `app.py` has called
`reward_judge.judge_correctness` on every successful code-bucket task since
Era 23. The verdict was computed live, spent on the reward's correctness
coefficient — and then the rejected answer was served to the caller anyway. The
missing wire was ~30 lines: spend the existing verdict a second time.

## What shipped

`routing/cascade.py` (new) + a wire in `/api/task` + response surface + MCP tool
description. 23 tests (16 unit, 7 serving-path integration); suite 1605 green.

Two design constraints, both load-bearing:

1. **The escalation arm is outside the bandit's action space.** The obvious
   implementation — flip `claude-cli` to `enabled: true` — is wrong: an
   unexplored arm inflates its own UCB, so the policy would start spending real
   money to explore it. The arm is constructed in `cascade.py` from the same
   `build_cloud_worker` the bench uses (factored out of `live_route.load_arms`
   so the two can't drift), reachable *only* by a judge rejection.
2. **Escalation never re-attributes the outcome.** The bandit keeps observing
   the local arm's own output, and escalation spend goes to the cost ledger but
   NOT to `TaskOutcome.cost_usd`. Crediting the local arm with Sonnet's answer,
   or billing it for a call it didn't make, would re-break the reward signal
   Era 23 was spent fixing. Both are pinned by tests.

Guardrails: code-like buckets only (the prose judge's ref-accept is 1.000 — it
never rejects, so extending there buys nothing); a daily escalation cap
(default 25) with refund-on-failure so a flaky arm can't silently exhaust the
day; and every failure path degrades to serving the local answer, never raises.

## Live confirmation

Both branches fired through a real `orch serve`:

| prompt | judge | escalated | cost |
|---|---|---|---|
| `rotate(lst, k)` | accept (1.0) | no | $0 |
| `median_of_two_sorted(a, b)` | **reject (0.0)** | **→ claude-cli** | $0.041 |
| `next_permutation(nums)` | accept (1.0) | no | $0 |

And the attribution invariant held in the real decision log: the escalated row
recorded `selected_agent=granite`, `correctness=0.0`, `reward=0.462` against the
accepted rows' 0.71–0.81. The bandit learned granite failed; the caller got the
correct answer. That is the whole design in one row.

## Economics, honestly, for this user

Escalation goes to `claude-cli` on the Max subscription — the *same quota pool*
as the Claude Code session it is meant to relieve. It is not free. It wins
because a fresh CLI subprocess carries no conversation context (a real in-session
call runs ~35K tokens, cache-dominated), so an escalated task costs roughly an
order of magnitude less quota than doing it inline — and the ~70–78% that never
escalate cost nothing at all.

Measured escalation cost this session ran $0.041–$0.113/call, notably above the
bench's $0.036/task always-cloud rate. Small-n, but worth watching: the cap
exists because the judge's precision is not 1.0.

## Known gap

`ClaudeCliWorker` spawns a bare `claude`, resolved via PATH. Under the launchd
daemon (`orch service start`) PATH is minimal and excludes `~/.local/bin` — so
escalation would silently degrade to serving local answers. Documented in
`agents.yaml`; set an absolute `binary_path` before running the cascade as a
daemon. Untested under launchd.

## Part two — flipping the code-judge on, and what live traffic said

Kaito's call: turn `MAHORAGA_REWARD_JUDGE=code` on and let it run. Doing that
surfaced four things, three of them bugs, and reversed the decision.

### 1. The daemon had no configuration surface at all

The launchd plist had no `EnvironmentVariables` block. A launchd job inherits
nothing — so under `orch service start`, every MAHORAGA_* knob was pinned to its
code default with no way to set it, and PATH excluded `~/.local/bin`, making the
escalation arm's `claude` binary unresolvable (degrading silently to serving
local answers). Fixed: `install` now bakes in a PATH covering venv/`~/.local/bin`
/Homebrew plus any KEY=VALUE lines from `~/.mahoraga-v2/service.env`.

Two adjacent traps found while doing it: `launchctl load` **prints failure and
still exits 0**, so `install` had been reporting "Service installed and started."
while nothing ran; and the label was stuck in launchd's persistent *disabled*
database (a leftover from the Era-8 `stop` rework), which survives unload and
reboot and fails every load with an opaque errno 5. `_load_job` now clears the
disabled flag and verifies via `list` instead of trusting the exit code.

### 2. The code-judge abstained on essentially all organic traffic

`extract_entrypoint` required a literal `def name(` **in the prompt**. HumanEval
prompts *are* function stubs, so it always matched on the bank — and never
matched a real request ("Write a Python function chunk(lst, n) that ..."), which
names the function in prose. **The measured 0.688→0.784 recall gain did not
transfer to the serving path at all.** Fixed with a prose `name(...)` fallback,
kept strictly conditional: a prompt containing a stub is still resolved by the
stub alone, so bank behaviour — and the published number — is unchanged.

### 3. The code-judge is unusable interactively on this hardware

With the entrypoint fixed, the check engages — and costs **~265s per task**.
Four live samples (model answered in 4.6–6.8s each):

| task | judge outcome | wall |
|---|---|---|
| `chunk` | abstain (1 reference) | 295s |
| `dedupe` | abstain (no consensus) | 44s |
| `running_max` | never ran (see below) | 8s |
| `count_vowels` | fully engaged | 274s |

Cause is the 16 GB box: K sequential reference generations from a 9.7B judge,
plus arm/judge swap thrash (granite 5.3 GB + qwen3.5 6.6 GB do not coexist).
The reading judge costs +4–9s; the tool costs +265s. **Decision: the daemon
default reverts to `on`, and the tool becomes a per-task opt-in** — `thorough:
true` on `/api/task` and the MCP `run_task` tool. Latency, not dollars, is what
makes it a batch-only feature, so the caller must choose per task.

### 4. The wire had a hole exactly where it mattered most

Live traffic: granite emitted **non-compiling code on 2 of 6 tasks** (a walrus
in a comprehension iterable; a plain syntax error). The exec gate caught both
and flipped the task to failed — and because the reward judge only runs on
successes (`if success and ...`), no verdict was produced, `correctness` stayed
`None`, and `None` never escalates. **The answers most certain to be wrong were
the only class that never got a second opinion**, and the caller received broken
code with `status: failed`.

Fixed: `should_escalate` now takes `exec_failed`. The gate is the *harder*
signal — code that does not compile is wrong deterministically, with none of the
judge's false-positive risk. When escalation succeeds the response reports
success (the caller has a working answer) while the bandit still records the
local arm's failure; those stopped being the same question the moment a second
tier existed.

### The bill for that bug

Adding the exec-gate trigger made the **test suite spawn real `claude` CLI
calls** — any test whose fixture output failed to compile now escalated for
real. Caught by two cost-recording tests asserting ledger row counts, and by the
suite going 60s → 438s. `conftest.py` gains `_no_live_escalation`; the existing
`_no_live_reward_judge` was not enough precisely because the new trigger needs
no judge. Lesson: every new path to a paid arm needs its own test-suite guard —
guarding the judge did not guard the cascade.

## Final state

Daemon running with `MAHORAGA_REWARD_JUDGE=on`, `MAHORAGA_CASCADE=on`,
`ESCALATE_TO=claude-cli`, cap 25/day. Live latency 9–18s per code task including
escalation. Suite 1638 green.

## Part three — making it measurable for the only user who matters

Kaito's framing, and it settles the resume question: **he is the user.** N=1
dogfooding, tracked honestly. That reframes what "does it work" means — not
adoption, but "does running this daily actually save me what the benchmark
says."

Which surfaced a gap created by Part one: **escalations were not recorded
anywhere queryable.** The cost ledger got a row with no join back to the
decision, and the decision log had no idea a cascade happened. The number
Kaito wants was literally uncomputable. Fixed: three columns on `decisions`
(`escalated_to`, `escalation_cost`, `escalation_reason`), threaded through
`TaskOutcome` — the cascade runs before `router.observe`, so no second write
path was needed. `escalation_reason` keeps the two triggers distinguishable,
because "the judge caught it" and "it did not compile" are different claims and
collapsing them would hide which signal is carrying the cascade.

Then `routing/usage_report.py` + `orch metrics usage`.

**The design decision that makes it honest: the counterfactual is measured, not
tabled.** `bench report cost` prices local rows off a rate table and its own
docstring calls the result a floor (bare tokens miss the cache-creation that
dominates a real CLI call — ~27× under the measured rate). Here the baseline is
the escalation arm's *own* per-task cost on this machine in this window, taken
from escalations that actually happened. No price table, no assumed model, no
extrapolation from someone else's hardware. With no priced escalation in the
window it reports "unknown" rather than guessing.

Two bounds stated in the output itself, so the caveat travels with the number:
it is a **substitution** baseline (what these tasks would have cost on the
escalation arm), NOT interactive-session spend, which carries conversation
context and costs far more. And bench rows are excluded — one 200-task
forced-explore run would swamp a month of real work.

First real reading (2026-08-11, the day's traffic):

```
Tasks routed        15
  served locally    14  (93.3%)
  escalated          1  (6.7%)   judge rejected
Judge: accepted 10 · rejected 3 · abstained 2
Spend: escalations $0.0403 · measured rate $0.0403/task (n=1)
       avoided $0.5647 · cost reduction 93.3% vs all-cloud
```

n=1 on the rate, so the dollar figure is noise for now — but the plumbing is
closed end to end and every task from here accumulates into it. All-time
organic is 229 tasks, 217 of them abstained because they predate the judge
entirely; that number will stay ugly and honest until new traffic dilutes it.

**The resume consequence:** this supports a second, independent claim alongside
the HumanEval+ benchmark — "N tasks over M weeks of daily use, X% served locally
at zero marginal cost". Two claims from two sources (one reproducible by a
stranger, one lived) beats one claim polished harder. What it does NOT support
is a production/users claim, and the phrasing should never imply one.

## Open

- **Delegation is the real funnel** — the cascade only saves tokens on tasks
  that reach `run_task`. Tool description rewritten to make the value legible to
  the calling model; the prompting side is Kaito's open thread.
- Judge coverage on organic traffic is thinner than the bank implies: of ~9 live
  code tasks, 2 never reached the judge (exec-gate failures, now escalating
  instead) and the rest split accept/reject. Worth a real coverage measurement
  once traffic accumulates.
- The arm/judge swap thrash is a 16 GB artifact. On ≥32 GB both models stay
  resident and the tool judge's cost profile changes completely — revisit
  `thorough` as a default there.
