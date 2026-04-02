# Option C: Planner-Executor Split Design
**Date:** 2026-04-02
**Branch target:** `feat/orchestrator-domain-store`

## Overview

Upgrade the orchestrator from a single-worker execution model to a three-agent pipeline: PLANNER decomposes missions (already done), SENIOR_WORKER executes tasks with conversation history, and VERIFIER scores output against done criteria and drives a two-tier retry system before escalation.

---

## Agent Roles

| Agent | Model | Role |
|---|---|---|
| PLANNER | qwen3:8b (Ollama) | Mission → task graph. Already implemented in `planning/`. |
| SENIOR_WORKER | claude-sonnet-4-6 | Executes tasks. Maintains conversation history per task. First attempt worker. |
| VERIFIER | claude-haiku-4-5-20251001 | Scores worker output 0–10 against task's `done_criteria`. Returns `VerificationResult`. |
| ESCALATED_WORKER | claude-opus-4-6 | Hard escalation target when Sonnet exhausts retries. Fresh history per task. |

---

## Retry System

### Two Tiers

**Soft retry** — same worker, verifier feedback injected via conversation history. Up to `MAX_SOFT_RETRIES = 2` per worker before hard escalation.

**Hard escalation** — new worker (Opus), fresh history, existing `ESCALATION_LIMIT` governs max escalations.

### Score Thresholds

| Score | Band | Action |
|---|---|---|
| 8–10 | Pass | Complete task |
| 4–7 | Close | Soft retry with feedback (up to 2x) |
| 0–3 | Wrong direction | Skip retries, hard escalate immediately |

Constants: `PASS_THRESHOLD = 8`, `RETRY_THRESHOLD = 4`

### Full Attempt Sequence

```
Sonnet attempt 1 (no feedback)
  score 8-10 → done
  score 4-7  → soft retry
Sonnet attempt 2 (feedback in history)
  score 8-10 → done
  score 4-7  → soft retry
Sonnet attempt 3 (feedback in history)
  score 8-10 → done
  any fail   → hard escalate (reset history, reset retry count)

Opus attempt 1 (no feedback)
  score 8-10 → done
  score 4-7  → soft retry
Opus attempt 2 (feedback in history)
  ...same pattern...
  any fail after MAX_SOFT_RETRIES → BLOCK (request human approval)

Score 0-3 at any point → skip remaining soft retries, hard escalate immediately
```

---

## Architecture

### New Module: `backend/orchestrator/verifier/`

Mirrors `planning/` structure.

```
verifier/
  __init__.py
  config.py     — PASS_THRESHOLD, RETRY_THRESHOLD, MAX_SOFT_RETRIES constants
  prompt.py     — SYSTEM_PROMPT + build_verify_message(task, output) -> str
  verifier.py   — verify(task, output, client) -> VerificationResult
```

**`VerificationResult` dataclass:**
```python
@dataclass
class VerificationResult:
    score: int        # 0-10, from Haiku
    passed: bool      # score >= PASS_THRESHOLD
    feedback: str     # populated when not passed
    action: str       # "pass" | "retry" | "escalate" — derived from score in Python
```

`action` is computed from score thresholds in Python. Haiku only returns `{"score": int, "feedback": str}` — minimal LLM output surface.

**Haiku prompt:**
- System: strict evaluator role, return only JSON `{"score": 0-10, "feedback": "..."}`
- User message contains: task goal, done_criteria, worker output
- Score is a holistic judgment of how well output satisfies `done_criteria`
- `feedback` field populated only when score < PASS_THRESHOLD

**Error handling:** `VerifierError` raised on unparseable JSON. Executor treats this as `action="escalate"` — fail safe, never silently passes bad output.

---

### Worker Changes: Stateful Conversation History

`WorkerAdapter.execute()` gains optional `feedback` parameter:

```python
async def execute(
    self, attempt: TaskAttempt, task: Task, feedback: str | None = None
) -> AsyncGenerator[WorkerEvent, None]: ...
```

Workers maintain `_history: dict[task_id, list[dict[str, str]]]`.

**First call** (feedback=None):
```python
history = [{"role": "user", "content": _build_prompt(task)}]
```

**Retry call** (feedback provided):
```python
history = _history[task_id]
history.append({"role": "assistant", "content": prior_output})
history.append({"role": "user", "content": feedback})
```

Workers store `prior_output` from the last successful API response keyed by `task_id`. History is cleared when a task reaches a terminal state (completed or blocked).

**Two ClaudeWorker registrations** (replaces the single `ClaudeWorker`):

| Worker ID | Model | Capabilities |
|---|---|---|
| `claude:sonnet` | claude-sonnet-4-6 | `["general", "deep_reasoning"]` |
| `claude:opus` | claude-opus-4-6 | `["complex_reasoning", "deep_reasoning", "general"]` |

Tasks requiring `complex_reasoning` route directly to Opus. All others start at Sonnet. Existing `OllamaWorker` gains the same history interface but Ollama capability tag (`cheap_repetitive`, `general`) means it won't be selected for tasks routed to Claude agents.

---

### Executor Changes (`service/executor.py`)

Two new tracking variables in `run_task()`:

```python
soft_retry_count: dict[str, int] = {}   # worker_id → retry count
feedback: str | None = None
```

**Verify step** replaces the existing `verify_done_criteria` string match:

```python
result = await verifier.verify(task, summary, haiku_client)

if result.action == "pass":
    # save artifact, complete task — same as current
    ...

elif result.action == "retry" and soft_retry_count.get(worker_id, 0) < MAX_SOFT_RETRIES:
    soft_retry_count[worker_id] = soft_retry_count.get(worker_id, 0) + 1
    feedback = result.feedback
    continue  # loop back, same worker, feedback passed to execute()

else:
    # retry exhausted or action == "escalate" (score 0-3)
    soft_retry_count = {}
    feedback = None
    attempted.add(worker_id)
    # falls through to existing escalation / block logic
```

The existing `should_escalate()` + `ESCALATION_LIMIT` governs hard escalations — no changes to that logic.

**`haiku_client`** is injected into `run_task()` alongside `store` and `registry`. FastAPI app instantiates it at startup in `lifespan()`.

---

## File Changes Summary

| File | Change |
|---|---|
| `workers/base.py` | Add `feedback` param to `execute()` abstract method; add `_history` management helpers |
| `workers/claude.py` | Stateful history; split into `claude:sonnet` and `claude:opus` registrations |
| `workers/ollama.py` | Add `feedback` param + history support (for interface compliance) |
| `service/executor.py` | Replace `verify_done_criteria` with `verifier.verify()`; add soft retry loop |
| `service/app.py` | Instantiate Haiku client at startup; inject into executor |
| `verifier/__init__.py` | New |
| `verifier/config.py` | New — `PASS_THRESHOLD=8`, `RETRY_THRESHOLD=4`, `MAX_SOFT_RETRIES=2` |
| `verifier/prompt.py` | New — system prompt + `build_verify_message()` |
| `verifier/verifier.py` | New — `verify()`, `VerificationResult`, `VerifierError` |

---

## Testing

- `tests/orchestrator/test_verifier.py` — unit tests for `verify()`: pass/retry/escalate branches, bad JSON handling, score boundary cases
- `tests/orchestrator/test_executor.py` — mock verifier, assert soft retry loop fires correctly, assert score-0-3 skips to escalation
- `tests/orchestrator/test_workers_claude.py` — assert history is built correctly across retries, assert history cleared on task completion
