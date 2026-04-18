# Benchmark Conclusions

## Summary

Early benchmark and simulation data from the LinUCB bandit stack. All conclusions below are from synthetic simulations (orch benchmark simulate/swap-test) run on 2026-04-14. Real-task conclusions will be added as the system accumulates runtime history.

## Strong patterns

- LinUCB shows better tail convergence than Thompson Sampling on 50-task simulation runs — exploration settles faster once context features stabilize
- Naive agent alternation costs ~0.10 reward points per task — the swap penalty is real and correctly calibrated in the bandit
- Memory bias at MEMORY_ALPHA=0.20 provides a meaningful nudge without overriding the bandit; 80/20 split between LinUCB exploit and episodic memory is the right balance for v1

## Weak patterns

- Insufficient real-task data to draw conclusions about specific agent performance on 16 GB Mac
- Cold-start behavior (agent not yet warm) not fully characterized in production

## Notable failures

- None yet from production — system not yet deployed with real tasks at scale

## Practical routing implications

- Let LinUCB dominate early; memory bias earns its weight after 50+ episodes
- Swap penalty means the router should prefer loyalty when scores are close
- OLS weight learning needs ~100 observations per bucket before it's trustworthy; fall back to priors before that

## Still uncertain

- Which free/public models perform best on which task categories on 16 GB Mac hardware
- Warm vs. cold performance delta for Ollama-backed models
- Real latency distribution for OpenCode, Gemini CLI, Goose on actual coding tasks
