---
name: mahoraga
description: When to use Mahoraga MCP tools vs handling work in Claude Code directly. Invoke when the user asks to delegate tasks to Mahoraga or when 3+ independent implementation subtasks exist.
type: reference
---

# Mahoraga — When to Use

Mahoraga is a multi-agent orchestrator running at localhost:8000.
MCP tools available: run_task, run_batch, route_task, agent_status, routing_stats, switch_strategy, recent_decisions.

## When to delegate to Mahoraga

- File creation and boilerplate (Mahoraga routes to codex-cli or ollama — free/cheap)
- Implementation plans with **3+ independent subtasks** → use `run_batch`
- Tasks where speed matters more than maximum quality
- Mixed workloads: code generation + research + tests in one shot

## When to keep work in Claude Code

- Complex reasoning, architecture decisions, code review
- Tasks requiring your full context window and planning ability
- Single-file edits where you already know exactly what to do
- Anything where getting it wrong costs more than doing it yourself

## Decision flow

1. Task needs deep reasoning → do it yourself
2. Task is mechanical execution → `run_task`
3. 3+ independent subtasks → `run_batch`
4. Unsure which agent will be picked → `route_task` first
5. After a batch → `routing_stats` to verify the bandit is learning

## run_batch tips

- Set `expected_files` for every coding task — prevents concurrent file conflicts
- Use `depends_on` (0-indexed) for tasks that read each other's outputs
- Use `parallel: false` for tasks with complex cross-file implicit dependencies
- Default `max_concurrent` is 2 — only raise if tasks clearly hit different resource groups (e.g., one local ollama task + one cloud task)

## run_batch example

```json
{
  "tasks": [
    {"prompt": "Create src/utils/hash.py with SHA-256 helper", "expected_files": ["src/utils/hash.py"]},
    {"prompt": "Create src/utils/validation.py with input sanitizer", "expected_files": ["src/utils/validation.py"]},
    {"prompt": "Refactor src/auth/login.py to use hash util", "depends_on": [0], "expected_files": ["src/auth/login.py"]},
    {"prompt": "Write tests for hash and validation utils", "depends_on": [0, 1], "expected_files": ["tests/test_utils.py"]}
  ],
  "parallel": true,
  "max_concurrent": 2
}
```
