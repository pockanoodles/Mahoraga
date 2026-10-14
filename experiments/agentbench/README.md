# Audit attempts behind the October report

One line per counted attempt, so the report's rates and verdicts can be
recomputed without the benches they came from. `orch bench verify` does that
for every claim in `experiments/claims.json` of kind `agentbench`, and CI runs it.

| File | Machine | What |
| --- | --- | --- |
| `studio-2026-10.jsonl` | Mac Studio M5 Max, 64 GB | qwen3.5 and qwen3.6 under aider, blind, on Mahoraga, more-itertools, click and attrs (2026-10-05 to 2026-10-07), and qwen3.5 at a 131k context on the three outside repositories (2026-10-13 to 2026-10-14) |
| `laptop-2026-09.jsonl` | 16 GB MacBook Pro | qwen3.5 and granite 4.1 under aider on Mahoraga (the probe in `../agent_edit/`), and qwen3.5 on the ops repository |

Each line names the repo, the task's commit and its commit date, the arm,
the condition and repeat, whether the attempt resolved the task, how long it
took, the run it came from, and the machine and model digest that run recorded.

The Studio file is `mahoraga audit export` over those four benches. The
laptop's attempts predate run manifests, so their machine reads `unrecorded`.
Its Mahoraga lines are the probe's own results in `../agent_edit/results/`,
one line per result file with the commit date taken from git, and its ops
lines are `mahoraga audit export` over the ops bench.

Degraded attempts (a throttled machine, a crashed agent) didn't measure the
agent and are not in these files, as they are in no published count.
