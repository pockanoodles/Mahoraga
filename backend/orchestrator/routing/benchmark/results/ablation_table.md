# Ablation Study Results

Hyperparameter sweeps for Mahoraga's LinUCB bandit router.
Each row is a full 200-task replay with different settings.

### Ablation: alpha (exploration)

| Setting | Success Rate | Mean Reward | Total Regret | beta (growth) | Sublinear? | Avg Latency | Avg Cost |
|---------|-------------|-------------|-------------|--------------|------------|------------|----------|
| alpha=0.5 | 90.5% | 0.8159 | 16.58 | 0.694 | Yes | 6.9s | $0.0439 |
| alpha=1.0 | 91.0% | 0.8098 | 17.43 | 0.576 | Yes | 6.4s | $0.0312 |
| alpha=2.0 | 89.0% | 0.7931 | 20.70 | 0.951 | No | 6.6s | $0.0333 |
| alpha=5.0 | 82.0% | 0.7490 | 29.45 | 1.028 | No | 6.4s | $0.0321 |
| alpha=0.25 | 79.5% | 0.7225 | 34.96 | 1.033 | No | 7.2s | $0.0635 |
| alpha=0.1 | 59.5% | 0.6436 | 50.49 | 0.824 | No | 4.1s | $0.0196 |

**Best: alpha=0.5** (reward=0.8159, regret=16.58)


### Ablation: Context dimension (d)

| Setting | Success Rate | Mean Reward | Total Regret | beta (growth) | Sublinear? | Avg Latency | Avg Cost |
|---------|-------------|-------------|-------------|--------------|------------|------------|----------|
| d=14 (Tier 1+2) | 91.0% | 0.8129 | 16.93 | 0.825 | No | 6.5s | $0.0340 |
| d=8 (Tier 1) | 87.5% | 0.7898 | 21.41 | 0.928 | No | 6.4s | $0.0302 |

**Best: d=14 (Tier 1+2)** (reward=0.8129, regret=16.93)


### Ablation: Reward weights

| Setting | Success Rate | Mean Reward | Total Regret | beta (growth) | Sublinear? | Avg Latency | Avg Cost |
|---------|-------------|-------------|-------------|--------------|------------|------------|----------|
| balanced (success=0.40, quality=0.20, speed=0.20, cost=0.20) | 92.0% | 0.8198 | 15.43 | 0.733 | Yes | 6.3s | $0.0292 |
| cost_first (success=0.25, quality=0.15, speed=0.15, cost=0.45) | 91.0% | 0.8163 | 16.29 | 0.773 | Yes | 6.4s | $0.0299 |
| speed_first (success=0.25, quality=0.15, speed=0.45, cost=0.15) | 90.5% | 0.8106 | 17.34 | 0.859 | No | 6.3s | $0.0290 |
| quality_first (success=0.30, quality=0.40, speed=0.15, cost=0.15) | 90.0% | 0.8093 | 17.69 | 0.773 | Yes | 6.5s | $0.0292 |
| success_only (success=1.00, quality=0.00, speed=0.00, cost=0.00) | 90.0% | 0.8071 | 17.92 | 0.826 | No | 6.5s | $0.0300 |
| default (success=0.50, quality=0.25, speed=0.15, cost=0.10) | 91.0% | 0.8060 | 18.20 | 0.591 | Yes | 6.3s | $0.0320 |

**Best: balanced (success=0.40, quality=0.20, speed=0.20, cost=0.20)** (reward=0.8198, regret=15.43)
