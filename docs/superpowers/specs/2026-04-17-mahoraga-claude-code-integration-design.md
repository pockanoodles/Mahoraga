# Mahoraga ↔ Claude Code Integration + Positioning Rewrite

**Date:** 2026-04-17  
**Status:** Approved  
**Scope:** MCP registration, skill-based toggle, PostToolUse hook, routing tiers, README rewrite

---

## Problem

Claude Code currently does all subagent and mechanical work using Sonnet, burning API credits for tasks that open source local models can handle. Mahoraga has a working MCP server, a bandit router, and registered workers (Ollama/Qwen, OpenCode, Gemini CLI, Goose) but is not wired into Claude Code's workflow. The integration is manual and forgettable.

---

## Goals

1. Register Mahoraga's MCP server with Claude Code
2. Give the user a skill-based toggle (`/mahoraga`) to route subtasks through Mahoraga on demand
3. Make the toggle sticky mid-session: a PostToolUse hook on the Skill tool reinjects routing context after every skill load
4. Define firm task-type boundaries so quality is preserved
5. Rewrite README to lead with research engine + routing math, not ease of use

---

## Non-Goals

- Automatic always-on routing (user wants to choose when to budget)
- Modifying Mahoraga's backend routing logic
- Brain layer integration (deferred)

---

## Architecture

```
User invokes /mahoraga
        ↓
Skill flips ~/.claude/mahoraga-active flag
        ↓
User asks Claude Code to do work
        ↓
Claude Code loads a skill (e.g. /research, /debug)
        ↓
PostToolUse hook fires on Skill tool call
        ↓
Hook checks flag → if active, injects routing reminder into context
        ↓
Claude Code routes subtasks via mcp__mahoraga__run_task or run_batch
        ↓
Mahoraga bandit picks worker (Qwen / OpenCode / Gemini CLI / Goose)
        ↓
Result returned to Claude Code for review/integration
```

---

## Components

### 1. MCP Registration

**File:** `~/.claude/settings.json`  
**Change:** Add `mcpServers` block

```json
"mcpServers": {
  "mahoraga": {
    "command": "python",
    "args": ["-m", "backend.mcp.server"],
    "cwd": "/Users/kaitosoeno/Projects/Mahoraga"
  }
}
```

Requires Mahoraga backend running at `localhost:8000`. The MCP server is a stdio bridge — no backend, no tools. The 7 tools (`run_task`, `run_batch`, `route_task`, `agent_status`, `routing_stats`, `switch_strategy`, `recent_decisions`) become available in every Claude Code session.

---

### 2. Toggle Skill

**File:** `~/.claude/plugins/skills/mahoraga.md` (verify exact path during implementation — use `/skill-builder` or check where existing user skills are stored)  
**Invocation:** `/mahoraga`

Behavior:
- Read `~/.claude/mahoraga-active`
- Flip the flag (create if missing = on, delete if present = off)
- Report new state clearly
- When ON: skill content includes routing rules so they're loaded into context immediately

The flag is global (not project-scoped) so it persists across sessions and projects.

---

### 3. PostToolUse Hook

**Script:** `~/.claude/scripts/mahoraga-routing.sh`  
**Trigger:** PostToolUse on `Skill` tool

```bash
#!/bin/bash
FLAG="$HOME/.claude/mahoraga-active"
[ -f "$FLAG" ] || exit 0

cat <<'EOF'
[Mahoraga ACTIVE] Route subtasks through Mahoraga, not Agent tool:
- File creation / boilerplate / formatting → run_task (Ollama/Qwen, $0)
- Structured code with clear spec → run_task (OpenCode/Goose)
- Research / search / summarization → run_task (Gemini CLI)
- 3+ independent subtasks → run_batch
- Architecture / review / complex reasoning → handle directly in Claude
EOF
```

Hook registration in `~/.claude/settings.json` — add to the existing `PostToolUse` array alongside the current `Edit|Write|Bash` and `Agent` entries:
```json
{
  "matcher": "Skill",
  "hooks": [
    {
      "type": "command",
      "command": "bash /Users/kaitosoeno/.claude/scripts/mahoraga-routing.sh",
      "async": false
    }
  ]
}
```

---

### 4. Routing Tiers

| Task type | Worker | MCP call | Rationale |
|-----------|--------|----------|-----------|
| File creation, boilerplate, formatting | Ollama (Qwen) | `run_task` | Free, fast, deterministic |
| Structured code with spec | OpenCode / Goose | `run_task` | Code-focused agents |
| Research, search, summarization | Gemini CLI | `run_task` | Strong at broad retrieval |
| 3+ independent subtasks | Best-fit per task | `run_batch` | Wave executor handles parallelism |
| Architecture, review, complex reasoning | Claude Code | — | Full context window required |

The bandit overrides tier defaults over time via learned LinUCB weights per capability bucket. Tiers are a warm start, not a hard constraint.

---

### 5. README Positioning Rewrite

**New tagline:**
> An online bandit routing engine for heterogeneous AI agents. Local-first, research-capable, learns from every task.

**Reorder README sections:**
1. What problem it solves (cost + quality routing across local + cloud)
2. The math (LinUCB + OLS weights + episodic memory = online learning, no retraining)
3. Research engine angle (Gemini CLI for search, Qwen for reasoning, escalation only on failure)
4. Architecture diagram (keep as-is)
5. Comparison table (keep, it's strong)
6. Motivation / related work (keep, accurate)
7. Adapter interface (keep, demote — it's a feature, not the story)

**Cut:** "ease of use" language, "plug-and-play" framing, any copy that targets non-developers.

---

## Error Handling

- Backend not running: MCP tools return `"Mahoraga is not running. Start with: python -m backend.main"` — Claude Code surfaces this and falls back to handling the task directly
- Worker offline: Mahoraga retries or escalates via existing bandit fallback logic
- Flag file missing: hook exits silently (treated as off)

---

## Testing

- Toggle on/off: invoke `/mahoraga` twice, verify flag appears/disappears at `~/.claude/mahoraga-active`
- Hook fires: load any skill while toggle is on, verify routing reminder appears in context
- MCP tools live: with backend running, call `mcp__mahoraga__health_check` to confirm connection
- End-to-end: toggle on, ask Claude Code to create a file → verify it routes via `run_task` not Agent

---

## Files Changed

| File | Action |
|------|--------|
| `~/.claude/settings.json` | Add `mcpServers` + PostToolUse hook |
| `~/.claude/plugins/skills/mahoraga.md` | Create toggle skill |
| `~/.claude/scripts/mahoraga-routing.sh` | Create hook script |
| `README.md` | Rewrite intro, tagline, section order |
