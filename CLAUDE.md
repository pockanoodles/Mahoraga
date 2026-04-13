# Mahoraga — CLAUDE.md

## Project
Self-hosted multi-channel AI assistant. Haiku plans, Sonnet executes, Opus escalates. Adaptive user model. Web chat + Telegram.

**Stack:** Python 3.12, FastAPI, aiosqlite, anthropic SDK, aiogram, httpx  
**Active branch:** `feat/orchestrator-domain-store`  
**Tests:** `pytest` from project root

## Brain / Journal

After completing a major feature, making an architectural decision, or hitting a significant tradeoff — call `write_journal` (Obsidian MCP) to file it into the Brain.

Entry format:
- **Title:** `"mahoraga: <what you did>"` — e.g. `"mahoraga: wired adaptive model into planner"`
- **Body:** what changed, why that approach, what alternatives were rejected

Do this once per meaningful chunk of work, not per file edit. The goal is a searchable record of *why* decisions were made, not a commit log.
