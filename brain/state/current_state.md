# Current State

## What Mahoraga is right now

A working local-first orchestrator with adaptive bandit routing, episodic memory, OLS weight learning, an MCP server, and parallel batch execution. All routing infrastructure is complete and tested (101 tests passing as of 2026-04-14).

Workers registered: Claude (Haiku/Sonnet/Opus), Ollama, OpenCode, Gemini CLI, Goose.

## Current priorities

1. Add the repo-local brain layer (this folder — in progress)
2. Wire brain_logger.py to write to this repo's brain/journal/ (not ~/Brain/)
3. Populate initial brain documents from accumulated project knowledge
4. Decide on session resume flow (how context from brain/ gets injected into tasks)

## Active workstreams

- **Brain layer** — adding repo-local markdown brain per the memory architecture spec
- **obrain** — separate personal brain project renamed from obsidian-brain, independent of Mahoraga

## Architecture shape

```
User input
    ↓
Gateway (FastAPI)
    ↓
Planner (Haiku)
    ↓
BanditRouter (LinUCB + Thompson + UCB1 + OLS weights + episodic memory)
    ↓
WaveExecutor (parallel) / single dispatch
    ↓
Worker (Ollama / OpenCode / Gemini CLI / Goose / Claude)
    ↓
Verifier → reward signal → bandit update
    ↓
brain_logger → brain/journal/
```

## Known constraints

- v1 targets 16 GB Mac; memory-hungry models need warm/cold state tracking
- No fine-tuning or weight updates — all learning is at the orchestration layer

## Open questions

- Session resume: what's the best trigger for injecting brain/state/ context into a task prompt?
- Retrieval: simple file-read from brain/ or something more structured?

## Next recommended tasks

1. Update brain_logger.py to write to brain/journal/ in this repo
2. Write first benchmark conclusions note from existing bandit test data
3. Commit brain/ to the repo as part of the project
