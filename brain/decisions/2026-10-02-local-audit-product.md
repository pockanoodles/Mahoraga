# ADR 2026-10-02 — Mahoraga becomes a local-vs-cloud audit of your own code

## Status
Accepted (2026-10-02).

**Supersedes, in part:** `2026-09-10-longitudinal-thesis.md` — its week 4–5
schedule and its "out of scope: new adapters, packaging" list. Its thesis
question survives as the spine of the report (see Decision 2).
**Builds on:** `2026-09-11-october-run-shape.md` (the two-axis run still
happens), the agent-edit probe (`brain/journal/2026-09-29-agent-edit-probe.md`,
on `feat/agent-edit-probe`), and `orch agentbench` (on `feat/agentbench`).

## Context

On 2026-09-28 Kaito called time on Mahoraga as a research artifact: it has to
become something people use. Three things learned since then fix what that
product is.

**1. The overnight runner is plumbing, not the product.** The agent-edit probe
put aider + qwen3.5 on 18 real Mahoraga commits: 5/18 blind, 5/18 with
feedback. On ops it was 1/9, and half the attempts hit the 20-minute timeout.
granite4.1-8b managed 0/18 and 1/18. Median attempts take 8–13 minutes and the
real speed is ~14.5 tok/s idle, not ~30. A night yields ~30–35 attempts and a
handful of test-passing diffs, while a frontier subscription does that in
minutes. "A free overnight coding worker" loses to the frontier on raw output.
"A local model server for a 16 GB Mac" is just Ollama plus cron.

**2. What nobody else produces is the answer to the user's actual question.**
That question, in Kaito's framing: *"I use cloud agents on my repo. Am I
wasting money? Should I switch to local? Pros, cons, statistics, tested on my
own code."* Leaderboards don't know your repo, and vendors won't tell you not
to buy their product. Mahoraga already has the pieces that answer it:
agentbench measures local agents on a real repo, the break-even framework
(09-10 decision 4) says when local pays, and the hardware tier map (Era 32)
says what changes at 24 GB.

**3. The strongest evidence is the user's real cloud history, not their
commits.** Claude Code writes one JSONL transcript per session. Each assistant
message carries the model, full token usage (input, output, cache read/write),
`cwd`, `gitBranch` and timestamps. There are also `file-history-snapshot`
entries (verified on this machine 2026-10-02). So the tasks a user already gave
a cloud agent, and what each one cost, are on disk. Replaying them against
local turns a benchmark into an audit. The cloud side of the comparison costs
nothing, because it already happened.

Kaito's bar for the project: people should look at it and think *this person
really did the work*, and anyone who understands it should be able to use it.
Users are the upside. Rigour is the part we control.

## Decision

1. **The product question:** *"Is local coding AI worth it for my code, on my
   hardware, and what would change that?"* Mahoraga measures, gives a verdict,
   and then routes. Routing comes last.

2. **Two layers, one question.**
   - **The report:** *"Is local coding AI worth it on a 16 GB Mac? —
     October 2026."* A public, dated, verifiable write-up. It folds in the
     longitudinal thesis (the two-axis HumanEval+ run from 09-11), the
     agent-edit results, the tier map, the break-even verdict, and the
     corrections and voided runs. The receipts are the credibility.
   - **The tool:** *"Don't trust my numbers — run it on your repo tonight."*
     The report persuades and the tool lets readers verify it. Each makes the
     other credible.

3. **Architecture: one pipeline with interchangeable stages.**
   `TaskSource → Runner → Grader → Ledger → Verdict`. Commit mining is one
   source and transcripts are another. Runners are agent adapters (aider,
   opencode, later goose). The Verdict reads only from the Ledger, so the
   published report and a user's verdict come out of the same code and cannot
   drift apart. This costs more than bolting a `replay` command onto
   agentbench, and we're taking that cost deliberately.

4. **Sources, in order.** v1: commits (exists). v2: **Claude Code and Codex**
   transcripts, then the git-attribution fallback (`Co-Authored-By`, `aider:`,
   `codex/` branches; the only route to cloud background agents). Later:
   Aider history. Cursor and Copilot only if real users ask; their storage is
   undocumented SQLite/JSON with no cost data. Every parser checks the tool's
   version, keeps fixture tests per supported version, and **fails loudly** on
   unknown formats.

5. **A grading ladder, reported by tier and never blended.**
   - *Test-verified:* the repo's tests pass with local's diff and fail without
     it.
   - *Reference-matched:* no test covers the task, but the user kept the cloud
     change, so local's diff is compared against that.
   - *Judge-graded:* opt-in only, see 6.
   - *Unverifiable:* reported as a share of usage ("X% of your usage can't be
     verified") and never given a score.

6. **The frontier judge: same vendor, off by default, and only counted after
   calibration.** It compares local's answer against the cloud answer the user
   accepted. It first lists the prompt's requirements, then checks each one in
   both answers (this targets the omission blind spot from Eras 15–17).
   Answers are labelled A/B blind, and each pair is judged twice with the order
   swapped; if the two verdicts disagree, the result is *uncertain*. Its
   agreement with ~40 of Kaito's hand labels (Cohen's κ) is published with the
   tier. **The tier enters the verdict only if κ ≥ 0.6**; below that it is
   shown as advisory only. Same vendor means no new party sees the data, since
   the user already sent it there. The cost is self-preference bias, which the
   calibration set measures; the bias is not assumed away. Cross-vendor is
   available as an option. All judge calls go through one audited client, and
   the audit's own cost appears in the verdict.

7. **The verdict engine is the break-even framework, written as code.**
   - **Output:** stay / split / switch, the reason, and a sensitivity line
     ("this flips to *split* at 25% edit-resolve or with a 24 GB GPU").
   - **Pricing:** both API and subscription plans. For subscriptions the
     questions are "could you drop a tier?" and "would this stop the rate
     limits?"
   - **Time cost:** developer time is priced, not just tokens.
   - **Hardware what-ifs:** come from the tier map and are always labelled as
     *projections*.
   - **Expected default:** for most users the answer will be "split", not
     "switch". The engine has to be comfortable saying "you're not wasting
     money."

8. **The statistics are part of the product.**
   - Wilson intervals on every rate.
   - Repeats, since qwen went pass/pass/no-edit/wrong-fix on identical inputs.
   - Sampling stratified by task type.
   - A planner that says how many nights an interval of a given width will
     take.
   - A manifest on every run: model digests, Ollama and agent versions, repo
     commit, prompts, power state.
   - Every published number goes through `claims.json` and `bench verify`.

9. **Privacy: local-only by default.** Opt-in per repo. A `DATAFLOW.md` for
   non-engineers. The opt-in judge is the only thing that leaves the machine.

10. **The existing routing work becomes the last stage, acting on the
    verdict.** The cascade, the bandit and the MCP serving path route the slice
    the verdict says local can take. The bandit research line **stays parked**
    (09-10) and nothing is deleted.

## Consequences

- **The 09-11 two-axis HumanEval+ run still happens.** It's a 3.5-hour
  overnight job that's already scripted, and it is the local-side point of the
  report's longitudinal section. **But ornith-1.5:9b's hardware question moves
  to agentbench as well**: its contested 70.6 is a SWE-bench *editing* claim,
  and HumanEval+ synthesis can't test it. The agent-edit harness can.
- **The repos tested have to go beyond Kaito's own.** "Mahoraga and ops" reads
  as anecdote. Two or three recognisable open-source Python repos with real
  test suites turn it into research, and they also show the tool working on
  other people's code.
- **Test dependencies have to be installed before the sandbox closes.** Agents
  run with network limited to localhost, so a repo's test environment is
  prepared as a separate step outside the sandbox.
- **Claude Code transcripts were being deleted after 30 days.**
  `cleanupPeriodDays` was raised to 3650 on 2026-10-02 so Kaito's own history
  (user zero) stops expiring. The trade-off is that plaintext prompts and code
  now persist for ten years, Time Machine included.
- **The landing debt gets paid first.** PRs #40–#44 have been open since
  2026-09-11, `feat/agent-edit-probe` and `feat/agentbench` are unpushed, and
  no PR has merged since #39. A product built on unmerged branches is not a
  product.
- **Explicitly not adopted:**
  - **Fine-tuning a local model on accepted cloud diffs** — parked until the
    vendor terms on using outputs are read.
  - **Sharing an anonymised ledger across users** — the Ledger format is
    designed for it, but the sharing isn't built.
  - **Mining Kaito's corrections into rules, and transcript-derived career
    records** — these belong to ops and the Brain vault, not Mahoraga.
