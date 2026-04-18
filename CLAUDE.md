# Mahoraga — CLAUDE.md

## Project
Self-hosted multi-channel AI assistant. Haiku plans, Sonnet executes, Opus escalates. Adaptive user model. Web chat + Telegram.

**Stack:** Python 3.12, FastAPI, aiosqlite, anthropic SDK, aiogram, httpx  
**Active branch:** `personal`  
**Tests:** `pytest` from project root

## Brain / Journal

The repo-local brain lives at `brain/` in the project root. After completing a major feature, making an architectural decision, or hitting a significant tradeoff — write to the brain.

**For session journals:** write a new file at `brain/journal/YYYY-MM-DD-<slug>.md` using the format in the spec.

**For architecture decisions:** write a new file at `brain/decisions/YYYY-MM-DD-<title>.md` using the ADR format.

**For state changes:** update `brain/state/current_state.md`.

Do this once per meaningful chunk of work, not per file edit. The goal is a searchable record of *why* decisions were made, not a commit log.
