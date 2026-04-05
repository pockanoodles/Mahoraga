# Mahoraga — The Adapting AI Assistant

**Date:** 2026-04-05
**Status:** Approved
**Repo:** github.com/pockanoodles/Mahoraga (new fork, clean history)
**Old repo (Ollama experiments):** github.com/pockanoodles/OlamaSon

---

## Overview

Mahoraga is a self-hosted, multi-channel AI assistant that learns how you work. Talk to it from Telegram (WhatsApp later). It routes tasks intelligently through Claude — Haiku plans, Sonnet executes, Opus escalates — tracks every dollar spent, and adapts to you over time.

Open source. Bring your own Anthropic API key. All state lives on your machine.

**The pitch:** Mahoraga — The Adapting AI That Actually Does Things.

**Target users:** Students, developers, professionals who want a personal AI assistant they control. Filling the gap left by OpenClaw after Anthropic cut off subscription OAuth abuse.

**Key differentiator:** Mahoraga adapts. It learns your preferences, communication style, tool usage patterns, and corrections — and evolves over time. It's not a static wrapper around Claude. It's a system that gets better the more you use it.

---

## Priority Order

1. Fork, strip Ollama/VS Code code, ship clean repo to GitHub
2. Core orchestrator working with Haiku planner / Sonnet executor / Opus escalation
3. Adaptive user model (the differentiator)
4. Web chat UI (default channel — zero config, just open localhost:8000)
5. Telegram channel adapter (opt-in, needs bot token)
6. Tools (web search, URL reader, document reader, code sandbox)
7. Cost tracking & transparency
8. WhatsApp channel (future)
9. Noctis dashboard integration for analytics (future)

---

## Architecture

```
Telegram (→ WhatsApp later)
        │
    ┌───▼───┐
    │Gateway │  FastAPI — routes messages to/from channels
    └───┬───┘
        │
   ┌────▼────┐
   │ Planner  │  Haiku — classifies intent, decomposes tasks, decides routing
   └────┬────┘
        │
  ┌─────▼──────┐
  │  Executor   │  Sonnet — executes tasks, uses tools (→ Opus on escalation)
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Verifier   │  Haiku — scores output 0-10, triggers retry/escalate
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  Adaptive   │  Haiku post-processing — learns from each interaction
  │  User Model │
  └─────┬──────┘
        │
     SQLite (all state local)
```

### What carries over from existing codebase

- Domain model: Mission, Plan, Run, Task, TaskAttempt, Artifact, Event
- Executor state machine (attempt → verify → retry/escalate)
- Capability router concept
- SQLite store (aiosqlite)
- Cost tracking per task
- setup.sh pattern

### What's stripped out

- All Ollama dependencies and planner code (→ OlamaSon repo)
- VS Code extension code
- Old orchestrator version (orchestrator/ directory)
- Ollama warmup logic in server.py

### What's new

- Channel adapter layer (Telegram first)
- Gateway that maps chat messages → Missions
- Haiku planner (replaces Ollama, same interface contract)
- Adaptive user model (persistent per-user learning)
- Tool system (web search, URL reader, docs, code sandbox)
- Clean README and install story

---

## Component Details

### Channel Layer

All channels implement the same adapter interface:

```
Channel Adapter Interface:
  receive(raw_message) → MahoragaMessage
  send(user_id, response) → platform delivery
  handle_media(attachment) → Artifact
```

**Web chat UI (default channel):**
- Static HTML + vanilla JS served by FastAPI from `/static`
- No build step, no npm, no React. Opens at `localhost:8000`
- Streams responses via SSE (Server-Sent Events)
- Zero config — works the moment `setup.sh` finishes
- This is what students use. No bot tokens, no third-party accounts.

**Telegram adapter (opt-in):**
- Uses `aiogram` (async-native, fits FastAPI)
- Bot token via `TELEGRAM_BOT_TOKEN` env var
- Supports text, images, documents, voice messages
- Long polling by default, webhook mode optional
- Only activates if `TELEGRAM_BOT_TOKEN` is set in `.env`

User identity: internal UUID (`user_id`). Channel accounts linked to this ID. Same person on web + Telegram shares one adaptive profile and conversation history.

### Planner (Haiku)

The gatekeeper. Every message hits Haiku first. This is where Mahoraga stays efficient.

**Routing logic:**
- Simple messages (greetings, quick questions) → Haiku responds directly, no Sonnet cost
- Single-step tasks → one task, Sonnet executes
- Complex tasks → task graph decomposition, Sonnet executes each node
- Failed tasks (2 soft retries) → escalate to Opus

**Cost impact:**
```
"hey what's up"                              → Haiku direct   ~$0.001
"summarize this article"                     → Sonnet task    ~$0.01
"research X, compare Y, write report"        → Task graph     ~$0.05
Task fails twice                             → Opus escalate  ~$0.15
```

The planner receives the user's adaptive profile in its system prompt, so the executor already knows preferences without wasting tokens.

Implementation: swap the Ollama HTTP call for `anthropic.messages.create(model="claude-haiku-4-5-20251001")`. Same structured JSON output contract for the task graph.

### Executor (Sonnet → Opus)

Carries over from existing codebase. State machine:

```
PENDING → RUNNING → VERIFYING → COMPLETED
                  ↘ RETRY (soft, up to 2x with feedback)
                  ↘ ESCALATE (Sonnet → Opus)
                  ↘ FAILED (after Opus fails, human gate)
```

Default model: `claude-sonnet-4-6`. Escalation model: `claude-opus-4-6`.

### Verifier (Haiku)

Carries over. Scores task output 0-10.
- Score 7-10: accept, task complete
- Score 4-6: soft retry with feedback (up to 2 attempts)
- Score 0-3: escalate to Opus immediately
- Opus attempt scores below 7: fail with human notification via Telegram

### Adaptive User Model

**What it tracks:**

| Category | Examples |
|---|---|
| Communication style | Short vs. verbose, formal vs. casual, language |
| Tool affinity | Frequently sends PDFs, always asks for web searches |
| Preferences | "bullet points not paragraphs", "respond in Japanese" |
| Task patterns | Recurring requests (weekly summaries on Mondays) |
| Corrections | "No, not like that" → records what went wrong and what they wanted |

**Schema:**

```sql
user_profiles
  user_id         TEXT PRIMARY KEY  -- UUID
  created_at      TIMESTAMP
  updated_at      TIMESTAMP

user_adaptations
  id              INTEGER PRIMARY KEY
  user_id         TEXT REFERENCES user_profiles(user_id)
  category        TEXT  -- style | tool_affinity | preference | pattern | correction
  key             TEXT  -- e.g. "response_length", "preferred_language"
  value           TEXT  -- JSON blob
  confidence      REAL  -- 0.0 to 1.0, decays if not reinforced
  last_reinforced TIMESTAMP
  created_at      TIMESTAMP
```

**Learning loop:**

After every interaction, a Haiku post-processing step reviews the conversation:
- User corrected something → store correction, high confidence
- User stated a preference → store preference, high confidence
- Smooth interaction, no corrections → reinforce existing patterns, bump confidence
- Pattern not reinforced in 30+ days → decay confidence

**Injection:** The planner receives a condensed user profile in its system prompt:

```
User profile:
- Prefers concise responses (3 sentences max)
- Frequently sends PDFs for summarization
- Corrected: don't use bullet points, use prose
- Language: English, casual tone
```

**Confidence decay is key.** People change. Stale preferences fade. Mahoraga doesn't get stuck — it adapts continuously.

### Tools (v1)

Four tools at launch:

| Tool | What it does | Dependency |
|---|---|---|
| Web search | Search + summarize results | Brave Search API or SearXNG |
| URL reader | Fetch and extract content from links | Built-in (httpx + readability) |
| Document reader | Extract text from PDFs, images, files | Built-in |
| Code execution | Sandboxed Python for calculations | Docker (optional) |

Tool interface:

```python
class Tool:
    name: str
    description: str  # planner reads this to decide routing

    async def execute(self, params: dict) -> ToolResult
```

### Cost Tracking

Every Mission records tokens and cost per model tier.

```sql
cost_ledger
  id              INTEGER PRIMARY KEY
  user_id         TEXT
  mission_id      TEXT
  model           TEXT  -- haiku / sonnet / opus
  input_tokens    INTEGER
  output_tokens   INTEGER
  cache_read_tokens INTEGER
  cost_usd        REAL
  created_at      TIMESTAMP
```

**Per-response footer (toggleable):**
```
📊 $0.003 (Haiku: 1.2k tok | Sonnet: 3.4k tok)
```

Users can ask "how much have I spent this week?" — answered from ledger.

**Prompt caching:** System prompts, user profiles, and tool descriptions are structured for Anthropic cache hits. Repeated context costs 90% less. The more you use Mahoraga, the cheaper it gets.

**v2:** Pipe this data into Noctis dashboard for visual analytics.

---

## Installation

**Requirements:**
- Python 3.12+
- Anthropic API key
- Docker (optional, for code sandbox)
- Telegram bot token (optional, for Telegram channel)

**Setup:**
```bash
git clone https://github.com/pockanoodles/Mahoraga.git
cd Mahoraga
cp .env.example .env        # paste API key (Telegram token optional)
./setup.sh                  # installs deps, init DB, starts server
```

Three commands. Open `localhost:8000`. Under 5 minutes from zero to chatting.

Telegram and WhatsApp documented separately as optional channel setups.

**Expected cost:** A typical day of casual usage costs $0.05-0.20 with Haiku-first routing. Most student conversations never leave Haiku.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| API framework | FastAPI + uvicorn |
| Telegram | aiogram |
| Claude SDK | anthropic (official Python SDK) |
| Database | SQLite via aiosqlite |
| HTTP client | httpx |
| CLI | Typer |
| Code sandbox | Docker (optional) |
| Testing | pytest + pytest-asyncio |

---

## Out of Scope (v1)

- WhatsApp channel (v2)
- Calendar / email / file management integrations (v2)
- Noctis dashboard integration (v2)
- Ollama / local model support (separate repo: OlamaSon)
- VS Code extension
- Hosted service / multi-tenant
- User authentication (single-user, local machine)

---

## Success Criteria

- A user can clone the repo, set one env var (API key), run setup.sh, and chat with Mahoraga in their browser in under 5 minutes
- Mahoraga routes simple messages through Haiku only (no unnecessary Sonnet calls)
- After 10+ interactions, the adaptive model noticeably influences responses
- Cost per casual conversation stays under $0.01
- The README tells the story: what it is, why it exists, how to set it up
