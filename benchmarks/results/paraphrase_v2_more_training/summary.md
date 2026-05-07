# Paraphrase-Transfer Benchmark — A1 hypothesis test

**Pairs**: 12 · **Test paraphrases**: 24 · **Seeds**: 10 · **Train repeats**: 15 · **Elapsed**: 59.7s

## Test-phase routing accuracy (held-out paraphrases)

| Condition | Test accuracy (mean ± std) | 95% CI | Test reward | Train reward |
|-----------|----------------------------|--------|-------------|--------------|
| `keyword@α=0.20` | 0.175 ± 0.067 | [0.127, 0.223] | 12.65 ± 0.76 | — |
| `keyword@α=0.30` | 0.167 ± 0.096 | [0.098, 0.235] | 12.55 ± 1.21 | — |
| `keyword@α=0.10` | 0.158 ± 0.043 | [0.128, 0.189] | 12.46 ± 0.63 | — |
| `semantic@α=0.30` | 0.154 ± 0.065 | [0.107, 0.201] | 12.40 ± 0.80 | — |
| `semantic@α=0.10` | 0.138 ± 0.034 | [0.113, 0.162] | 12.23 ± 0.53 | — |
| `semantic@α=0.00` | 0.125 ± 0.044 | [0.094, 0.156] | 12.11 ± 0.64 | — |
| `semantic@α=0.20` | 0.125 ± 0.028 | [0.105, 0.145] | 12.09 ± 0.60 | — |
| `keyword@α=0.00` | 0.125 ± 0.044 | [0.094, 0.156] | 12.11 ± 0.64 | — |
| `off@α=0.00` | 0.125 ± 0.044 | [0.094, 0.156] | 12.11 ± 0.64 | — |

## Δ test accuracy vs off-baseline

| Condition | Δ accuracy | t (approx) | Rough p<0.05? |
|-----------|-----------:|-----------:|:-------------:|
| `keyword@α=0.20` | +0.050 | +1.96 | no |
| `keyword@α=0.30` | +0.042 | +1.25 | no |
| `keyword@α=0.10` | +0.033 | +1.71 | no |
| `semantic@α=0.30` | +0.029 | +1.17 | no |
| `semantic@α=0.10` | +0.013 | +0.71 | no |
| `semantic@α=0.00` | +0.000 | +0.00 | no |
| `semantic@α=0.20` | +0.000 | +0.00 | no |
| `keyword@α=0.00` | +0.000 | +0.00 | no |
