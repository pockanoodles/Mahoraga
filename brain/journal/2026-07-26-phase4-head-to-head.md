# 2026-07-26 — Phase 4: local roster vs Claude Code (head-to-head)

## What happened

Ran Phase 4 (Q5) end-to-end for the first time: the local 3-arm roster vs the
`claude-cli` cloud arm on the 50-row verifiable bank, with real cost capture.
`bench_run_id=19`, force-explore, 4 arms × 50 prompts × repeats=1 = 200 tasks,
memory off, ~35 min wall (2115s). Preflight verified the cloud arm end-to-end
(model alias accepted, cost lands in both `task_metrics` and `cost_ledger`)
before committing the run.

## Results — pass@1 (hidden-test execution) + measured cost

| Arm | pass@1 | cost/task |
|---|---|---|
| claude-cli (Sonnet 4.6) | **1.000** (50/50) | **$0.0491** (measured) |
| ollama:granite4.1-8b (5.3 GB) | **0.900** (45/50) | $0 |
| ollama:qwen3-14b (9.3 GB) | **0.880** (44/50) | $0 |
| ollama:qwen3.5 (6.6 GB) | **0.818** (36/44) | $0 |

- **Quality:** best local arm (granite) retains **90%** of Claude's verified
  pass@1 at $0 marginal cost.
- **Cost:** Claude actually charged **$0.0491/task** ($2.4526 for 50, min
  $0.0066 / max $0.077 — cache-creation-dominated). Local is free.
- **Heuristic inversion replicated** on independent data + a stronger arm:
  Spearman rho=0.2, the perfect arm (claude) ranks only 3/4 by the heuristic
  (heuristic's top pick = qwen3-14b). Confirms Era 9 — the heuristic is blind
  to correctness — now against a 100%-correct cloud arm.

## The cost number: report floor vs honest denominator

`orch bench report cost --bench-run-id 19` headlines **9.9% / $1.36 per 1k** —
this is the documented *floor*. It prices hypothetical-cloud local rows at
bare token rates with no cache modeling (~$0.0018/task), understating real
cloud cost ~27×. The honest denominator is the **measured** $0.0491/task from
the actual claude-cli rows.

**Honest economics (projection, not measured — this batch was round-robin, not
routed):** granite-first + verify-gate + escalate the ~10% failures to cloud →
~$4.9 per 1k vs $49 all-cloud ≈ **90% cost cut at ~cloud quality** (claude
solved 100%, so it'd likely recover granite's misses).

## Caveats (all logged, none hidden)

1. **Force-explore ≠ routing.** This measured per-arm quality+cost, NOT the
   bandit's routing policy. The cost report's "74.9% local" is just 3/4 arms
   being local, not a learned fraction. Routing/escalation economics are
   projections from the measured per-arm numbers.
2. **qwen3.5's 6 non-passes were infrastructure, not the model** — Ollama
   `HTTP 500`/`ReadError` with empty output, clustered at run start (cold-load
   flakiness on 16 GB during the first arm's warmup, visible as the 139s
   elapsed spikes in the log). Model pass@1 = 0.818 over 44 completed; 0.72 if
   cold-load drops count as failures. Future benches: add a warmup call + retry.
3. **n=50, repeats=1.** claude-vs-local gap is robust; 1-prompt gaps *among*
   local arms (granite 45 vs qwen3-14b 44) are within noise. Python-only,
   benchmark not live traffic.

## Decision surfaced: qwen3-14b does not earn its RAM

The 9.3 GB arm was retained (2026-07-10) as a diagnostic; the audition
condition was "does it earn a permanent seat on the correctness axis we now
trust." Answer on this properly-powered run: **no** — it lands middle of the
pack (0.880), beaten by the 5.3 GB granite (0.900), no correctness advantage
for ~2× the footprint. Recommendation: drop to a lean 2-local-arm roster
(granite + qwen3.5). Left as a scope decision for Kaito, not pulled silently.

## Housekeeping done

`claude-cli` reverted to `enabled: false` in agents.yaml; manual `orch serve`
stopped; caffeinate released; server down. Daemon left stopped (as it was
pre-session) — `orch service start` to restore live routing. Raw outputs in
`$CLAUDE_JOB_DIR/tmp/phase4_results.jsonl` (ephemeral); authoritative record is
`bench_run_id=19` + verify/cost reports.

## Follow-up shipped same session

- **qwen3-14b dropped** — Kaito's call; roster now granite + qwen3.5.
  `enabled: false` in agents.yaml (bandit history preserved).
- **Infra flake fixed** in `workers/ollama.py`: transient cold-load failures
  (HTTP 5xx, ReadError/ReadTimeout/RemoteProtocolError) now retry up to 2× with
  2s→4s backoff; 4xx and ConnectError fail fast. Safe because the response is
  buffered and nothing is yielded until the stream completes — so a retry can
  rebuild from scratch. 5 regression tests, full suite 1430 green. Helps live
  traffic too (idle-eviction cold loads), not just benches.

## Next

- Open thread (c): LLM-judge validation — now trivially cheap against this
  run's 50-row ground truth (score the outputs, correlate vs pass@1).
- Q6 (memory on/off) re-run on the fixed ruler (pass@1 can finally see it).
- Optional: repeats=2 pass to tighten the local-arm error bars.
- Commit outstanding: roster + flake fix + Phase 4 brain docs are uncommitted
  on `main` — need a feat branch + PR.
