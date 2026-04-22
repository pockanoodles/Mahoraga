# Brain Capture Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-22-brain-capture-design.md`  
**Goal:** Automatic brain capture from Claude Code sessions (→ Obsidian) and Mahoraga task outputs (→ repo brain/).

---

## Parallelism

Three independent tasks — dispatch as parallel agents:

| Agent | Files touched | Depends on |
|-------|--------------|------------|
| A — Claude Code pipeline | `CLAUDE.md`, `~/.claude/settings.json` | nothing |
| B — Gateway + lifespan | `backend/orchestrator/gateway.py`, `backend/orchestrator/service/app.py` | nothing |
| C — BanditRouter brain write | `backend/orchestrator/routing/bandit_router.py` | nothing |

---

## Task A: Claude Code pipeline

### A1 — CLAUDE.md brain capture instructions

Edit `/Users/kaitosoeno/Projects/Mahoraga/CLAUDE.md`.

Add a new **Brain Capture** section after the existing `## Brain / Journal` section:

```markdown
## Brain Capture (Automatic)

At the start of every conversation, call `mcp__obsidian-brain__get_session_briefing` silently to load context from the Obsidian vault. Mention it only if something directly relevant surfaces.

During conversation, proactively call obrain MCP tools when:
- **Decision** (architecture, routing, tradeoff) → `mcp__obsidian-brain__auto_file` with `context="decision"`
- **Idea / design / concept** → `mcp__obsidian-brain__auto_file` with `context="concept"`
- **End of meaningful work chunk** → `mcp__obsidian-brain__write_journal` with a summary
- **Routine notable exchange** → `mcp__obsidian-brain__append_to_note` on today's daily note (one-liner)

Filter rule: decision = would go in a commit message or ADR. Concept = something you'd want to find in 3 months. Everything else = one-liner on daily note.

On demand: when the user references past work, call `mcp__obsidian-brain__search_brain` and surface the result inline.
```

### A2 — Stop hook in ~/.claude/settings.json

Read `~/.claude/settings.json` first. Add a `Stop` hook entry that appends a session-ended marker to today's Obsidian daily note.

The hook command:
```
mkdir -p ~/Brain/journal && printf '\n## Session ended — %s\n' "$(date '+%H:%M')" >> ~/Brain/journal/$(date '+%Y-%m-%d').md
```

Add under `hooks.Stop` as a new entry with empty `matcher`. Preserve all existing settings.

---

## Task B: Gateway duration/quality + lifespan shutdown

### B1 — gateway.py: pass duration and quality to log_task_completion

File: `backend/orchestrator/gateway.py`

Current call (around line 193):
```python
log_task_completion(
    task_title=task.title or mission.title,
    task_goal=task.goal or "",
    agent_used=attempt.worker_id or "unknown",
    output_preview=output[:500] if output else "",
    cost=0.0,
)
```

Replace with:
```python
_duration = None
if attempt.started_at and attempt.ended_at:
    _duration = attempt.ended_at - attempt.started_at
_quality = 1.0 if attempt.status.value == "completed" else 0.0
log_task_completion(
    task_title=task.title or mission.title,
    task_goal=task.goal or "",
    agent_used=attempt.worker_id or "unknown",
    output_preview=output[:500] if output else "",
    cost=0.0,
    quality_score=_quality,
    duration_seconds=_duration,
)
```

No new imports needed — `log_task_completion` already accepts these kwargs.

### B2 — app.py: log_session_summary on shutdown

File: `backend/orchestrator/service/app.py`

1. Add import at top with the other brain_logger imports (or add new import):
```python
from ..brain_logger import log_session_summary
```

2. Find the lifespan function (around line 135). It has an `async with` or `yield` pattern. Add after the `yield` (shutdown section):
```python
try:
    log_session_summary(notes="Mahoraga backend shutdown")
except Exception:
    pass
```

---

## Task C: BanditRouter → brain_logger.log_decision()

File: `backend/orchestrator/routing/bandit_router.py`

The router already calls `self.logger.log_decision()` (SQLite DecisionLogger). We need to ALSO call `brain_logger.log_decision()` to write to `brain/decisions/log.md`.

1. Add import near top of file:
```python
from ..brain_logger import log_decision as brain_log_decision
```

2. Find the `self.logger.log_decision(...)` call (around line 162). Immediately after it, add:
```python
try:
    brain_log_decision(
        decision=f"Routed to {selected.worker_id}",
        reasoning=f"strategy={self.strategy.__class__.__name__}",
        context="mahoraga-router",
    )
except Exception:
    pass
```

Note: check what variable holds the selected worker at that point in the code — it may be `selected`, `agent`, or similar. Read the surrounding context before editing.

---

## Verification

After all three tasks:

- [ ] `pytest` passes from project root
- [ ] `CLAUDE.md` has Brain Capture section
- [ ] `~/.claude/settings.json` has Stop hook
- [ ] `gateway.py` passes duration + quality to `log_task_completion`
- [ ] `app.py` calls `log_session_summary` on shutdown
- [ ] `bandit_router.py` calls `brain_log_decision` after routing
