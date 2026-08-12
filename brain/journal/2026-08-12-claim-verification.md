# 2026-08-12 — binding every published number to its artifact

## What prompted it

A resume question, not a research one. The HumanEval+ result has to survive a
stranger checking it before mid-September. The gap was not the number — it was
that a reader had exactly two options: trust the README, or spend 3.5 hours
re-running the benchmark on a 16 GB Mac with Ollama and a `claude` CLI. Nothing
in between, and nothing preventing the prose from drifting away from the data.

## The finding that made it small

The numbers were already backed. Recomputing straight from
`experiments/live_route_humaneval_164.jsonl` reproduces every published figure
exactly — 0.9207 → 0.921, 0.9756 → 0.976, $8.47, $35.97, 76.5%, recall 0.6875 →
0.688. The claim was never soft; it was just **presented as prose rather than as
something runnable**. So the work was a verifier, not a re-run.

## What shipped

`routing/benchmark/verify.py` + `experiments/claims.json` + `orch bench verify`
+ `docs/RESULTS.md`, wired into CI. 15 tests; suite 1670 green.

Three design constraints, all load-bearing:

1. **The comparison is literal, at the published precision.** A claim records
   the number *as printed* plus its decimal place, and passes only when the
   recomputed metric rounds to exactly that. The property under test is "the
   digits in the README are the digits this file yields", not "these are
   roughly similar" — approximate verification of a published number is theatre.
   Rounding is half-up via `Decimal`, because Python's half-to-even makes
   `round(0.6875, 3) == 0.688` a coincidence rather than an intent.
2. **Claims live in data, not code.** `experiments/claims.json` binds each
   number to its artifact, its decimal place, and the files that quote it. So
   publishing a number is a reviewable manifest edit, and a failure names the
   prose that needs fixing.
3. **Missing or uncomputable FAILS; it never skips.** An absent artifact and a
   metric that cannot be derived are the exact conditions this exists to catch.
   A verifier that passes when it should not is worse than none, because it
   launders an unbacked claim as a checked one.

The split that clarified everything: `bench repro` answers *"is the data real on
my hardware"*; `bench verify` answers *"does the README still match the data"*.
Different questions, wildly different costs (3.5 h vs milliseconds). Only the
second is something a skeptical reader will actually run.

## Two things found by doing it

**A full replication existed only on this machine.**
`experiments/repro_2026-08-04.jsonl` — an independent 164-task run — was never
committed. It is the strongest single artifact here: **routed pass@1 came out
identical (0.921) across both runs**, on a different day, with a different judge
configuration, and with the local arm's own pass@1 moving 0.805 → 0.774 because
decoding is not seed-pinned. The routed result held anyway, which is the cascade
absorbing local variance by escalating what it catches. That converts the
headline from a single measurement into a replication.

It also carries an unflattering result worth publishing: the code judge bought
**+9.6 points of fail-recall for +$6.27/1k and zero pass@1**. The extra recall
was spent escalating answers that would have passed. Recorded in `RESULTS.md`
because it is the result, not because it is the one we wanted — and it retires
any argument for making `thorough` the default on this bank.

**`experiments/` was ignored as a directory while its contents were tracked.**
The published evidence survived only because someone once ran `git add -f`; the
next artifact would have silently failed to commit — as `repro_2026-08-04.jsonl`
in fact did. Switched to `experiments/*` with explicit negations (git will not
descend into an excluded directory, so a bare directory rule makes negations
inert). Publishing evidence is now a deliberate act that shows up in a diff,
which matches how `claims.json` already treats it.

## What this is for

It supports one honest sentence: *"94% of frontier pass@1 at 24% of frontier
cost on HumanEval+, replicated across two independent full-bank runs, verifiable
from the repo in one command without hardware."*

`RESULTS.md` states what it does **not** support, in the same page, deliberately:
nothing about production, users, uptime, or scale; nothing about the contextual
bandit improving routing (it never beat round-robin — it is architecture here,
not a result); no generalisation past Python function synthesis at n=164; and no
cost claim against interactive assistant use, since the baseline is substitution.
Volunteering the limits is what makes the rest credible.

## Open

- **Kill the Max-subscription asterisk.** Reproducing the cloud arm still needs
  the `claude` CLI on Max auth. An API-key escalation arm through the existing
  single-egress client makes the repro runnable by anyone with an API key.
- **The delegation funnel is still the binding constraint** for the second,
  lived claim. Every measurement starts at `run_task`; nothing measures what
  never arrived, so the denominator is invisible and "improve delegation" is
  currently unfalsifiable. The plan is a `PreToolUse` hook logging eligible
  inline actions alongside delegations, labelled offline by the router's own
  bucket classifier, surfaced as `orch metrics funnel`.
- The existing `~/.claude/scripts/mahoraga-routing.sh` hook advertises arms that
  do not exist (OpenCode, Goose, Gemini CLI are all disabled) and fires on
  `PostToolUse: Skill`, which is close to never during coding work. Same class
  of bug as the code-judge entrypoint: a mechanism that looked live and was not.
