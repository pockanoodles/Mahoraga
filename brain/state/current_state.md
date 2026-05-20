# Current State — 2026-05-20

## What Mahoraga is right now

A working local-first orchestrator with per-bucket bandit routing, episodic memory (semantic mode default), and a persistent background daemon. The full routing loop is verified: task → bucket classification → per-bucket UCB scoring → Ollama arm runs → reward → A/b matrix update → episode in DB.

## Active roster (3 arms, all local)

| Arm | Model | Strengths | Prior |
|---|---|---|---|
| `ollama:qwen3.5` | qwen3.5:latest (6.6 GB, Q4_K_M) | code, reasoning | 0.75 |
| `ollama:gemma4-e4b` | gemma4:e4b (9.6 GB) | plan, research, general | 0.75 |
| `ollama:granite4.1-8b` | granite4.1:8b (5.3 GB, IBM) | test, review, structured output | 0.75 |

Cloud agents (claude, codex-cli, gemini-cli) are registered but effectively disabled — no API keys in env, gated by budget pacer.

## Architecture shape

```
User (Claude Code + /mahoraga skill)
    ↓ MCP
FastAPI (orch serve — runs as launchd daemon, always on)
    ↓
BanditRouter
    ├── classify_bucket(context) → bucket label
    ├── LinUCBPerBucketRouter.select_agent() → UCB pick from 3 arms
    ├── episodic memory (semantic mode, nomic-embed-text)
    └── quality scoring → composite reward → A[bucket][agent] update
    ↓
OllamaWorker (subprocess call to localhost:11434)
    ↓
routing_decisions.db (SQLite, ~/.mahoraga-v2/)
```

## Bandit state

- Strategy: `linucb_per_bucket` (per-bucket disjoint A/b matrices, γ=0.98 global decay)
- State file: `~/.mahoraga-v2/bandit_state.json` — starts fresh (no prior state)
- Real traffic: 1 task as of 2026-05-20 (decision #251, `code_generation` bucket, reward 0.8001)
- Synthetic benchmark data: 250 decisions in DB from prior simulation runs

## Infrastructure

- `orch service install` — launchd daemon, login-persistent, KeepAlive, logs to `~/.mahoraga-v2/server.log`
- `orch serve` — manual start at localhost:8000
- `agents.yaml` — config-driven arm registration; `enabled: false` disables without losing bandit history

## Ollama models (disk)

```
qwen3.5:latest       6.6 GB  ← arm 1
gemma4:e4b           9.6 GB  ← arm 2
granite4.1:8b        5.3 GB  ← arm 3
nomic-embed-text     274 MB  ← semantic episodic memory
qwen3:14b            9.3 GB  ← STALE, remove with: ollama rm qwen3:14b
```

## Next steps (in order)

### 1. Clean stale model
```bash
ollama rm qwen3:14b
```

### 2. `orch benchmark lab` — feed the bandit real data
Forces all 3 arms through real Ollama calls with quality scoring.
Without this, the bandit has 1 real data point and can't differentiate arms.
Run from project root:
```bash
orch benchmark lab
```
Target: ~72 real observations (3 arms × 24 prompts). After this the
exploit/explore scores will diverge from the cold-start uniform UCB=2.623.

### 3. Cross-bucket routing check
Send tasks of different types through the MCP and verify bucket classification:
- research task → `research` bucket → gemma4-e4b should win
- debug task → `debugging` bucket → granite should compete
- plan task → `complex`/`plan` bucket → gemma4-e4b should win

### 4. Gamma (adaptive per-arm decay)
Once arms have 20–50 pulls each, prediction error EMAs are meaningful.
Gamma makes each arm's decay rate proportional to how wrong its predictions
have been — fast-forgetting for mis-calibrated arms, slower for stable ones.

**Spec:** `γ_a,t = γ_min + (γ_max − γ_min) · exp(−E_a,t / τ)`
where `E_a,t` = prediction error EMA for arm `a` at time `t`.

Implementation targets:
- `backend/orchestrator/routing/strategies/linucb_per_bucket.py` `update()`
- Add `pred_error_ema: dict[str, float]` and `gamma_per_arm: dict[str, float]`
- Warmup guard: skip adaptive γ until arm has ≥10 pulls
- Persist `pred_error_ema` and `gamma_per_arm` in `save_state`/`load_state`

### 5. Semantic retrieval validation
Semantic episodic memory is wired as default (`MEMORY_MODE_SEMANTIC`) but
never verified against our 3-arm roster. After benchmark lab run:
- Check `_retrieve_memory_biases_rich()` is calling nomic-embed-text
- Verify episodic memory is growing (`.bin` file size increasing)
- Compare routing quality with `MAHORAGA_MEMORY_MODE=keyword` vs default

## Key files

| File | What it does |
|---|---|
| `backend/orchestrator/service/app.py:207` | Strategy initialization (linucb_per_bucket) |
| `backend/orchestrator/routing/strategies/linucb_per_bucket.py` | The v2 bandit |
| `backend/orchestrator/routing/bandit_router.py` | Full routing loop, memory, escalation |
| `backend/orchestrator/routing/strategies/static.py` | `classify_bucket()` — bucket labels |
| `backend/orchestrator/adapters/loader.py` | agents.yaml → adapter + worker registration |
| `agents.yaml` | Arm roster, capabilities, priors |
| `backend/orchestrator/cli/commands/service.py` | launchd daemon management |
| `~/.mahoraga-v2/routing_decisions.db` | All routing decisions + rewards |
| `~/.mahoraga-v2/bandit_state.json` | Persisted A/b matrices per bucket |

## Known issues

- `qwen3:14b` (9.3 GB) still in Ollama — not in roster, wastes disk
- Cross-bucket routing unverified with real traffic — only `code_generation` tested
- `_DEFAULT_PRIORS` equal across all 3 arms (by design, for pure cold-start exploration) — will diverge naturally with data
