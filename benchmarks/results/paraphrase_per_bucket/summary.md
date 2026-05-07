# Paraphrase-Transfer Benchmark — A1 hypothesis test

**Pairs**: 12 · **Test paraphrases**: 24 · **Seeds**: 10 · **Train repeats**: 15 · **Elapsed**: 70.9s

## Test-phase routing accuracy (held-out paraphrases)

| Condition | Test accuracy (mean ± std) | 95% CI | Test reward | Train reward |
|-----------|----------------------------|--------|-------------|--------------|
| `semantic@α=0.20` | 0.212 ± 0.072 | [0.161, 0.264] | 13.04 ± 1.00 | — |
| `semantic@α=0.30` | 0.208 ± 0.062 | [0.164, 0.253] | 12.99 ± 0.87 | — |
| `keyword@α=0.30` | 0.196 ± 0.088 | [0.133, 0.259] | 12.87 ± 1.13 | — |
| `keyword@α=0.20` | 0.192 ± 0.063 | [0.147, 0.237] | 12.81 ± 0.70 | — |
| `semantic@α=0.10` | 0.163 ± 0.072 | [0.111, 0.214] | 12.52 ± 1.07 | — |
| `keyword@α=0.10` | 0.158 ± 0.055 | [0.119, 0.198] | 12.48 ± 0.73 | — |
| `semantic@α=0.00` | 0.113 ± 0.056 | [0.073, 0.152] | 11.97 ± 0.56 | — |
| `keyword@α=0.00` | 0.113 ± 0.056 | [0.073, 0.152] | 11.97 ± 0.56 | — |
| `off@α=0.00` | 0.113 ± 0.056 | [0.073, 0.152] | 11.97 ± 0.56 | — |

## Δ test accuracy vs off-baseline

| Condition | Δ accuracy | t (approx) | Rough p<0.05? |
|-----------|-----------:|-----------:|:-------------:|
| `semantic@α=0.20` | +0.100 | +3.47 | yes |
| `semantic@α=0.30` | +0.096 | +3.63 | yes |
| `keyword@α=0.30` | +0.083 | +2.53 | yes |
| `keyword@α=0.20` | +0.079 | +2.98 | yes |
| `semantic@α=0.10` | +0.050 | +1.74 | no |
| `keyword@α=0.10` | +0.046 | +1.85 | no |
| `semantic@α=0.00` | +0.000 | +0.00 | no |
| `keyword@α=0.00` | +0.000 | +0.00 | no |
