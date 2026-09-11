# 2026-08-12 — measuring the top of the funnel

## What prompted it

The cascade works, the benchmark is verifiable, and the usage report says 93% of
tasks that reached Mahoraga were served locally. None of that answers the
question that bounds all of it: **how much delegable work reaches Mahoraga at
all?** The cascade saves nothing on a task that is never delegated.

Every measurement in the repo starts at `run_task`. So the denominator was
invisible — 15 delegated tasks could be 15 of 15 or 15 of 200, and nothing
distinguished them. "Improve delegation" was unfalsifiable, which is the Era-20
bandit trap one level up: an intervention with no measurable target.

## The design decision that made it honest

**The rate is reported as a lower bound, and says so in its own output.**

A hook sees a file being written. It cannot see whether the model needed three
turns of conversation to know what to write. So the candidate rule counts
anything whose *shape* fits — new code file, 5–300 lines — which over-counts the
denominator and pushes the measured rate down.

That direction is deliberate. This number's job is to argue the tool is
underused; an over-stated rate would quietly retire a problem that is still
there. Erring toward "you delegate less than this" fails safe.

Exclusions are reported *with their reason* rather than dropped, so the
definition of "delegable" is arguable instead of asserted:

| excluded | why |
|---|---|
| `edit-in-place` | surgical change defined by surrounding code; the arms get no repo |
| `non-code-file` | docs, config, data |
| `below-round-trip-threshold` | <5 lines — faster to write than to round-trip |
| `oversized-for-local-arm` | >300 lines — beyond a context-free 8B's one-shot |

## Two things I got wrong in the plan, and corrected while building

**1. I had proposed reusing the router's bucket classifier to label
eligibility. It does not fit.** `_classify_bucket` takes a *prompt*; a
PostToolUse hook has the *output*. Running a keyword classifier over generated
code matches "code" trivially and discriminates nothing. Forcing it would have
been a fidelity bug dressed as code reuse — the funnel's labels would look
principled while measuring noise. Replaced with action shape (tool, extension,
size), which is what the hook can actually observe.

**2. I had proposed taking the numerator from the decisions DB.** Wrong
population: the DB records all organic traffic, the hook only sees Claude Code
sessions. The ratio's two halves would describe different worlds. Both halves
now come from the single hook log.

## The cost split

The recorder runs on every `Write`. `orch --help` alone costs ~500 ms to import
Typer and the command tree; the stdlib-only recorder is ~20 ms. So: recorder in
`scripts/claude_code_funnel_hook.py` (no orchestrator imports, pinned by a
test), analysis in `routing/funnel_report.py` where import cost is free.

It exits 0 on *any* input — malformed payload, missing directory, unreadable
disk. A measurement tool that can interrupt the work it measures gets
uninstalled within a day, and then it measures nothing. `PostToolUse` rather
than `PreToolUse` so a rejected edit cannot inflate the denominator. It logs no
file contents, only derived shape.

## The intervention side, fixed at the same time

`~/.claude/scripts/mahoraga-routing.sh` was advertising **OpenCode, Goose, and
Gemini CLI** — all `enabled: false`. It was promising capabilities Mahoraga
cannot deliver, which teaches the calling model the tool is unreliable the first
time it tries one. Same class of bug as the code-judge entrypoint: a mechanism
that looked live and was not.

Rewritten around **disqualifiers rather than categories.** The old version asked
"is this boilerplate / structured code with a clear spec?" — a judgment call,
and the default answer to a judgment call mid-task is "I'll just do it."
Inverting it makes delegation the default for code-shaped work and puts the
burden on *not* delegating. Whether that actually moves the rate is now a
measurable question, which is the entire point of having built the meter first.

## State

Hook installed in `~/.claude/settings.json` (merged, backup written alongside);
it captured a real `Edit` on the first try. `orch metrics funnel` reads
`~/.mahoraga-v2/funnel.jsonl`. Suite 1706 green.

## Open

- **The rate needs weeks, not hours.** The meter exists; the reading does not.
  Nothing about the September claim can be written from one day of data.
- The 5/300-line candidate bounds are asserted from what an 8B can plausibly
  one-shot, not measured. Once there is traffic, check whether delegated tasks
  near the bounds actually succeed and move them if not.
- Delegation quality is still unmeasured: the funnel counts whether work was
  delegated, not whether the delegated result was accepted or thrown away. A
  high rate with silently discarded outputs would look like success.
