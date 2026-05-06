# Mahoraga — CLAUDE.md

## Project
Agent-agnostic LLM orchestration framework with online bandit routing. FastAPI backend, Python 3.12, vanilla HTML/CSS/JS frontend. GitHub: pockanoodles/Mahoraga

**Stack:** Python 3.12, FastAPI, aiosqlite, anthropic SDK, httpx  
**Active branch:** `v2` (semantic-augmented routing — see `docs/semantic-routing.md`). v1 is frozen on `main`.  
**Tests:** `pytest` from project root

## Repo Layout
- `backend/orchestrator/adapters/` — AgentAdapter shims (registration, capabilities, health checks)
- `backend/orchestrator/workers/` — Worker implementations (actual task execution via subprocess/API)
- `backend/orchestrator/routing/` — LinUCB bandit, quality scoring, reward calc, episodic memory, decision log
- `backend/orchestrator/service/` — FastAPI app and endpoints
- `backend/orchestrator/verifier/` — Output verification / retry gating
- `backend/orchestrator/planning/` — Task classifier and planner
- `backend/orchestrator/domain/` — Data models (Task, TaskAttempt, Mission, Run)
- `frontend/` — Vanilla JS/HTML frontend
- `tests/` — pytest suite

## Key Architecture
- Two-stage routing: keyword classifier → capability bucket → LinUCB bandit picks agent within bucket
- 9-dimensional context vector (`routing/context.py`); feature 9 (`queue_depth_norm`) is always 0.0 (reserved)
- dLinUCB (γ=0.98): discounted updates in `routing/strategies/linucb.py` `update()`
- Composite reward: success/quality/speed/cost (per-bucket weights, learnable via OLS)
- Spawn penalty fires when `agent_spawn_time_ms > 500`
- State: `~/.mahoraga-v2/bandit_state.json` (bandit), `~/.mahoraga-v2/routing_decisions.db` (SQLite log)

## Running
- `orch serve` — backend at localhost:8000
- `pytest` — run tests
- `orch benchmark simulate` — strategy comparison (200 synthetic tasks)
- `orch benchmark lab` — forced round-robin with quality scoring (8 agents × 24 prompts)

## Agents
ollama:qwen3-4b (local), ollama:gemma4-e4b, ollama:lfm2, ollama:deepseek-r1, codex-cli, aider, gemini-cli, goose, opencode, claude (escalation only)

## Hardware
MacBook Pro (Nov 2024), M-series, 16 GB unified memory. Qwen3 4B Q4_K_M at 33.8 t/s, LFM2 at 77.1 t/s.

## Brain / Journal

The repo-local brain lives at `brain/` in the project root. After completing a major feature, making an architectural decision, or hitting a significant tradeoff — write to the brain.

**For session journals:** write a new file at `brain/journal/YYYY-MM-DD-<slug>.md` using the format in the spec.

**For architecture decisions:** write a new file at `brain/decisions/YYYY-MM-DD-<title>.md` using the ADR format.

**For state changes:** update `brain/state/current_state.md`.

Do this once per meaningful chunk of work, not per file edit. The goal is a searchable record of *why* decisions were made, not a commit log.

## Brain Capture (Automatic)

At the start of every conversation, call `mcp__obsidian-brain__get_session_briefing` silently to load context from the Obsidian vault. Mention it only if something directly relevant surfaces.

During conversation, proactively call obrain MCP tools when:
- **Decision** (architecture, routing, tradeoff) → `mcp__obsidian-brain__auto_file` with `context="decision"`
- **Idea / design / concept** → `mcp__obsidian-brain__auto_file` with `context="concept"`
- **End of meaningful work chunk** → `mcp__obsidian-brain__write_journal` with a summary
- **Routine notable exchange** → `mcp__obsidian-brain__append_to_note` on today's daily note (one-liner)

Filter rule: decision = would go in a commit message or ADR. Concept = something you'd want to find in 3 months. Everything else = one-liner on daily note.

On demand: when the user references past work, call `mcp__obsidian-brain__search_brain` and surface the result inline.
