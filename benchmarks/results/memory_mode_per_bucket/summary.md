# Memory-Mode Evaluation Summary

**Prompts**: 30 × 8 repeats · **Seeds**: 10 · **Modes**: semantic, keyword, off · **α**: 0.00, 0.05, 0.10, 0.20 · **Conf-weighted**: off · **Elapsed**: 103.6s

## Headline metrics (sorted by mean reward)

| Condition | Mode | α | Conf | Cum reward (mean ± std) | 95% CI | Accuracy | Regret |
|-----------|------|---|------|-------------------------|--------|----------|--------|
| `semantic@α=0.00` | semantic | 0.00 | no | 130.91 ± 2.12 | [129.40, 132.43] | 23.92% | 81.66 |
| `keyword@α=0.00` | keyword | 0.00 | no | 130.91 ± 2.12 | [129.40, 132.43] | 23.92% | 81.66 |
| `off@α=0.00` | off | 0.00 | no | 130.91 ± 2.12 | [129.40, 132.43] | 23.92% | 81.66 |
| `semantic@α=0.05` | semantic | 0.05 | no | 130.18 ± 1.83 | [128.87, 131.49] | 23.21% | 82.39 |
| `keyword@α=0.05` | keyword | 0.05 | no | 129.52 ± 1.88 | [128.17, 130.86] | 22.58% | 83.05 |
| `semantic@α=0.10` | semantic | 0.10 | no | 129.19 ± 1.81 | [127.90, 130.49] | 22.33% | 83.35 |
| `keyword@α=0.20` | keyword | 0.20 | no | 128.89 ± 2.13 | [127.37, 130.41] | 22.00% | 83.60 |
| `semantic@α=0.20` | semantic | 0.20 | no | 128.43 ± 2.03 | [126.98, 129.88] | 21.58% | 84.04 |
| `keyword@α=0.10` | keyword | 0.10 | no | 128.14 ± 1.79 | [126.86, 129.42] | 21.29% | 84.35 |

## Deltas vs off-mode baseline

| Condition | Δ mean reward | t (approx) | Rough p<0.05? |
|-----------|---------------|------------|---------------|
| `semantic@α=0.00` | +0.000 | +0.00 | no |
| `keyword@α=0.00` | +0.000 | +0.00 | no |
| `semantic@α=0.05` | -0.731 | -0.82 | no |
| `keyword@α=0.05` | -1.397 | -1.56 | no |
| `semantic@α=0.10` | -1.721 | -1.95 | no |
| `keyword@α=0.20` | -2.022 | -2.13 | yes |
| `semantic@α=0.20` | -2.488 | -2.68 | yes |
| `keyword@α=0.10` | -2.772 | -3.16 | yes |

## Per-bucket accuracy (mean across seeds)

| Bucket | `semantic@α=0.00` | `semantic@α=0.05` | `semantic@α=0.10` | `semantic@α=0.20` | `keyword@α=0.00` | `keyword@α=0.05` | `keyword@α=0.10` | `keyword@α=0.20` | `off@α=0.00` |
|---|---|---|---|---|---|---|---|---|---|
| code_generation | 0.19 | 0.18 | 0.17 | 0.19 | 0.19 | 0.18 | 0.15 | 0.16 | 0.19 |
| code_refactoring | 0.10 | 0.13 | 0.11 | 0.11 | 0.10 | 0.12 | 0.12 | 0.13 | 0.10 |
| complex_reasoning | 0.30 | 0.29 | 0.27 | 0.24 | 0.30 | 0.29 | 0.26 | 0.23 | 0.30 |
| debugging | 0.12 | 0.08 | 0.11 | 0.10 | 0.12 | 0.10 | 0.10 | 0.12 | 0.12 |
| file_operations | 0.19 | 0.16 | 0.16 | 0.17 | 0.19 | 0.19 | 0.19 | 0.23 | 0.19 |
| planning | 0.23 | 0.17 | 0.21 | 0.24 | 0.23 | 0.11 | 0.16 | 0.25 | 0.23 |
| research | 0.40 | 0.39 | 0.37 | 0.33 | 0.40 | 0.38 | 0.35 | 0.32 | 0.40 |
| simple_chat | 0.25 | 0.36 | 0.28 | 0.34 | 0.25 | 0.29 | 0.30 | 0.41 | 0.25 |
