# Audit attempts behind the October report

One line per counted attempt, so the report's rates and verdicts can be
recomputed without the benches they came from. `orch bench verify` does that
for every claim in `experiments/claims.json` of kind `agentbench`, and CI runs it.

| File | Machine | What |
| --- | --- | --- |
| `studio-2026-10.jsonl` | Mac Studio M5 Max, 64 GB | qwen3.5 and qwen3.6 under aider, blind, on Mahoraga, more-itertools, click and attrs (2026-10-05 to 2026-10-07), and qwen3.5 at a 131k context on the three outside repositories (2026-10-13 to 2026-10-14) |
| `laptop-2026-09.jsonl` | 16 GB MacBook Pro | qwen3.5 and granite 4.1 under aider on Mahoraga (the probe in `../agent_edit/`), and qwen3.5 on the ops repository |
| `laptop-2026-10.jsonl` | 16 GB MacBook Pro (M4, Mac16,1) | qwen3.5 at 131k under aider, blind, on more-itertools and attrs, before the request-timeout fix: the 20-minute night of 2026-10-14 and the 90-minute nights of 2026-10-17, 2026-10-19 and 2026-10-28. Kept as the record of what section 6 describes, not as a result |

`specimen-2026-10.json` is not a ledger. It keeps one attempt whole for the landing page:
the first task of the laptop's clean 131k run (more-itertools `f89d7a3`, picked by its
position in the bench, not by its outcome), with the commit's own fix, the agent's diff,
the grader's counts and the run it came from. `scripts/specimen.py` writes it from the
bench, and `scripts/export_site.py` refuses it if its outcome and its counts disagree.

Each line names the repo, the task's commit and its commit date, the arm,
the condition and repeat, whether the attempt resolved the task, how long it
took, the run it came from, and the machine and model digest that run recorded.

The Studio file is `mahoraga audit export` over those four benches. The
laptop's attempts predate run manifests, so their machine reads `unrecorded`.
Its Mahoraga lines are the probe's own results in `../agent_edit/results/`,
one line per result file with the commit date taken from git, and its ops
lines are `mahoraga audit export` over the ops bench.

`laptop-2026-10.jsonl` is `export.write` over four benches in one file: the
default bench root's attrs and more-itertools benches (the 20-minute night)
and the same two in the long-limit root (the 90-minute nights). Each line also
says whether the attempt ran out of time, the limit its run gave it, and how
many model requests failed, counted from the aider log for attempts recorded
before that count was kept. Its model digest (`6488c96fa5fa`) differs from the
Studio's (`6bac66862d57`) because each is the digest of a machine's own Ollama
manifest. Both manifests point at the same model file, sha256 `dec52a44…`.

Degraded attempts (a throttled machine, a crashed agent) didn't measure the
agent and are not in these files, as they are in no published count.
