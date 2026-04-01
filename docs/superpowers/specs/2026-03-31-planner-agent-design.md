# Planner Agent Design

**Date:** 2026-03-31
**Status:** Approved

---

## Overview

Add a Planner Agent that automatically decomposes a mission into executable tasks,
then smoke test the full system end-to-end. This makes the orchestrator autonomous —
instead of manually writing tasks, you give it a mission and it generates the task graph.

**Scope:** Additive only. No changes to executor, workers, store schema, or domain models.

---

## Phase 1 — Smoke Test

Before writing any new code, verify Plans A-C are wired together correctly:

1. `orch mission create` → `orch plan create` → `orch task add` (manual) → `orch run start`
2. Watch OllamaWorker execute the task
3. Fix anything broken

This is done first. If the existing system doesn't run, we fix it before building on top.

---

## Phase 2 — Planner Module

### New files

```
backend/orchestrator/planning/
    __init__.py
    prompt.py       — system prompt template for qwen3:8b
    planner.py      — Ollama call, parse, validate, save tasks
    validator.py    — field checks + DAG cycle detection
```

### New CLI command

```
orch plan <mission_id>
```

Runs the planner against the mission, prints generated tasks, saves them to the store.

### Full workflow after this

```
orch mission create "Build REST API"
orch plan <mission_id>          ← new
orch run start <plan_id>
```

---

## Planner I/O

### Input

Mission fields sent to the model:
- `title`
- `goal`
- `success_condition`

### Ollama call

- Model: `qwen3:8b`
- `format: "json"` (Ollama enforces valid JSON output)
- Temperature: 0
- Endpoint: `POST /api/chat`

### System prompt strategy

Short and strict. Decompose the goal into 3-8 tasks. Output only JSON. No explanation text.
Prompt lives in `prompt.py` so it can be tuned independently.

### Expected model output schema

```json
{
  "tasks": [
    {
      "title": "Set up project structure",
      "goal": "Create the directory layout and install dependencies",
      "type": "setup",
      "dependencies": [],
      "done_criteria": "Directory exists and deps are installed"
    },
    {
      "title": "Implement user model",
      "goal": "Define the User dataclass and database schema",
      "type": "coding",
      "dependencies": ["Set up project structure"],
      "done_criteria": "User model exists with id, email, hashed_password fields"
    }
  ]
}
```

Dependencies reference task **titles** within the same batch. Resolved to IDs after all tasks
are created.

### Mapping to domain models

```python
Task.new(
    run_id=run_id,
    title=t["title"],
    goal=t["goal"],
    done_criteria=t.get("done_criteria", ""),
    dependencies=[...],   # resolved Dependency objects
    context_refs=[],      # empty — vector layer fills this later
)
```

---

## Validation (validator.py)

Runs before anything is saved. Atomic — if validation fails, nothing is written.

Rules:
1. Every task has non-empty `title` and `goal`
2. All dependency references exist in the same task batch (by title)
3. No cycles in the dependency graph (uses existing `domain/dependencies.detect_cycles`)

Raises with a descriptive message listing which checks failed.

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| Ollama unreachable | Raise `OllamaUnavailable` with clear message |
| JSON parse fails | Raise with raw model response attached |
| Validation fails | Raise listing failed checks, nothing saved |
| All errors | Propagate to CLI, print cleanly — no silent failures |

---

## Testing

- **Unit:** `validator.py` — cycle detection, missing fields, bad dep references. No Ollama needed.
- **Integration:** `planner.py` — mock Ollama HTTP call, assert tasks saved correctly.
- **Smoke:** One real Ollama test, skipped in CI if Ollama not running (`pytest.mark.ollama`).
- **Regression:** All 95 existing tests stay green.

---

## What Does Not Change

- `executor.py`
- `workers/`
- `store/` schema
- Domain models (`domain/`)
- Routing

The planner is purely additive. If it breaks or is removed, the rest of the system is unaffected.

---

## Out of Scope

- Vector layer / embeddings (deferred — add once planner is working and we can see real failure modes)
- Adaptive re-planning (deferred)
- Parallel task execution scheduling (already handled by existing wave-based executor)
