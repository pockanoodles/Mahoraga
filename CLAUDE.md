# Mahoraga — CLAUDE.md

## Project
Agent-agnostic LLM orchestration framework with online bandit routing. FastAPI backend, Python 3.12, vanilla HTML/CSS/JS frontend. GitHub: pockanoodles/Mahoraga

**Stack:** Python 3.12, FastAPI, aiosqlite, anthropic SDK, httpx  
**Trunk:** `main` — trunk-based flow: short-lived `feat/`/`fix/`/`chore/` branches → PR → CI (`pytest -m "not slow"`) → merge. Releases are tags (`v2.0`, …), not branches. Semantic-augmented routing spec: `docs/specs/semantic-routing.md`.  
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
- 9-dimensional context vector (`routing/context.py`); feature 9 (`queue_depth_norm`) is live pool depth from ExecutionPool
- dLinUCB (γ=0.98): discounted updates in `routing/strategies/linucb.py` `update()`
- Composite reward: success/quality/speed/cost (per-bucket weights, learnable via OLS)
- Spawn penalty fires when `agent_spawn_time_ms > 500`
- State: `~/.mahoraga-v2/bandit_state.json` (bandit), `~/.mahoraga-v2/routing_decisions.db` (SQLite log)

## Running
- `orch serve` — backend at localhost:8000
- `pytest` — run tests
- `orch benchmark simulate` — strategy comparison (200 synthetic tasks)
- `orch bench run --mode force-explore --prompts <bank.jsonl> --agents <roster>` — forced round-robin live batch

## Agents
Active (2 arms, local only): ollama:qwen3.5 (9.7B Q4_K_M, code/reasoning; also the escalation judge), ollama:granite4.1-8b (IBM, test/review/structured output)  
Disabled in agents.yaml: qwen3-14b (dropped 2026-07-26 — Phase 4 bench put it mid-pack behind granite at ~2× the RAM), gemma4-e4b (lowest reward in every bucket, bench 2026-05-20), deepseek-r1 (unblocks at 32 GB), lfm2, claude, claude-cli (Phase 4 cost-bench arm — runs the `claude` CLI on Max-subscription auth, reports real cost; enable only for head-to-head bench runs), codex, gemini, aider, opencode, goose  
Roster source of truth: `agents.yaml`; current snapshot in `brain/state/current_state.md`

## Hardware
MacBook Pro (Nov 2024), M-series, 16 GB unified memory. Qwen3.5 9.7B Q4_K_M at ~30 t/s on Apple Silicon.

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
