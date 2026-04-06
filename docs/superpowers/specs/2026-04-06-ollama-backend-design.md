# Ollama Backend + Multi-Model Routing

**Date:** 2026-04-06
**Status:** Approved
**Feature:** Ollama as a free local backend, toggled alongside Claude in the UI

---

## Overview

Add Ollama as a fully supported backend. Users toggle between Claude and Ollama from the chat header. The system automatically routes each task to the right model — no user configuration needed. Settings drawer shows the routing table read-only.

**Why:** Students and users without an Anthropic API key need a free path. Ollama runs entirely locally, no network calls, no billing.

---

## Hardware Target

16GB unified memory MacBook Pro. ~11GB usable after macOS overhead. One model loaded at a time — swapping is acceptable between turns, not within a turn.

---

## Model Stack (3 models)

| worker_id | model | size | role |
|-----------|-------|------|------|
| `ollama:planner` | `qwen3.5:2b` | ~1.5GB | Task decomposition — stays warm |
| `ollama:fast` | `qwen3.5:2b` | ~1.5GB | Simple Q&A — same model as planner, different system prompt |
| `ollama:coder` | `qwen2.5-coder:7b` | ~4.7GB | Code generation, debugging, refactoring |
| `ollama:general` | `qwen3.5:9b` | ~6.6GB | Writing, reasoning, analysis, everything else |

`ollama:planner` and `ollama:fast` share the same underlying model — no swap between planning and fast tasks. One swap max per request: either coder or general loads, never both.

---

## Architecture

### New files

**`backend/orchestrator/workers/ollama.py`** — `OllamaWorker`
- `__init__(self, model: str, system_prompt: str, base_url: str)`
- `async def run(task, context) -> AsyncIterator[str]` — streams from `POST /api/chat` with `"stream": true`, yields `message.content` chunks
- `async def health_check() -> bool` — `GET /api/tags`, confirms model is available
- worker_id pattern: `ollama:<role>` (e.g. `ollama:coder`)

**`backend/orchestrator/workers/router.py`** — `TaskRouter`
- `route(task: Task, backend: str) -> str` — returns worker_id
- Pure keyword heuristic, no LLM call

**`backend/orchestrator/config.py`** — thin config layer
- Reads/writes `~/.mahoraga/config.json`
- Keys: `active_backend` (default: `"claude"`), `ollama_base_url` (default: `"http://localhost:11434"`)
- `get(key)`, `set(key, value)`, loaded once at startup

### Modified files

- `backend/orchestrator/workers/registry.py` — register all 4 Ollama workers at startup
- `backend/orchestrator/gateway.py` — read `active_backend` from config, pass to planner + router
- `backend/orchestrator/service/app.py` — add `GET /settings/backend`, `POST /settings/backend`
- `static/app.js` — backend toggle chip in chat header
- `static/settings.js` — updated drawer with Claude/Ollama sections

---

## Routing Logic

### Claude (unchanged)
- Planning step → `claude:haiku`
- All execution → `claude:sonnet`

### Ollama (keyword heuristic)

| Condition | Worker |
|-----------|--------|
| Planning step | `ollama:planner` |
| Task ≤ 8 words, or "what is / define / how many" | `ollama:fast` |
| "code / function / implement / debug / refactor / script / class / test / fix / bug / API / import" | `ollama:coder` |
| Everything else | `ollama:general` |

System prompts per role are defined as constants in `router.py`. The worker receives task + the resolved system prompt.

---

## Config Persistence

`~/.mahoraga/config.json`:
```json
{
  "active_backend": "ollama",
  "ollama_base_url": "http://localhost:11434"
}
```

Two new endpoints:
- `GET /settings/backend` → `{"active_backend": "ollama", "ollama_base_url": "..."}`
- `POST /settings/backend` → body `{"active_backend": "claude"}`, persists and returns updated config

Gateway reads `active_backend` on every request — no restart required to switch backends.

---

## UI

### Chat header toggle chip
Sits between the title and the gear icon:

```
Mahoraga          [Claude ▾]  [⚙]
```

- Click toggles Claude ↔ Ollama, calls `POST /settings/backend`
- Chip label updates immediately
- Pill style: accent background when Claude, muted when Ollama (or vice versa)

### Settings drawer (read-only, three sections)

```
BACKEND
  Active: Claude   [toggle]

CLAUDE
  API Key:   sk-an••••1234
  Planner:   claude-haiku
  Executor:  claude-sonnet

OLLAMA
  URL: http://localhost:11434

  ROUTING TABLE
  planner  →  qwen3.5:2b
  fast     →  qwen3.5:2b
  coder    →  qwen2.5-coder:7b
  general  →  qwen3.5:9b
```

Everything read-only. No inputs, no save buttons. API key editing is a future task.

---

## Error Handling

- Ollama not running → `health_check()` fails at startup, gateway logs warning, requests return a clear error message in the chat ("Ollama is not running at localhost:11434")
- Model not pulled → same path, message tells user to run `ollama pull <model>`
- Mid-stream failure → caught in `OllamaWorker.run()`, yields `[ERROR] ...` sentinel (same as ClaudeWorker)

---

## Out of Scope

- API key editing in the UI
- Per-conversation backend override
- Hardware profile tiers (constrained/standard/full)
- Model auto-pull on first use
- Ollama model selector (user picks from installed models)
