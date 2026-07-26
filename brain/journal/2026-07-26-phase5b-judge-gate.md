# 2026-07-26 — Phase 5b: the verification tax, and a free local judge

## What happened

5a proved an 87–89% cost-cut *ceiling* using an oracle escalation gate. 5b asked
the real question: can a **fallible** gate — deciding escalation from prompt +
output alone, no hidden tests (the production posture) — capture that ceiling?
Answer: the heuristic can't, a cloud judge can but is too expensive through the
CLI, and a **free local judge captures nearly all of it**.

## Results (granite as primary, 5 true failures / 50)

| Gate | pass@1 | $/1k |
|---|---|---|
| oracle (5a ceiling) | 1.000 | $6.30 |
| heuristic quality | 1.000* | $42.51 (*escalates 43/50) |
| LLM judge — sonnet via claude-cli | 0.980 | $54.97 ❌ |
| **LLM judge — qwen3.5 LOCAL (free)** | **0.960** | **$6.10 (87.6% cut)** |

Three findings, in order of importance:

1. **A free local judge is the answer.** qwen3.5, judging granite's outputs with
   the anti-length rubric, hit accuracy 0.920 / fail-recall 3/5. Because the
   judge is local ($0), the routed cost collapses to just the escalations:
   **0.960 pass@1 at $6.10/1k — near-oracle economics, entirely on the local
   box.** This is the Thesis-A outcome: the gate that makes local-first routing
   pay lives locally.

2. **A capable judge CAN track correctness — first time in the project.** sonnet
   scored 0.960 accuracy, caught 4/5 real failures. Every prior LLM judge (Era 7)
   shared the heuristic's length bias; the anti-length rubric + single-output
   correctness framing broke it. The idea is validated.

3. **But the CLI egress kills the cloud judge.** $0.0487/call (cache-creation
   dominates; the CLI spawns a fresh process per call, no cache reuse) →
   $48.70/1k just to judge → judge-gate $54.97/1k, *worse than always-cloud*. A
   working gate, economically upside-down, purely from the egress.

The heuristic gate confirmed Era 10 on the escalation task: granite's failures
scored 0.75, identical to its successes' mean, so catching them forces escalating
43/50 — capturing only ~15% of the savings (tax $36.21/1k).

## Cheap-egress ranking

**local Ollama (free, on-thesis, no new egress) > Anthropic API + prompt caching
(~$2/1k, real spend + key, needs `cache_control` added to `ClaudeWorker`) > CLI
(dead).** Cache-read is 0.1× input in `pricing.py`, so a cached-rubric haiku judge
would be ~$0.002/call — but it bills real dollars, unlike the free local path.

## Shipped

- `routing/judge_gate.py` — worker-agnostic `judge_one` (drives any WorkerAdapter),
  anti-length correctness rubric, lenient verdict parse.
- `route_sim.simulate` — new `gate_cost_per_task`: the judge's own per-call cost,
  charged on every task (the honest accounting — an LLM judge isn't free like the
  heuristic).
- `orch bench report judge-gate` — default `--judge-egress local` (free); verdict
  cache keyed by judge model so re-runs / `--json` never re-pay.
- 8 tests (fake-worker judge unit tests + gate-cost sim test); suite 1446 green.

## Caveats

n=50 with only 5 granite failures → recall is noisy (need a harder bank). The
local judge leaks 2 wrong answers (0.96 not 1.0). Judged **code** with ground
truth — but on verifiable tasks you'd just run the tests (the oracle); the
judge's real job is **non-verifiable** tasks, which this run did not test.

## Next

- Harder bank (more failures) to tighten judge recall + test non-verifiable tasks.
- If pursuing a cloud judge: add `cache_control` to `ClaudeWorker` (~$2/1k) — but
  the free local judge already makes the case.
- 5c — a live routed run with the local-judge gate wired into serving, the real
  end-to-end proof of Thesis A.
