# 2026-08-01 — The judge gate becomes a serving-path feature

## Why

Coming back after ~5 days off, the state of the repo was: Thesis A proven four
different ways (5a ceiling → 5b verification tax → 5c live cascade → 5d
no-oracle bank, plus Eras 16–18 on the tool judge), and **none of it reachable
by a user.** The cascade lived in `routing/live_route.py`, called only by
`orch bench live-route`. `/api/task` — the actual serving path, the thing the
daemon exposes to Claude Code and Cursor — still ran a worker, checked a cheap
validator, and served whatever came out.

That gap had been on the "next" list since 5c and kept losing to more research.
The honest read on picking it up: the research arc had outrun the product by
four phases, and the ADR (`2026-07-26-thesis-a-research-as-onramp`) says routing
*is* the product and the lab is the on-ramp. Right now only the lab existed.

Also cleared first: **PR #27 had been sitting green and unmerged for 5 days.**
CI passing, `mergeable_state: clean`, 869 lines of Era 16–18 work including
`tool_judge.py` and three journals. Until it merged, `main`'s `current_state.md`
claimed Era 15 was the frontier — the written record was three eras behind the
code. Merged it as step one.

## What shipped

`routing/judge_escalation.py` + wiring in `service/executor.py`. After a
worker's output clears the cheap validator, a free local judge re-reads
(prompt, output) and votes correct/incorrect; an "incorrect" vote routes the
task to the next capable worker. Rubric follows the bucket — `JUDGE_RUBRIC` for
code/test/refactor/debug/security, `GENERAL_RUBRIC` (Era 15) for everything
else.

The design work was almost entirely about **making a fallible judge safe on a
live path**, which the bench framing had let us ignore. Three invariants, all
test-pinned — full reasoning in
`brain/decisions/2026-08-01-judge-gate-escalate-signal.md`:

1. **A reject escalates, never fails.** Wired into the executor's escalation
   path, not its failure path.
2. **The judge isn't consulted when escalation is impossible.** With nowhere to
   go, a reject could only block a task the validator already passed. Checking
   `should_escalate` before paying for the call makes the harmful case
   unreachable *and* saves the latency.
3. **The escalated-from answer is a floor.** Escalation target dies → serve the
   original rather than block.

Off by default (`MAHORAGA_JUDGE_GATE=on`), opposite the exec gate's default,
because this one changes *which answer the caller gets* rather than only the
bandit's reward.

## Two things the wiring surfaced that the bench never could

**(1) A blocked-task path I'd missed.** The first version of invariant 3 only
covered "escalation target returned a failure event". A test caught the other
exit: after the target fails, the executor loops once more, `assign_worker`
raises `NoCapableWorker`, and it early-returns to `blocked` from the *assign*
block — bypassing the fallback entirely. Factored the fallback into
`_serve_judge_fallback` and covered both exits. Worth noting the test found
this, not review.

**(2) Enabling the gate would have quietly corrupted the bandit.** The bandit
attributes every task to `selected_agent`, and a judge-rejected task usually
still *completes* — the escalation target answers. So `success=True` would be
credited to the arm whose answer the judge just rejected: the gate would have
positively reinforced exactly the output it was built to catch. Fixed with a
side-channel (`pop_judge_gate`, mirroring the existing `pop_task_metrics`
pattern) and a split in `app.py` between `success` (what the caller got —
unchanged, still honest) and `bandit_success` (what the arm earned).

This one is the more interesting finding. It's a hazard that **only exists in
the serving path** — in a bench, the judge's verdict *is* the routing decision
and there's no learner downstream to mislead. Four phases of bench work couldn't
have surfaced it.

## Part 2 — instrumentation, so the gate is measurable on organic traffic

Shipping the gate without instrumentation would have left the interesting
question unanswerable, so the same session added the read-back path:

- `judge_gate_events` table on the decision log (+ `log_judge_gate`), one row per
  consultation — **accept or reject**. Rejects alone give no escalation *rate*;
  the denominator needs the accepts.
- `routing/judge_live_report.py` + `orch bench report judge-live`.

**What it reports, and what it refuses to report.** Escalation rate (overall,
per bucket, per judged agent), the verdict mix, judge latency (mean/p90 — on a
serving path the tax is *time*, paid whether or not the gate fires), and the
fallback rate: how often an escalation went nowhere, i.e. how often invariant 3
saved a task a hard-reject design would have blocked.

It deliberately does **not** report accuracy or recall. Era 14's "4 of 50
needless" came from grading against hidden tests; organic traffic has no oracle,
which is the entire reason the banks exist. The report says so in its own output
and in `as_dict`'s `caveat` field, and a test asserts `accuracy` never appears in
the overall cell — because the failure mode here is a future me reading a
20%-matches-20% line as "the gate is correct." **Divergence is the finding;
agreement is weak confirmation.**

Two aggregation decisions worth writing down, both test-pinned:

- **An abstain is not a "correct" vote.** A judge whose call errored keeps the
  local answer, same *action* as a "correct" verdict — but collapsing them would
  hide an Ollama outage as a run of clean accepts. Four verdict classes
  (correct / incorrect / unparseable / abstained), kept apart.
- **A NULL latency is skipped, not zeroed.** Otherwise abstains (which cost 0 ms
  by construction) would drag the mean down and understate the real tax.

## Numbers

Suite 1481 → **1550 green** (`pytest -m "not slow"`); 69 new tests across the
gate (48) and the report (21). No live inference this session — the gate's
behavior is measured by 5c/5d, and what's new is plumbing, safety properties, and
aggregation, all of which unit tests pin precisely. The report was smoke-run
against a seeded DB to confirm the rendering, the `--json` path, and the
no-data path.

## Caveats & next

- **The 77.9% headline does not apply to the current roster.** With the live
  roster local-only, escalation goes local→local, not local→cloud. Still worth
  something (5a: qwen3.5 recovers 1 of granite's 5 misses free) but it is not
  the cloud number and must not be quoted as one.
- **Not yet run on organic traffic.** Everything here is bank-measured. The gate
  and its report are now both in place, so the open question is finally
  answerable — does Era 14's 20% escalation rate hold on real prompts? — but it
  needs hours with `MAHORAGA_JUDGE_GATE=on` before `orch bench report judge-live`
  says anything. **That run is the next session's work**, and it is the first
  thing about this gate a bench genuinely cannot tell us.
- **Era 15 predicts a specific per-bucket shape**: the same judge was permissive
  on prose and conservative on code, so code buckets should escalate more than
  general ones. The report splits per bucket precisely so that prediction is
  checkable rather than assumed.
- **Tool judge deliberately not wired in.** 5-sample solver consensus is too
  slow inline. The escalate-signal framing is what makes adding it safe later —
  a solver bug becomes a needless escalation, not a rejected correct answer
  (Era 18's `pipes-tank` → −12 failure).
- Self-judging is reachable on a 2-arm roster and is weaker than anything
  Era 14/15 measured. Warns rather than refusing.
