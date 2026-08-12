# Results

Every number Mahoraga publishes, the artifact it came from, and the limits it
carries. Nothing here is quoted from memory — each figure is recomputed from a
per-case JSONL committed to this repo.

## Verify it in one second

```bash
orch bench verify
```

No models, no network, no API key, no GPU. It reads the committed per-case
results, recomputes each headline figure, and requires it to round to exactly
the value published below. It exits nonzero on any mismatch and runs in CI, so
prose cannot drift away from data.

That is the cheap half of reproducibility. The expensive half —
[`orch bench repro`](../README.md#reproduce-the-benchmark) — re-runs the whole
benchmark on your hardware (~3.5 h, Ollama + `claude` CLI). Verification asks
"does the README match the data"; reproduction asks "is the data real here".
They answer different questions and you want both.

## Headline: HumanEval+, 164 tasks, live end to end

Artifact: [`experiments/live_route_humaneval_164.jsonl`](../experiments/live_route_humaneval_164.jsonl)
· 2026-08-03 · all 164 tasks in the `code` bucket.

| Policy | pass@1 | $/1k tasks |
| --- | --- | --- |
| Always cloud (`claude-cli`, Sonnet) | 0.976 | $35.97 |
| Always local (`granite4.1-8b`) | 0.805 | $0.00 |
| **Routed: local → judge → cloud** | **0.921** | **$8.47** |

The cascade recovers about two-thirds of the quality gap between the free local
arm and the cloud arm, at 23.5% of the always-cloud bill — a **76.5% cost cut**.
It escalated **22.6%** of tasks. The judge is itself a free local model, so it
contributed **$0.00** to the bill.

The number the quality rides on is the judge's **fail-recall: 0.688**. Of the 32
answers that genuinely failed the hidden tests, it caught 22 and sent them to
the cloud arm. The other 10 were served wrong. That is the quality price of the
cost cut, and it is why routed pass@1 is 0.921 rather than 0.976.

## Second run: the same bank with a stronger judge

Artifact: [`experiments/repro_2026-08-04.jsonl`](../experiments/repro_2026-08-04.jsonl)
· 2026-08-04 · independent full-bank run, adding the generated-test
`differential_check` on top of the reading judge.

| | reading judge | + code judge |
| --- | --- | --- |
| routed pass@1 | 0.921 | **0.921** |
| judge fail-recall | 0.688 | **0.784** |
| escalation rate | 22.6% | 39.0% |
| routed $/1k | $8.47 | $14.74 |
| cost reduction | 76.5% | 60.9% |
| always-local pass@1 | 0.805 | 0.774 |

Two things worth reading carefully, because neither is flattering by default:

**Routed pass@1 came out identical — 0.921 — across two independent runs.** That
is the strongest single line on this page. It is a replication, not a repeat:
different day, different judge configuration, different local sampling (the
local arm's own pass@1 moved 0.805 → 0.774, since decoding is not seed-pinned).
The routed result held anyway, which is the cascade doing its job — it absorbs
local variance by escalating what it catches.

**The stronger judge bought recall and no quality.** +9.6 points of fail-recall
cost +$6.27 per 1k tasks and moved routed pass@1 by zero on this bank. The extra
recall was spent escalating answers that would have passed anyway. On this
evidence the code judge is not worth its cost here; it is kept as an opt-in
(`thorough: true`), not a default. Reported because it is the result, not
because it is the one we wanted.

## Method

- **Bank**: HumanEval+ (EvalPlus v0.1.10), all 164 problems, committed at
  `experiments/prompts_humaneval_plus.jsonl` and regenerable from the release.
- **Grading**: pass@1 against HumanEval+'s hidden tests, executed locally. The
  judge never sees those tests — it reads only the prompt and the candidate.
- **Cost**: not estimated from a token rate table. Each cloud call is billed
  through the `claude` CLI, which reports its own per-task cost; those figures
  are summed. Local inference is counted at $0 marginal — hardware and
  electricity are not amortised in.
- **Always-cloud baseline**: measured, not extrapolated. The cloud arm ran on
  every task, including ones the cascade kept local, so the comparison is on
  identical inputs.
- **Hardware**: 16 GB M-series MacBook Pro. Local arm `granite4.1:8b`, judge
  `qwen3.5:latest`, cloud arm `claude-cli` (Sonnet).

## What these numbers support, and what they don't

Supported:

- On HumanEval+, a cascade of a free 8B local model plus a free local judge
  reaches 94% of frontier pass@1 at 24% of frontier cost.
- The routed result replicated exactly across two independent full-bank runs.
- The published figures are recomputable by anyone with the repo, in one
  command, without hardware.

Not supported, and not claimed anywhere:

- **Anything about production, users, uptime, or scale.** Mahoraga has one user
  by design. Daily-use figures are tracked separately by
  [`orch metrics usage`](../README.md#monitoring) and are a different claim.
- **That the contextual bandit improves routing.** It does not, measurably: in
  head-to-head evaluation it never beat round-robin between the two local arms.
  The bandit is architecture in this repo, not a result. The cascade is the
  tier that carries the numbers above.
- **Generalisation beyond Python function synthesis.** One bank, one language,
  one task shape, n=164, one hardware configuration.
- **A cost cut against interactive assistant use.** The baseline is
  substitution — what these same tasks cost on the cloud arm — not what a
  conversation carrying full context would have cost.

## Adding a claim

Published numbers live in [`experiments/claims.json`](../experiments/claims.json),
bound to the artifact they were computed from and the decimal place they were
printed at. If a number is not in that manifest with an artifact behind it, it
does not go in the README. `orch bench verify` enforces this in CI.
