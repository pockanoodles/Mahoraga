# Memory-Mode Evaluation Summary

**Prompts**: 30 × 8 repeats · **Seeds**: 10 · **Modes**: semantic, off · **α**: 0.10 · **Conf-weighted**: off · **Elapsed**: 21.3s

## Headline metrics (sorted by mean reward)

| Condition | Mode | α | Conf | Cum reward (mean ± std) | 95% CI | Accuracy | Regret |
|-----------|------|---|------|-------------------------|--------|----------|--------|
| `off@α=0.00` | off | 0.00 | no | 128.33 ± 4.28 | [125.26, 131.39] | 21.42% | 84.14 |
| `semantic@α=0.10` | semantic | 0.10 | no | 127.02 ± 1.47 | [125.97, 128.07] | 20.25% | 85.41 |

## Deltas vs off-mode baseline

| Condition | Δ mean reward | t (approx) | Rough p<0.05? |
|-----------|---------------|------------|---------------|
| `semantic@α=0.10` | -1.304 | -0.91 | no |

## Per-bucket accuracy (mean across seeds)

| Bucket | `semantic@α=0.10` | `off@α=0.00` |
|---|---|---|
| code_generation | 0.16 | 0.17 |
| code_refactoring | 0.05 | 0.11 |
| complex_reasoning | 0.22 | 0.21 |
| debugging | 0.11 | 0.12 |
| file_operations | 0.26 | 0.21 |
| planning | 0.16 | 0.14 |
| research | 0.35 | 0.39 |
| simple_chat | 0.17 | 0.17 |
