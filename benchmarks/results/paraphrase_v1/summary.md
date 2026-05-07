# Paraphrase-Transfer Benchmark — A1 hypothesis test

**Pairs**: 12 · **Test paraphrases**: 24 · **Seeds**: 10 · **Train repeats**: 6 · **Elapsed**: 17.9s

## Test-phase routing accuracy (held-out paraphrases)

| Condition | Test accuracy (mean ± std) | 95% CI | Test reward | Train reward |
|-----------|----------------------------|--------|-------------|--------------|
| `keyword@α=0.30` | 0.242 ± 0.096 | [0.173, 0.310] | 13.31 ± 1.04 | — |
| `keyword@α=0.20` | 0.229 ± 0.091 | [0.164, 0.294] | 13.18 ± 1.03 | — |
| `semantic@α=0.20` | 0.212 ± 0.080 | [0.156, 0.269] | 12.99 ± 0.73 | — |
| `semantic@α=0.30` | 0.196 ± 0.062 | [0.151, 0.240] | 12.81 ± 0.65 | — |
| `semantic@α=0.00` | 0.192 ± 0.090 | [0.127, 0.256] | 12.79 ± 1.07 | — |
| `keyword@α=0.00` | 0.192 ± 0.090 | [0.127, 0.256] | 12.79 ± 1.07 | — |
| `off@α=0.00` | 0.192 ± 0.090 | [0.127, 0.256] | 12.79 ± 1.07 | — |
| `semantic@α=0.10` | 0.175 ± 0.076 | [0.121, 0.229] | 12.61 ± 0.91 | — |
| `keyword@α=0.10` | 0.175 ± 0.073 | [0.123, 0.227] | 12.62 ± 0.88 | — |

## Δ test accuracy vs off-baseline

| Condition | Δ accuracy | t (approx) | Rough p<0.05? |
|-----------|-----------:|-----------:|:-------------:|
| `keyword@α=0.30` | +0.050 | +1.20 | no |
| `keyword@α=0.20` | +0.037 | +0.93 | no |
| `semantic@α=0.20` | +0.021 | +0.55 | no |
| `semantic@α=0.30` | +0.004 | +0.12 | no |
| `semantic@α=0.00` | +0.000 | +0.00 | no |
| `keyword@α=0.00` | +0.000 | +0.00 | no |
| `semantic@α=0.10` | -0.017 | -0.45 | no |
| `keyword@α=0.10` | -0.017 | -0.45 | no |
