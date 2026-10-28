# 2026-10-28: the laptop's 131k numbers measured two clocks, not the model

Kaito asked for everything that could be done overnight toward the October report,
with the Studio offline and the laptop as the only machine.

## What happened

The plan was to finish the laptop's 38-task set at the 90-minute limit. The engine
rule that made that cheap holds: the attempt limit is a kill from outside that aider
never sees, so a 20-minute attempt that ended on its own is exactly the attempt a
90-minute limit would have produced. Only the 11 timed-out tasks needed a rerun, and
claims gained `limit_s` to count attempts that way (#67).

Reading the logs to explain why the laptop solved 8 of 28 tasks where the Studio solved
14 with the same model file showed the real cause. Aider gives each model request
litellm's 600 seconds unless told otherwise, and the laptop reads a long prompt at
about 140 tokens a second, so any request over about 84k tokens is cut and retried.
27 of the 20-minute night's 38 attempts lost a request (25 of its 26 timeouts came
after one), 13 of the 16 attempts at the 90-minute limit did, and the two that hit
that limit never got a reply at all. The Studio reads prompts several times faster
and never met that clock, so the gap measured aider. The same bug would have hit any
16 GB user on the 131k default arm.

## What changed

- #68: aider's `--timeout` is the attempt limit, and every attempt keeps its failed
  requests (counted from the aider log for attempts recorded before).
- The confounded agentbench night was stopped. All 47 laptop tasks rerun on the fixed
  harness in a fresh root (`agentbench-131k`), code pinned at b07b584, from the night
  of 10-28. The old attempts stay as `laptop-2026-10.jsonl`, the record section 6 of
  the report describes, not a result.
- #67: claims can count attempts at a limit, pair two slices task by task with an
  exact McNemar p, and pass failed and timed-out counts to the verdict, so #63's rule
  fires in `bench verify` too. `summarise` uses the verdict's median.
- #66: six README and DATAFLOW commits from 10-14 that never left a local main.
- #69: the dead web tools are deleted, and every Ollama call reads `OLLAMA_BASE_URL`,
  which also fixes the router's workers ignoring it (the Docker image looked for
  Ollama inside the container).
- HumanEval+ granite 4.1 against 4.2 ran for section 2. The worker returned an answer
  on 164 of 164 prompts for 4.1 and on only 46 of 164 for 4.2, which looks like a
  harness failure (the ornith pattern) and must be read from the logs and graded
  before the report cites it.
- The v2 slice-1 design got 12 findings from Codex, all kept: slice 1 becomes a
  read-only inventory of the user's history (human turns, tool calls paired with their
  results, usage once per message, which evidence exists), with no "replayable" count
  until reconstruction and grading are built and tested.

## Why it matters

This is the third time a clock outside the model set a published-looking number: the
20-minute attempt limit, then aider's request timeout, after the 32k context window
before them. Each time the tell was the same, a slow machine's failures concentrated
in long prompts. The audit now treats the attempt limit as the only clock it sets, and
the report says so.
