# Bandit Router Benchmark Results

| Strategy | Success Rate | Avg Latency | Avg Cost/Task | Total Cost | Avg Reward |
|----------|-------------|-------------|---------------|------------|------------|
| static (baseline) | 73.5% | 4.9s | **$0.0000** | **$0.0000** | 0.622 |
| ucb1 | 75.0% | 5.0s | $0.0009 | $0.1704 | 0.634 |
| thompson | **78.0%** | 5.0s | $0.0012 | $0.2366 | **0.653** |
| linucb | 75.0% | **4.7s** | $0.0010 | $0.1941 | 0.640 |
