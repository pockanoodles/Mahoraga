# Brain Capture Design

**Date:** 2026-04-22  
**Status:** Approved

## Overview

Automatic capture of everything meaningful from Claude Code conversations and Mahoraga task outputs into a persistent second brain. Two pipelines, two targets, one purpose: rich context that compounds over time.

## Goals

- Every important idea, decision, and design from Claude Code sessions lands in Obsidian automatically
- Mahoraga task completions and routing decisions land in the repo-local brain with real metrics
- Claude reads past context at session start without being asked
- Obsidian becomes a queryable knowledge graph — concepts linked, decisions traceable, progress visible

## Non-Goals

- Networked brain for mini PC / remote Mahoraga (separate spec)
- LLM-powered summarization in stop hooks (zero extra credits)
- Writing Mahoraga backend data to Obsidian (Obsidian is personal)

---

## Architecture

Two independent pipelines:

```
Claude Code conversation
  ├── Session start  → get_session_briefing (obrain MCP, silent)
  ├── Mid-session    → auto_file / write_journal / append_to_note (proactive)
  └── Stop hook      → shell script appends "Session ended HH:MM" to daily note

Mahoraga backend (pure Python, zero LLM, zero credits)
  ├── Task completion → log_task_completion() [cost + quality + duration fixed]
  ├── Routing decision → log_decision() [called from BanditRouter]
  └── Shutdown        → log_session_summary() [FastAPI lifespan shutdown]
       └── writes to brain/journal/ (repo-local, ships with OSS)
```

---

## Pipeline 1: Claude Code → Obsidian Vault

### Session Start

A CLAUDE.md instruction directs me to call `get_session_briefing` at the start of each conversation. I load it silently. If something relevant surfaces, I mention it in one line. Otherwise invisible.

(No hook is used here — Claude Code has no session-start hook event. CLAUDE.md instructions are the right mechanism for proactive per-session behavior.)

### Mid-Session Proactive Writes

I call obrain MCP tools when any of these emerge during conversation:

| Trigger | Tool | Destination |
|---------|------|-------------|
| Architecture/routing decision | `auto_file(context="decision")` | `decisions/` |
| Idea / design / concept | `auto_file(context="concept")` | `concepts/` |
| End of meaningful work chunk | `write_journal(title, content)` | `journal/YYYY-MM-DD.md` |
| Routine exchange worth noting | `append_to_note` on daily note | `journal/YYYY-MM-DD.md` |

**Filter rule:**
- Would it go in a commit message or ADR? → decision
- Would you want to find it in 3 months? → concept  
- Everything else → one-liner on daily note

### Stop Hook (Safety Net)

A shell-only stop hook in `settings.json` appends a session-ended marker to today's daily note. No LLM. Pure `date` + file append.

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "mkdir -p ~/Brain/journal && printf '\\n## Session ended — %s\\n' \"$(date '+%H:%M')\" >> ~/Brain/journal/$(date '+%Y-%m-%d').md"
      }]
    }]
  }
}
```

### Obsidian Vault Structure

```
~/Brain/
  journal/YYYY-MM-DD.md     ← daily note, links to everything from that day
  decisions/                ← architecture and routing decisions
  concepts/                 ← ideas, design patterns, mental models
  conversations/            ← session summaries
  inbox/                    ← unclassified, processed later
```

Daily note format:
```markdown
# 2026-04-22

## Sessions
- 14:32 — Brain capture design ([[decisions/brain-capture-design]])
- 15:10 — Mahoraga routing refactor ([[concepts/bandit-routing]])

## Session ended — 15:47
```

### Context Retrieval

- **Session start:** `get_session_briefing` auto-loaded (lightweight, silent)
- **On demand:** when you reference past work, I call `search_brain(query)` and surface the result inline

---

## Pipeline 2: Mahoraga → Repo Brain

### What's Broken Today

- `cost` hardcoded to `0.0` in `gateway.py` — never calculated
- `quality_score` and `duration_seconds` never passed to `log_task_completion()`
- `log_decision()` exists but never called — BanditRouter makes decisions silently
- `log_session_summary()` exists but nothing triggers it

### Fixes

**`gateway.py`** — measure duration, pass real values:

```python
start_time = time.monotonic()
# ... task execution ...
duration = time.monotonic() - start_time
log_task_completion(
    task_title=task.title or mission.title,
    task_goal=task.goal or "",
    agent_used=attempt.worker_id or "unknown",
    output_preview=output[:500] if output else "",
    cost=attempt.cost_usd or 0.0,
    quality_score=attempt.quality,
    duration_seconds=duration,
)
```

**`BanditRouter`** — log every routing decision:

```python
log_decision(
    decision=f"Routed to {selected_worker}",
    reasoning=f"score={score:.3f}, capability={capability}",
    context="mahoraga-router",
)
```

**FastAPI lifespan** — log session summary on shutdown:

```python
@asynccontextmanager
async def lifespan(app):
    yield
    log_session_summary(
        tasks_completed=registry.total_tasks,
        total_cost=registry.total_cost,
        agents_used=registry.agents_used,
    )
```

**`Attempt` model** — add missing fields:

```python
@dataclass
class Attempt:
    ...
    cost_usd: float = 0.0
    quality: float | None = None
```

### Output Format (repo brain)

```markdown
## 2026-04-22 14:32 — summarize text
- Agent: haiku | Cost: $0.0012 | Duration: 1.4s | Quality: 0.87
- Output: The text discusses...
```

### Retrieval

File I/O only, no LLM. When Mahoraga needs historical context (benchmarks, agent performance, past decisions), it reads `brain/` directly.

---

## Out of Scope (Future)

- Networked obrain accessible from mini PC running local LLMs
- External storage for Obsidian vault
- Cross-device brain sync
