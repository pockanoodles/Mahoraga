# Think-First Pipeline Design

**Project:** ollama-runtime (ollama-claude-code-dupe)
**Date:** 2026-03-26
**Goal:** Close the gap with Claude Code's technical skill for B-sized codebases (5–30k lines). Focus: structured reasoning before action, reliable edits, autonomous self-correction.

---

## 1. Architecture

```
User message
  │
  ├─ classify()  [FAST_WORKER, ~1s]
  │    └─ returns: Classification { complexity, task_type }
  │
  ├─ route(complexity) → model selection
  │    simple  → FAST_WORKER  (qwen2.5-coder:7b)
  │    medium  → SENIOR_WORKER (qwen2.5-coder:14b)
  │    complex → APEX_WORKER   (qwen3-coder:30b)
  │
  ├─ run_agent(model, messages, workspace)
  │    ├─ PLAN PHASE: agent emits plan block before any tool calls
  │    └─ EXECUTE PHASE: tool loop (max 10 iterations)
  │
  ├─ verify()  [FAST_WORKER — medium/complex only]
  │    └─ ACCEPT → yield done
  │    └─ REVISE → inject corrections, retry same model
  │         └─ still REVISE → escalate(model), retry once more
  │              └─ still REVISE → yield done (no infinite loops)
  │
  └─ SSE events stream throughout: token | plan | tool_call | done | error | model
```

Simple tasks skip verify — no latency cost for one-liners.

---

## 2. Think-First System Prompt (CODER_SYSTEM)

Before any tool call, the agent must emit a PLAN block:

```
PLAN (required before any tool calls):
- Read: [files/symbols I need to look at]
- Expect: [what I think I'll find]
- Change: [exactly what I'll modify]
- Verify: [command to confirm it worked]
```

This forces structured reasoning before action. For simple tasks the plan is one line each. For debug/refactor tasks it becomes an explicit hypothesis before investigation.

### Full CODER_SYSTEM rules:

**PLAN** — required before calling any tool. Answer all four fields.

**WORKFLOW:**
1. ORIENT: use list_dir or glob to understand project layout (skip if task is obviously scoped)
2. SEARCH: use grep to find the exact function/class/symbol — never guess locations
3. READ: read_file before touching any file — never modify what you haven't read
4. IMPLEMENT: edit_file for existing files (surgical patch), write_file for new files only
5. VERIFY: run_bash to check syntax or run the nearest relevant test after any change
6. SUMMARIZE: state what changed and why, concisely. No file dumps.

**TOOL RULES:**
- edit_file is the default for modifying existing files
- write_file is for new files only — overwrites everything
- old_string in edit_file must be unique — include surrounding lines if needed
- Use offset/limit when reading large files
- grep before read when looking for something specific
- run_bash for: tests, syntax checks, git status, linters
- Never fabricate file contents — read first, always

**ERROR RECOVERY:**
- If a tool returns an error, re-read the relevant file and adjust before retrying
- Never retry the same tool call unchanged
- If edit_file fails (not found / multiple matches), read the file again and re-match

**QUALITY:**
- Minimal diffs. Change only what the task requires.
- Preserve existing code style and conventions.

---

## 3. Orchestrator Pipeline

### Data Models

```python
@dataclass
class Classification:
    complexity: Literal["simple", "medium", "complex"]
    task_type: Literal["code", "debug", "refactor", "plan", "explain"]

@dataclass
class Verdict:
    verdict: Literal["ACCEPT", "REVISE"]
    corrections: str = ""
```

### classify(message) → Classification
- Single Ollama call to FAST_WORKER with CLASSIFIER_SYSTEM
- Returns Classification dataclass
- Falls back to `{ complexity: "medium", task_type: "code" }` on parse failure

### verify(task, agent_events) → Verdict
- Input: original task message + concatenated `content` from all `token` events in agent_events
- Single Ollama call to FAST_WORKER with VERIFIER_SYSTEM
- Returns Verdict dataclass
- Falls back to ACCEPT on parse failure (don't block on verifier errors)

### run() pipeline

```python
async def run(message, workspace, history):
    # 1. Classify
    classification = await classify(message)
    model = route(classification.complexity)
    yield {"type": "model", "model": model}

    # 2. Execute
    trimmed = history[-24:]
    messages = trimmed + [{"role": "user", "content": message}]
    collected_events = []

    async for event in run_agent(model, messages, workspace):
        collected_events.append(event)
        yield event

    # 3. Verify (medium/complex only)
    if classification.complexity == "simple":
        return

    verdict = await verify(message, collected_events)
    if verdict.verdict == "ACCEPT":
        return

    # Retry with corrections
    correction_msg = f"[CORRECTION]: {verdict.corrections}"
    messages = messages + [{"role": "user", "content": correction_msg}]
    collected_events = []

    async for event in run_agent(model, messages, workspace):
        collected_events.append(event)
        yield event

    # Escalate if still failing
    verdict2 = await verify(message, collected_events)
    if verdict2.verdict == "REVISE":
        escalated = escalate(model)
        if escalated != model:
            async for event in run_agent(escalated, messages, workspace):
                yield event
```

---

## 4. Tool Improvements

### read_file — add line numbers
Output format: `  42│ def foo():`
Provides direct line references for error messages and debugging.

### All tools — output truncation
Cap at 300 lines. Append: `[output truncated at 300 lines — use offset/limit to read more]`
Prevents context overflow on large files/grep results.

### run_bash — always include exit code
Append `\nexit: <returncode>` to all output.
Agent always knows if the command succeeded.

### edit_file — add replace_all flag
`replace_all: bool = False`
When True, replaces all occurrences. Required for renames and bulk updates.

---

## 5. Config Changes

| Setting | Before | After | Reason |
|---------|--------|-------|--------|
| NUM_CTX | 8192 | 32768 | Real codebases need room |
| History window | 8 messages | 24 messages | Debug sessions span many turns |
| Verify scope | not wired | medium + complex | Self-correction for non-trivial tasks |
| Simple task verify | — | skipped | No latency overhead for one-liners |

---

## 6. New SSE Event Type

```json
{ "type": "plan", "content": "Plan:\n- Read: ...\n- Expect: ...\n- Change: ...\n- Verify: ..." }
```

Emitted before the first tool call in each agent run. Extension can surface it in the UI later.

---

## 7. What's Out of Scope

- UI changes (extension webview stays as-is)
- CLI mode
- Planner-Executor split (Option C — future upgrade after this ships)
- Docker/CI/CD

---

## 8. Future: Option C (Planner-Executor Split)

After this ships, upgrade to a dedicated PLANNER agent (qwen3:8b) that decomposes the task into steps, SENIOR_WORKER executes each step, VERIFIER checks after each. Separate agents, separate conversation histories. More autonomous for complex cross-file refactors.
