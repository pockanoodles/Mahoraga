# Ablation Study Summary

All experiments run on 200-task oracle, seed=42.

## Strategy Comparison

| Configuration | Final Regret |
|--------------|-------------|
| static | 4.9098 |
| ucb1 | 26.4484 |
| thompson | 12.5048 |
| linucb | 14.3601 |
| dlinucb | 17.2485 |

## Warm Start

| Configuration | Final Regret |
|--------------|-------------|
| dlinucb (cold) | 14.1286 |
| dlinucb (warm) | 13.3256 |

## Episodic Memory

| Configuration | Final Regret |
|--------------|-------------|
| dlinucb (α=0.0) | 14.8449 |
| dlinucb (α=0.20) | 15.5474 |

## Swap Penalty

| Configuration | Final Regret |
|--------------|-------------|
| dlinucb (β=0.0) | 14.1355 |
| dlinucb (β=0.10) | 14.5424 |

## Bucket Granularity

| Configuration | Final Regret |
|--------------|-------------|
| dlinucb (7 buckets) | 13.5266 |
| dlinucb (3 buckets) | 12.5283 |

## Adaptive Gamma

| Configuration | Final Regret |
|--------------|-------------|
| global γ=0.98 | 12.8484 |
| adaptive γ | 11.7317 |
| adaptive γ + recovery | 11.6399 |
