# 2026-09-29 — can a local model edit a real repo?

## What prompted it

The delegation funnel's first reading excluded 1,299 of 1,613 logged actions as
`edit-in-place`: surgical changes to existing files, disqualified because the
arms never see a repository. That was the strongest evidence in the repo that
the binding constraint is arm *context*, not arm *selection*. But it only
said what was excluded, not what repo access would buy.

The question behind it was a product one. Mahoraga is useful to people who pay
per API call; it is not useful to its own author, whose work is editing files
in repos. If local models could make those edits, the cascade gets a second,
much larger job.

## The choice not to build

A tool loop is about 30 lines. What makes one work with a weak model is
everything around it: an edit format the model does not mangle, a compressed
view of the repo that fits a small context, recovery from garbage output. Aider
has years of tuning for exactly that, and it runs Ollama models. So the probe
uses Aider as the agent and measures it, rather than building a worse loop
before knowing whether the idea holds.

This also fixes the architecture if the answer is yes: **Mahoraga is the layer
above the agent**, not the agent. A local agent tries, the repo's tests decide,
and a frontier agent takes over on failure.

## The design decisions that made it honest

**Ground truth by construction.** Each task is a real commit with its source
change reverted and its tests kept. Two gates reject tasks that cannot
discriminate: the tests must pass at the commit, and must fail at the base.
The agent gets the commit message and the test file names, never the source
files.

**"Resolved" includes not breaking anything.** Every fail-to-pass test must pass
AND every test that passed before must still pass across the full suite. The
second half turned out to be the one that mattered.

**Tests are restored before scoring.** An agent that edits a test to make it
pass gets scored against the real one. None tried.

**Isolation was load-bearing, not hygiene.** Tests at commits before 07-03 write
into their repo's `brain/` (the bug `74a2e41` fixed), and during the run they
did, inside the throwaway clones. Pytest ran under a scratch HOME for the same
reason.

## Result

| Arm | Resolved | Multi-file | Broke other code | Median |
| --- | --- | --- | --- | --- |
| qwen3.5, blind | 5 / 18 | 4 / 12 | 7 | 8.4 min |
| qwen3.5, test feedback | 5 / 18 | 3 / 12 | 5 | 13.1 min |
| granite4.1-8b, blind | 0 / 18 | 0 / 12 | 1 | 12.9 min |
| granite4.1-8b, test feedback | 1 / 18 | 0 / 12 | 0 | 11.8 min |

**1. About one real edit in four lands, from a free 9B model on a laptop.** That
includes two three-file changes solved end to end. Not the result that makes
local editing the default, but not zero either.

**2. The test gate caught every failure.** No failing attempt in any arm would
have been served. The typical failure is *over-reach*: the model fixes the
target and "tidies" a neighbouring function it had no reason to touch, which
breaks an existing test. Without the full-suite check, that ships silently.

**3. Feedback does not raise the solve rate.** Seeing the failing tests made
qwen back out of over-reach (regressions 7 → 5) but it solved no hard task it
had missed blind, and it took 1.5× as long. When the model does not understand
the change, more attempts do not supply understanding.

**4. Arm quality does not transfer across interfaces.** granite4.1-8b, the
cascade's first-try arm and the best local arm on HumanEval+, is the worst
editing arm by a distance. In 27 of 36 attempts it wrote search blocks quoting
code that does not exist in the file. The Phase-4 ranking is a ranking for
single-prompt synthesis, not for "can this model be an agent."

**5. Latency, not accuracy, decides the product shape.** 8 to 13 minutes per
attempt on 16 GB rules out interactive use: nobody waits ten minutes to be told
to go ask Claude. The viable shape is asynchronous — a queue where the free
model takes one shot, the tests decide, and the rest escalates.

## What went wrong, and what it cost

- An uncapped qwen3.5 thinking runaway held one call past litellm's 600-second
  timeout. Fixed by capping generation at 8,192 tokens, and the four attempts
  made before the fix were rerun so every scored attempt shares one config.
- The first task build recorded no regression baseline for five tasks, because
  a new test file that cannot import crashed the whole pytest run. Fixed with
  `--continue-on-collection-errors`; one March-era commit was dropped when its
  suite could not run at all.
- The machine drained from 100% to 7% **while plugged in**: a 30 W adapter
  cannot keep up with sustained Ollama load. The run was paused at 00:14 and
  resumed on a 65 W adapter. For any overnight bench: 65 W or more, lid open,
  `caffeinate -s -i -w <pid>`.

## Limits

n = 18 from one repository, whose tests were written alongside each fix. Commit
messages are richer than a cold bug report. "Resolved" means the tests pass:
`a7d91e2` was solved without the live-route flush the original also made, which
no test covers. Four of qwen's blind failures are partly environmental (two
edit-format rejections, a 30 MB tracked log blowing the context at one commit,
one timeout).

## Open

- Whether the async-queue product is worth building is a direction decision,
  not a finding. This measurement is the input to it.
- The findings Era for this belongs after PR #42 lands, since Eras 25–33 live
  on that branch.
- Next cheap check if the direction holds: `ornith-1.5:9b` (on disk, Qwen3.5
  lineage) and `whole`-format edits for small files, to see whether the 28% is
  a model ceiling or a format ceiling.

Harness and per-attempt results: `experiments/agent_edit/`.
