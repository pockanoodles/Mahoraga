# Mahoraga Routing Benchmark Results

| Strategy | Success Rate | Mean Reward | Avg Latency | Avg Cost | Total Regret | beta | Sublinear? |
|----------|-------------|-------------|-------------|---------|-------------|------|------------|
| static | 96.0% | **0.8649** | 6.3s | $0.0360 | 6.88 | 1.569 | No |
| ucb1 | 83.5% | 0.7524 | 6.5s | $0.0323 | 28.69 | 0.950 | No |
| thompson | 92.5% | 0.8070 | 6.2s | $0.0281 | 17.73 | 1.175 | No |
| linucb | 90.0% | 0.8049 | 6.3s | $0.0295 | 18.38 | 0.659 | Yes |

## Oracle: Best Agent per Category

| Category | Best Agent | Mean Score |
|----------|-----------|------------|
| simple_chat | ollama | 0.92 |
| code_generation | codex-cli | 0.90 |
| code_refactoring | aider | 0.92 |
| debugging | aider | 0.88 |
| file_operations | codex-cli | 0.93 |
| research | gemini-cli | 0.88 |
| planning | gemini-cli | 0.80 |
| complex_reasoning | gemini-cli | 0.82 |
