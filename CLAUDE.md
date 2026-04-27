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

## Brain Capture (Automatic)

At the start of every conversation, call `mcp__obsidian-brain__get_session_briefing` silently to load context from the Obsidian vault. Mention it only if something directly relevant surfaces.

During conversation, proactively call obrain MCP tools when:
- **Decision** (architecture, routing, tradeoff) → `mcp__obsidian-brain__auto_file` with `context="decision"`
- **Idea / design / concept** → `mcp__obsidian-brain__auto_file` with `context="concept"`
- **End of meaningful work chunk** → `mcp__obsidian-brain__write_journal` with a summary
- **Routine notable exchange** → `mcp__obsidian-brain__append_to_note` on today's daily note (one-liner)

Filter rule: decision = would go in a commit message or ADR. Concept = something you'd want to find in 3 months. Everything else = one-liner on daily note.

On demand: when the user references past work, call `mcp__obsidian-brain__search_brain` and surface the result inline.
