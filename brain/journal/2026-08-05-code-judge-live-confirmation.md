# 2026-08-05 — Code-judge live confirmation: the pre-registered test ran

## What ran

Era 22 ended with a guard: don't cite the 0.939 replay headline until
`orch bench repro --code-judge` confirms it on fresh inference. Attempt #1
(2026-08-04 evening) died twice: the Mac slept (lid closed on battery — nohup
survives hangups, not sleep) and, once awake, an OverflowError at case 83/164
(factorial-class ints overflow `float()` in `values_equal`). PR #33 fixed the
compare, added a crash-net (gate exceptions degrade to abstain in
`route_one`), and made live-route flush per-case JSONL incrementally.
Attempt #2 ran clean: 164/164, ~85s/case, caffeinate pinned to the PID.

## Results (fresh inference, all 164, min_disagree=2 untouched)

| | this run | Era 19 (P0) | Era 22 projection |
|---|---|---|---|
| always-cloud | 0.970 @ $37.69/1k | 0.976 @ $35.97 | — |
| always-local (granite) | 0.774 | 0.805 | — |
| routed | 0.921 @ $14.74/1k | 0.921 @ $8.47 | 0.939 @ $10.04 |
| judge fail-recall | **29/37 = 0.784** | 0.688 | 0.781 |
| retention / cut | 94.9% / 60.9% | 94.4% / 76.5% | 96.2% / 72.1% |

**Same-run paired decomposition** (from `judge_detail` on the recorded rows —
flip the 19 `code-judge override` rows back to accept):

- base judge only: recall 21/37 = **0.568**, routed 0.884 @ $10.43/1k
- base + code-judge: recall **0.784**, routed **0.921 @ $14.74/1k**
- the tool: 8/8 catches genuine (6 recovered by cloud), **0/11
  over-escalations lost quality** (cloud recovered every one)

## The verdict

**Reproduced:** the tool's own claims — recall 0.784 vs 0.781 projected, and
the recall-only economics (quality can't go down; live, it didn't). The
same-run lift, +3.7 pts routed pass@1 for +$4.31/1k, is the strongest honest
claim the tool has.

**Not reproduced:** the 0.939/96.2%/72.1% package — dead as a headline,
permanently. Not the tool's fault: run-to-run variance elsewhere dominates
(granite 0.805→0.774, base-judge recall 0.688→0.568, base over-escalations
15→24). The pre-registration did its job: had we cited the replay, a
reproducer would have gotten 0.921.

**Three findings with legs** (detail in findings Era 24): the reading judge is
the high-variance component and the execution layer is the stable one; the
min_disagree threshold is exhausted as a precision lever (counts 2–7 vs 2–8
don't separate); single-run numbers carry ±3-pt local-arm variance, so
headlines cite runs or ranges, never projections.

## Citable state after tonight

Era 19's P0 headline stands unchanged (0.921 @ $8.47/1k, 76.5% cut, 94.4% of
cloud — complete honest run). Addable, all confirmed fresh: judge fail-recall
0.69 → 0.78 via self-generated executed tests; +3.7 pts same-run; 94–95%
retention across both fresh runs; verification failures cost money, never
correctness. README needs no correction — it cites P0.

Data: `experiments/repro_2026-08-04.jsonl` (per-case, incl. judge_detail
provenance). Chained next: K=5 case-coverage sweep (running overnight),
then A1 semantic routing — now the top open lever (Era 23 exonerated the
reward; Era 24 caps what judge recall alone can add at 16 GB).
