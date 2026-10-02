# 2026-10-02 — the overnight server was the plumbing; the audit is the product

A conversation session, no code. It started with Kaito saying he was "still a
bit lost for maho," and asking whether Mahoraga becomes a way to use the 16 GB
laptop as an overnight experimental server for local models.

## The pushback

Yes as the way it runs, no as the identity. The argument came from Mahoraga's
own numbers:
- **The output is small.** About 30–35 attempts a night at 8–13 minutes each,
  with resolve rates of 1/9 (ops) to 5/18 (Mahoraga). That's a handful of
  test-passing diffs per night, which a frontier subscription produces in
  minutes.
- **It's a commodity.** "An overnight local model server" is Ollama plus cron.
- **The laptop is a poor server.** The charger, lid, idle-gate and sandbox
  workarounds are the cost of making a laptop act like one.

The laptop is the test rig. The homelab 3090 is the server, if Mahoraga's own
data says it's worth buying.

## The reframe

Kaito's bar: something people look at and think "this person really did the
research", which anyone who understands it can also use. Users are the upside;
rigour is what we control. That points at a question nobody else answers:
**"Is local coding AI worth it for my code, on my hardware, and what would
change that?"** agentbench, the break-even framework and the tier map were
three halves of it built separately.

Kaito then put it as a user story: *"I use cloud agents. Am I wasting money?
Should I switch? Pros, cons, statistics, tested on my own code."* Three places
where that ambition outran what's built:
- Mahoraga can't see the cloud bill.
- Most users pay a flat subscription, so per-token savings don't apply.
- The honest answer will usually be "split", not "switch".

## The idea that makes it distinctive

**Test against the user's real cloud history.** Checked on this machine:
Claude Code transcripts carry, per assistant message, the model, full token
usage, `cwd`, `gitBranch` and timestamps, plus `file-history-snapshot` entries.
The tasks a user already gave a cloud agent become the test set. The cloud side
of the comparison costs nothing because it already happened; only local needs
compute. That turns a benchmark into an audit.

The hard part is grading, so it became a ladder reported by tier and never
blended: test-verified, reference-matched (the user kept the cloud change),
judge-graded (opt-in), and unverifiable (reported as a share, not scored).

## Decisions (Kaito's calls)

1. **Scope:** v1 by Oct 15 = report + pipeline refactor, commit source only.
   Transcripts are v2.
2. **v2 sources:** Claude Code and Codex.
3. **Judge:** same vendor, off by default, counts only if calibration clears.
   The trade-off was privacy against bias. Same vendor means no new party sees
   the data, since the user already sent it there, but the judge may prefer its
   own style. The calibration set measures that bias instead of assuming it
   away.
4. **Retention:** `cleanupPeriodDays` raised to 3650. The oldest Mahoraga
   transcript was from 09-10; history had been expiring on a rolling basis.

## What surfaced while rewriting the plans

- **No PR has merged since #39.** #40–#44 have been open three weeks, and the
  agent-edit and agentbench branches were never pushed. That became Phase 0,
  because a product on unmerged branches isn't one.
- **The 09-11 consolidated HumanEval+ run never ran.** The 09-28 pivot took the
  nights. It's first in the new schedule, since it's scripted and ready.
- **ornith's hardware question was being asked of the wrong instrument.** Its
  contested 70.6 is a SWE-bench *editing* claim; HumanEval+ synthesis can't
  test it, but agentbench can. It now runs in both.
- **`docs/plans/` looked un-ignored, but the fix was sitting unmerged in
  #40.** It's a small example of what the merge backlog was costing.

## Phase 0, same day

Landed the backlog:
- **Merged:** #40, #42, #41, #43.
- **#44:** had never run CI, because it was stacked on a feature branch.
  Retargeted to main and synced with it.
- **#45:** the agent-edit probe and agentbench, pushed. 144 of its 164 files
  are per-attempt evidence, kept on purpose so the numbers can be recomputed.
- **Closed:** #38.

## Later uses for the transcript archive (noted, not planned)

- **Owned by Mahoraga:**
  - a frozen, dated benchmark of real tasks, replayed against every new local
    model
  - the 3090 purchase case
  - real spend tracking
- **Parked pending vendor terms:** fine-tuning on accepted diffs.
- **Belongs to ops / Brain:** mining Kaito's own corrections into rules, and
  transcript-derived career records.

ADR: `brain/decisions/2026-10-02-local-audit-product.md`. Plan (local):
`docs/plans/2026-10-local-audit.md`.

## The build, same day

Once the plan was rewritten, Kaito said to start building. These went in, in
order, each with tests (suite 1828 green at #47):

1. **Grading became a stage of its own,** with a tier on every attempt, so the
   v2 reference grader and the judge plug in without touching the runner. The
   report refuses to add tiers together.
2. **Every run writes a manifest:** model digests, agent and Ollama versions,
   harness commit, task and prompt hashes, machine and power. Without one, a
   published rate can't be tied to the setup that produced it.
3. **Intervals count tasks, not attempts.** This decision changed the
   measurement plan. Repeats can't narrow a rate's interval, so the plan
   shifted toward more repositories over more repeats.
4. **The verdict engine,** with prices only as dated, sourced observations.
   The pricing page didn't print Max 20x's price and chatgpt.com refused the
   fetch, so those plans require `--plan-price` rather than a remembered number.
5. **A screening agent mined four outside repos in parallel.** Three yielded
   73 tasks. The fourth exposed tqdm's test naming, and two exposed a
   src-layout bug that the gates had caught by dropping everything.
6. **`doctor`, the `MAHORAGA_AGENTS_YAML` override** (so the night run doesn't
   edit a tracked file), and tonight's script. The script deliberately strips
   the daemon's cascade and escalation env, so the October run matches
   August's method.

**The finding that most changes the story:** for an API user the dominant
cost of local is the developer's minutes on a failed attempt. That makes the
realistic customer a team paying per token for long tasks, not a subscriber.
