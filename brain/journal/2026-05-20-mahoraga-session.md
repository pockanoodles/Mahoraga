# Mahoraga session — 2026-05-20

## What we did

**Root cause resolved:** `orch serve` was never running between sessions — that's why zero data accumulated across all prior work. Fixed permanently with `orch service install` (launchd daemon, login-persistent, KeepAlive=true).

**3-arm local roster finalized:**
- `ollama:qwen3.5` — code/reasoning
- `ollama:gemma4-e4b` — plan/research
- `ollama:granite4.1-8b` — structured/test/review (pulled today, ~5 GB IBM model)
- Cloud agents effectively disabled for now (no API keys, budget-gated)

**`linucb_per_bucket` activated as default strategy:**
- Was built and registered but never the default — service booted with v1 `"linucb"` on every restart
- One-line change in `app.py`; committed in `2320ed1`
- Updated `_DEFAULT_PRIORS` to match actual arm names (`ollama:qwen3.5` etc.) — stale names (aider, opencode, goose) removed

**Full loop verified:**
- Sent real task through MCP → classified `code_generation` → all 3 arms scored via per-bucket UCB → `qwen3.5` picked → ran → reward 0.8001 → episode #251 in DB
- Arms start with identical UCB=2.623 (equal cold-start priors — correct, pure exploration)

## Commits this session

- `c6c9a19` — cleanup team, v2 restructure (previous session, already committed)
- `2320ed1` — feat(routing): switch default strategy to linucb_per_bucket

## Decision

Gamma is deferred. With 1 real data point, there are no prediction error EMAs to adapt from. Next is `orch benchmark lab` to give the bandit real per-bucket signal before any further refinement.
