# Memory-Mode Evaluation Summary

**Prompts**: 30 × 8 repeats · **Seeds**: 10 · **Modes**: semantic, keyword, off · **α**: 0.10, 0.20 · **Conf-weighted**: off · **Elapsed**: 44.0s

## Headline metrics (sorted by mean reward)

| Condition | Mode | α | Conf | Cum reward (mean ± std) | 95% CI | Accuracy | Regret |
|-----------|------|---|------|-------------------------|--------|----------|--------|
| `off@α=0.00` | off | 0.00 | no | 128.33 ± 4.28 | [125.26, 131.39] | 21.42% | 84.14 |
| `keyword@α=0.20` | keyword | 0.20 | no | 127.89 ± 1.99 | [126.46, 129.31] | 21.12% | 84.53 |
| `semantic@α=0.10` | semantic | 0.10 | no | 127.80 ± 2.31 | [126.15, 129.45] | 20.96% | 84.61 |
| `keyword@α=0.10` | keyword | 0.10 | no | 127.01 ± 2.12 | [125.49, 128.53] | 20.25% | 85.38 |
| `semantic@α=0.20` | semantic | 0.20 | no | 125.68 ± 2.32 | [124.02, 127.34] | 19.00% | 86.63 |

## Deltas vs off-mode baseline

| Condition | Δ mean reward | t (approx) | Rough p<0.05? |
|-----------|---------------|------------|---------------|
| `keyword@α=0.20` | -0.437 | -0.29 | no |
| `semantic@α=0.10` | -0.527 | -0.34 | no |
| `keyword@α=0.10` | -1.315 | -0.87 | no |
| `semantic@α=0.20` | -2.647 | -1.72 | no |

## Per-bucket accuracy (mean across seeds)

| Bucket | `semantic@α=0.10` | `semantic@α=0.20` | `keyword@α=0.10` | `keyword@α=0.20` | `off@α=0.00` |
|---|---|---|---|---|---|
| code_generation | 0.15 | 0.12 | 0.15 | 0.15 | 0.17 |
| code_refactoring | 0.07 | 0.09 | 0.08 | 0.09 | 0.11 |
| complex_reasoning | 0.26 | 0.24 | 0.26 | 0.24 | 0.21 |
| debugging | 0.11 | 0.10 | 0.10 | 0.11 | 0.12 |
| file_operations | 0.21 | 0.17 | 0.17 | 0.24 | 0.21 |
| planning | 0.15 | 0.12 | 0.09 | 0.14 | 0.14 |
| research | 0.36 | 0.33 | 0.36 | 0.35 | 0.39 |
| simple_chat | 0.24 | 0.23 | 0.23 | 0.34 | 0.17 |
