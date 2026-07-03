# Current State — 2026-07-03

## What Mahoraga is right now

A working local-first orchestrator with per-bucket bandit routing, episodic memory (semantic mode default), and a persistent background daemon. The full routing loop is verified: task → bucket classification → per-bucket UCB scoring → Ollama arm runs → reward → A/b matrix update → episode in DB.

## Active roster (2 arms, all local)

| Arm | Model | Strengths | Prior |
|---|---|---|---|
| `ollama:qwen3.5` | qwen3.5:latest (6.6 GB, Q4_K_M) | code, reasoning | 0.75 |
| `ollama:granite4.1-8b` | granite4.1:8b (5.3 GB, IBM) | test, review, structured output | 0.75 |

`ollama:gemma4-e4b` disabled 2026-05-23 — lowest reward in every bucket in the 2026-05-20 bench; granite covers the same capability space. Cloud agents (claude, codex-cli, gemini-cli) are registered but effectively disabled — no API keys in env, gated by budget pacer.

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
- State file: `~/.mahoraga-v2/bandit_state.json`
- Decisions DB: `~/.mahoraga-v2/routing_decisions.db` — clean reset 2026-05-20, **207 real decisions since** (111 qwen3.5, 96 granite4.1-8b; last traffic 2026-07-02)
- Backups of pre-reset state at `~/.mahoraga-v2/*.bak`

## Infrastructure

- `orch service install` — launchd daemon, login-persistent, KeepAlive, logs to `~/.mahoraga-v2/server.log`
- `orch serve` — manual start at localhost:8000
- `agents.yaml` — config-driven arm registration; `enabled: false` disables without losing bandit history

## Ollama models (disk)

```
qwen3.5:latest       6.6 GB  ← arm 1
granite4.1:8b        5.3 GB  ← arm 2
nomic-embed-text     274 MB  ← semantic episodic memory
gemma4:e4b           9.6 GB  ← disabled arm, still on disk (rm if space needed)
qwen3:14b            9.3 GB  ← unused by roster; on disk as of 2026-07-03
```

## Next steps (in order)

### 1. ~~Clean stale model~~ ✅ done
### 2. ~~`orch benchmark lab`~~ ✅ done (also found + fixed unexplored-arm UCB inflation bug)

### ~~Let real traffic train the bandit~~ ✅ underway
207 real decisions since the 2026-05-20 reset (111 qwen3.5, 96 granite).
Both arms are past the 20–50 pull warmup threshold — adaptive gamma (§4)
is now unblocked.

### 3. Cross-bucket routing check
Send tasks of different types through the MCP and verify bucket classification:
- code task → `code` bucket → qwen3.5 should edge out granite (0.782 vs 0.776 in bench)
- plan/research task → `plan`/`research` buckets → granite should win (0.874 / 0.833)
- debug task → `debugging` bucket → both compete

### ~~4. Gamma (adaptive per-arm decay)~~ ✅ shipped 2026-07-03
Live in `linucb_per_bucket.py` with per-(bucket, arm) warmup, a
noise-floor-centered mapping, variance floor + outlier cap, EMA-decay
recovery, and w-weighted tracking — several deliberate deviations from
`docs/specs/gamma-spec.md`, all forced by adversarially-verified defects.
**Read `brain/decisions/2026-07-03-adaptive-gamma.md` before touching it.**
Drift ablation (`orch benchmark ablation`, exp 6): adaptive+recovery beats
global γ 11.64 vs 12.85 final regret. Remaining: full sweep grid with
detection/recovery metrics; distance-weighted episodic α (separate spec).

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

## Known issues / lessons

- **Auto-logging to the repo brain is off as of 2026-07-03.** The router was appending a content-free "Routed to X" entry to `brain/decisions/log.md` on every decision (2M lines), and the daemon wrote an empty journal stub on every shutdown. Both call sites removed; SQLite (`routing_decisions.db`) is the only decision log. See ADR `brain/decisions/2026-07-03-remove-brain-auto-append.md`.
- **Never use `--mode force-explore` to seed the bandit.** Force-explore trains some arms and leaves others cold — creates UCB inflation asymmetry. If seeding is needed, use `inject_pseudo_obs` or run bandit mode.
- Cross-bucket routing unverified with real traffic — only tested via routing probe
- `_DEFAULT_PRIORS` equal across all 3 arms (by design, pure cold-start exploration) — will diverge naturally

## What we learned from the bench run (2026-05-20)

Even though the bench data was wiped from the bandit matrices (clean reset), the quality signal is informative:
- **granite4.1-8b** won 6/7 buckets — best avg reward, especially plan (0.874) and research (0.833)
- **qwen3.5** narrowly beat granite on code only (0.782 vs 0.776)
- **gemma4-e4b** underperformed across the board — lowest reward in every bucket
The bandit will rediscover this naturally from real traffic.
