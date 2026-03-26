# Spec: ollama-runtime — Local Coding Agent

> Status: DESIGN APPROVED

## Overview

A local coding agent runtime that replicates Claude Code behavior at $0 cost.
VSCode sidebar panel as the interface, Python backend as the intelligence layer,
multi-model Ollama routing to compensate for weaker local models.

**Hardware constraint:** 16GB RAM — max model size ~14B parameters.

**Build order:**
1. Python backend — tools + multi-model routing + HTTP server
2. VSCode extension — webview panel, SSE streaming
3. Session memory — handoff.md injection
4. Skills/hooks — port .claude/ workflow system

---

## Project Structure

```
~/Projects/ollama-runtime/
  backend/
    server.py         ← FastAPI, exposes /chat /status /clear
    orchestrator.py   ← classify → route → execute → verify
    agent.py          ← single model agent loop (ported from ~/.claude/ollama-agent/)
    tools.py          ← full tool suite, no path restrictions
    memory.py         ← session memory, handoff.md read/write
    prompts.py        ← system prompts per model role
    models.py         ← model config, routing thresholds
  extension/
    src/
      extension.ts    ← VSCode entry point, registers panel
      panel.ts        ← WebviewPanel, HTTP client, SSE listener
    webview/
      index.html      ← chat UI shell
      chat.js         ← render tokens, tool call events, model badges
      style.css       ← Noctis warm dark theme, Inter font
    package.json
    tsconfig.json
  docs/
    superpowers/specs/
```

---

## Models

| Model | Role | Used For |
|---|---|---|
| `qwen2.5-coder:7b` | Fast worker | Simple tasks, boilerplate, tests |
| `qwen2.5-coder:14b` | Senior worker | Multi-step coding, debugging, refactors |
| `qwen3:14b` | Planner / Judge | Classification, verification, complex planning |

**Rule:** qwen3:14b never writes code. It classifies, judges, and plans only.

---

## Architecture

```
VSCode Extension (TypeScript webview)
         ↕ POST /chat + SSE streaming
  Python Backend — localhost:11278
         ↓
    Orchestrator
    ┌──────────────────────────────┐
    │  1. Classify  (qwen3:14b)    │
    │  2. Route → 7b or 14b coder  │
    │  3. Execute  (tool loop)     │
    │  4. Verify   (qwen3:14b)     │
    │  5. Retry / escalate         │
    └──────────────────────────────┘
         ↓
    Tool Executor
    read_file  write_file  run_bash
    list_dir   grep        glob
         ↓
    Ollama API — localhost:11434
```

---

## Orchestrator Flow

```
message + workspace context
  ↓
[1] CLASSIFY — qwen3:14b
    → { complexity: simple | medium | complex,
        type: code | debug | refactor | plan | explain }

[2] ROUTE
    simple   → qwen2.5-coder:7b  (verify only if confidence < 0.6)
    medium   → qwen2.5-coder:14b (always verify)
    complex  → qwen3:14b plan → qwen2.5-coder:14b execute → verify

[3] EXECUTE — agent tool loop
    max 20 iterations
    tools: read_file, write_file, run_bash, list_dir, grep, glob

[4] VERIFY (medium + complex) — qwen3:14b
    → ACCEPT
    or REVISE { specific corrections }

[5] RETRY on REVISE
    max 2 retries
    3rd fail → escalate: 7b→14b-coder, 14b-coder→qwen3

[6] STREAM tokens + events back via SSE
```

---

## SSE Event Protocol

Every backend response is streamed as typed SSE events:

```
data: {"type": "token",     "content": "Reading..."}
data: {"type": "tool_call", "tool": "read_file", "path": "signup.py"}
data: {"type": "model",     "model": "qwen2.5-coder:14b"}
data: {"type": "token",     "content": "def validate_email(...)"}
data: {"type": "done"}
data: {"type": "error",     "message": "..."}
```

The extension renders tool_call events as inline status (`reading signup.py...`)
and model events as a badge update. Tokens stream directly into the chat bubble.

---

## Tools

All tools resolve paths relative to `workspacePath` sent from the extension.
No path restrictions — agent operates freely in the workspace.

| Tool | Description |
|---|---|
| `read_file` | Read any file. Params: path, offset (lines), limit (lines) |
| `write_file` | Write any file in workspace. Creates dirs as needed. |
| `run_bash` | Full bash. cwd = workspace root. 30s timeout. |
| `list_dir` | Directory listing with file types |
| `grep` | Regex search across files. Params: pattern, path, glob filter |
| `glob` | File pattern matching. Params: pattern, path |

---

## VSCode Extension UI

Sidebar webview panel. Activated via Activity Bar icon.

```
┌──────────────────────────────┐
│ ◈ ollama-runtime         [⚙] │
├──────────────────────────────┤
│                              │
│  you                         │
│  ╔══════════════════════╗    │
│  ║ add validation to    ║    │
│  ║ the signup endpoint  ║    │
│  ╚══════════════════════╝    │
│                              │
│  agent  · 14b-coder ·        │
│  reading signup.py...        │
│  ╔══════════════════════╗    │
│  ║ ```python            ║    │
│  ║ def validate_email:  ║    │
│  ║   ...                ║    │
│  ║ ```                  ║    │
│  ╚══════════════════════╝    │
│                              │
├──────────────────────────────┤
│ ask anything...          [↵] │
└──────────────────────────────┘
```

- Warm dark theme (Noctis aesthetic), Inter font
- Syntax-highlighted code blocks
- Active model badge updates in real-time as routing switches models
- Tool call status shown inline as agent works

---

## Session Memory (Phase 2)

`memory.py` manages a `handoff.md` file per workspace:

**On every request:** Load `handoff.md` → inject into system prompt as context
**On every response:** Append decisions, file changes, current state to `handoff.md`

Creates pseudo-memory across messages exactly like Claude Code session context.

---

## Out of Scope (v1)

- Skills/hooks system (Phase 3)
- Multi-workspace support
- Git integration
- Terminal TUI
- Auth or remote access

---

## Acceptance Criteria (v1)

- [ ] VSCode panel opens and accepts text input
- [ ] Backend classifies task and routes to correct model
- [ ] Agent reads/writes files and runs bash in workspace
- [ ] Tool calls visible in UI as they happen
- [ ] qwen3:14b verifies medium/complex outputs
- [ ] Retry and escalation work correctly
- [ ] Responses stream token by token, not all at once
