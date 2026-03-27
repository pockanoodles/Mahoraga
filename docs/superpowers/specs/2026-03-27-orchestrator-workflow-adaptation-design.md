# Orchestrator Workflow Adaptation — Design Spec
Date: 2026-03-27

## 1. Problem

Claude Code currently acts as everything: strategist, planner, task manager, and executor coordinator. The goal is to lift that structure one level up. The orchestrator becomes the control plane. Claude becomes one premium worker inside it.

New shape:
```
Human
→ Orchestrator
→ Claude / Extension / Ollama workers
→ files / tools / validators
```

Claude stops being the workflow. Claude becomes part of the workflow.

---

## 2. Approach

**Clean-room rewrite (Approach C).** The existing `backend/orchestrator_svc/` is replaced entirely. Only the two worker adapters (`claude_adapter.py`, `extension_adapter.py`) are rewritten from scratch alongside the new domain. The routing keyword logic and event bus are replaced with the capability model and explicit event types defined here.

---

## 3. Module Structure

```
backend/
  orchestrator/
    domain/
      models.py          # Mission, Plan, Run, Task, TaskAttempt, all enums
      transitions.py     # State machine — legal status changes, blocking rules
      dependencies.py    # Dependency resolution — unlock logic, cycle detection
      artifacts.py       # Artifact creation, linking, existence checks
      events.py          # Event types, creation, append-only log rules
    store/
      missions.py        # Mission + Plan + Run persistence
      tasks.py           # Task + TaskAttempt persistence
      artifacts.py       # Artifact persistence
      events.py          # Event log persistence
    workers/
      base.py            # WorkerAdapter ABC
      claude.py          # Claude Sonnet via Anthropic API
      extension.py       # VS Code extension via HTTP
      registry.py        # Worker registration and capability lookup
    routing/
      router.py          # Capability-based worker assignment
      escalation.py      # Escalation rules
    service/
      app.py             # FastAPI — HTTP routing only, no business logic
      executor.py        # Attempt lifecycle driver — thin, delegates to domain
      approvals.py       # Approval gate — pause/resume
    cli/
      main.py            # `orch` CLI entry point (Typer)
      commands/          # One file per command group
  tests/
    orchestrator/
      test_transitions.py
      test_dependencies.py
      test_artifacts.py
      test_events.py
      test_routing.py
      test_executor.py
      test_approvals.py
      test_persistence.py
```

**Key boundaries:**
- `domain/` has zero knowledge of HTTP, CLI, or workers — pure logic and rules
- `workers/` only knows about `TaskAttempt` and `Task` — not missions or plans
- `service/` wires domain + workers together, holds no business rules
- `cli/` is a thin shell over HTTP calls to the service
- Business rules live in `domain/` only

---

## 4. Domain Model

```python
# Mission — top-level durable objective
Mission:
  id, title, goal, background
  success_condition: str
  context_refs: list[str]        # files/dirs the worker should know
  global_constraints: list[str]
  preferences: dict              # escalation policy, preferred workers, etc.
  status: MissionStatus          # active | completed | cancelled
  created_at, updated_at

# Plan — structured strategy for a mission
Plan:
  id, mission_id, version: int
  phases: list[str]
  worker_strategy: dict
  validation_strategy: dict
  task_graph_shape: str          # linear | parallel | mixed
  status: PlanStatus             # draft | approved | superseded
  created_at

# Run — one live execution of an approved plan
Run:
  id, mission_id, plan_id
  mode: RunMode                  # plan_first | direct | review_loop
  status: RunStatus              # active | paused | completed | failed | cancelled
  created_at, updated_at

# Task — logical unit of work
Task:
  id, run_id, parent_task_id
  title, goal
  scope: list[str]               # paths worker may affect
  context_refs: list[str]        # what worker should read
  done_criteria: str             # externally verifiable — structured type planned for v2
  dependencies: list[Dependency]
  constraints: list[str]         # inherits from mission, narrowed here
  preferred_worker_type: str
  required_capabilities: list[str]
  escalation_count: int
  status: TaskStatus
  created_at, updated_at

# TaskAttempt — one worker's execution of a task
TaskAttempt:
  id, task_id, worker_id
  status: AttemptStatus
  error_code: str                # machine-readable failure reason
  blocking_reason: str           # human-readable description of what blocked/failed
  started_at, ended_at
  summary: str
  artifact_refs: list[str]
  validator_refs: list[str]

# Dependency — typed condition, not raw task_id
Dependency:
  task_id
  type: DependencyType           # completion | artifact | approval

# Artifact — produced output, explicitly tracked, append-only
Artifact:
  id, run_id, task_id, attempt_id
  type: str                      # file | diff | report | test_result | planning_output
  location: dict                 # supports file paths, URLs, DB refs — not path: str
  created_at

# Event — append-only audit history, never authoritative state
Event:
  id, run_id, task_id, attempt_id
  type: EventType
  payload: dict
  ts
```

**Event types (initial set):**
`mission.created`, `plan.created`, `plan.approved`, `run.started`, `run.paused`, `run.cancelled`,
`task.created`, `task.ready`, `task.assigned`, `task.blocked`, `task.completed`, `task.failed`, `task.cancelled`,
`attempt.assigned`, `attempt.started`, `attempt.completed`, `attempt.failed`, `attempt.escalated`, `attempt.cancelled`,
`approval.requested`, `approval.granted`, `approval.rejected`,
`artifact.created`

---

## 5. Status Models

### Task status — logical progress
```
pending     exists, waiting on dependencies
ready       dependencies satisfied, may be assigned
in_progress has an active attempt
blocked     no immediate valid recovery action exists
completed   done_criteria verified by orchestrator
failed      not being retried automatically
cancelled   intentionally stopped
```

### TaskAttempt status — execution outcome
```
assigned    worker selected, not yet started
running     worker is executing
completed   worker reports success (not task completion — see §7)
failed      worker reports failure
blocked     worker cannot proceed, needs input
escalated   handed off to next worker
cancelled   stopped by human
```

`escalated` belongs on `TaskAttempt`, not `Task`. Escalation is what happened to a worker attempt.

### Legal task transitions
```
pending     → ready          all dependencies satisfied
ready       → in_progress    attempt assigned
in_progress → completed      done_criteria verified
in_progress → blocked        attempt fails/blocks, no recovery rule applies
in_progress → failed         explicit give-up (manual or policy)
in_progress → cancelled      human cancels
blocked     → ready          blocker resolved
failed      → ready          explicit human retry/reopen only
```

Illegal transitions rejected with explicit error. Terminal states (`completed`, `cancelled`) cannot mutate without explicit reopen.

---

## 6. Core Invariants

1. `Task.status` is set only by `transitions.py` — never by a worker
2. `TaskAttempt.status` is set by the worker adapter; `transitions.py` decides what it means for the parent Task
3. Artifacts and Events are append-only — no updates, no deletes
4. A task cannot have two `running` attempts simultaneously
5. Late results from stale attempts are silently dropped — attempt ID must match current active attempt
6. `done_criteria` is checked orchestrator-side — worker `summary` is evidence, not truth
7. A successful attempt flows through orchestrator verification before the task becomes `completed`
8. Events are audit history — state truth lives in domain models, never derived from event log

---

## 7. Blocking Rule

A task becomes `blocked` when:
- the current attempt cannot proceed
- AND the orchestrator has no immediate valid recovery action

**Task stays `in_progress`:** attempt fails → routing rule says escalate → new attempt starts immediately.

**Task becomes `blocked`:** human approval required, dependency unresolved, no valid fallback worker, or no escalation path remains.

Tasks block on orchestration failure, not merely worker difficulty.

---

## 8. Executor Flow

`service/executor.py` is thin — it drives the loop and delegates all decisions to `domain/`.

```
on attempt result received:
  1. validate attempt ID matches current active attempt (drop stale results)
  2. write AttemptStatus to store
  3. call transitions.resolve_task(task, attempt, store)
       → decides new TaskStatus
       → checks done_criteria if attempt completed
       → checks escalation rules if attempt failed
  4. if escalation:
       → escalation.py creates new TaskAttempt, assigns next worker
  5. if blocked:
       → approvals.py raises approval.requested event, pauses task
  6. if task completed:
       → dependencies.check_ready(run) unlocks downstream tasks
  7. publish event
```

---

## 9. Worker Interface

```python
class WorkerAdapter(ABC):
    id: str
    capabilities: list[str]

    async def execute(attempt: TaskAttempt, task: Task) -> AsyncIterator[WorkerEvent]
    async def cancel(attempt_id: str) -> None
    async def health() -> WorkerHealth
```

Workers stream `WorkerEvent`. The executor consumes the stream and writes to the event log. Workers never write to the store directly.

**Capability vocabulary (v1):**
- `file_editing` — can read/write files
- `deep_reasoning` — planning, architecture, review
- `review` — code review, feedback
- `planning` — spec and task decomposition
- `cheap_repetitive` — future Ollama slot

---

## 10. Routing + Escalation

**`routing/router.py` — capability-based:**
```
assign_worker(task, registry):
  1. if task.preferred_worker_type → try that worker first
  2. else match task.required_capabilities against registry
  3. if no match → task becomes blocked (no capable worker)
  4. return worker_id, log routing decision as event
```

**`routing/escalation.py`:**
```
should_escalate(task, attempt, registry):
  - attempt status is failed or escalated
  - task escalation_count < mission escalation policy limit (default 2)
  - a higher-capability worker exists that hasn't already attempted this task
  - no ping-pong: cannot escalate back to a worker that already attempted

on_escalate(task, attempt):
  - mark attempt: status=escalated
  - create new TaskAttempt with next worker
  - increment task.escalation_count
  - publish attempt.escalated event
```

---

## 11. Planning Flow

Planning runs through the same `Task` + `TaskAttempt` machinery — no special type.

```
orch plan create --mode plan_first:
  1. create Plan record (status=draft)
  2. create Task:
       title: "Plan: <mission title>"
       goal: derive task graph for this mission
       required_capabilities: ["planning", "deep_reasoning"]
       scope: []
       constraints: ["planning_only"]
  3. create Run (status=paused)
  4. assign planning task to Claude
  5. Claude returns structured JSON — raw output stored as Artifact (type=planning_output)
  6. validate task graph:
       - all tasks have required fields
       - dependency references valid within graph
       - no cycles
       - no scope/constraint conflicts
     if valid   → persist task graph, Plan status=draft, await human approval
     if invalid → attempt status=failed, error_code=planning_output_invalid,
                  blocking_reason describes failure, raw artifact preserved for inspection
  7. human inspects, may retry planning or manually fix
  8. orch plan approve <plan_id>
       → Plan status=approved
       → Run remains paused until human runs: orch run start <run_id>

orch run start <run_id>:
       → Run status=active
       → dependency resolution runs, ready tasks unlocked
       → execution begins

orch plan create --mode direct:
  - skip planning task
  - Run goes straight to active
  - tasks pre-supplied or submitted manually via CLI

orch plan create --mode review_loop:
  - skip planning task
  - assign implementation to extension
  - after completion, create review Task for Claude
  - apply revisions if review requests changes
```

**`planning_only` constraint:**
- `domain/transitions.py` enforces constraint checks before an attempt may start
- `executor.py` calls `transitions.check_constraints(task, attempt)` and aborts dispatch on violation — no constraint logic lives in executor itself
- rejects any attempt that would write files on a `planning_only` task
- Claude output is parsed as structured data, not executed as instructions

**Planning failure path:** auto-escalation does not trigger on planning failures — they need human eyes. Failure surfaces clearly with `blocking_reason` and raw artifact, then blocks for human input.

---

## 12. Dependency Resolution

```python
Dependency:
  task_id: str
  type: DependencyType   # completion | artifact | approval

completion  upstream task must be completed
artifact    upstream task must have produced a linked artifact
approval    approval event (approval.granted) must exist for task_id
```

Dependency resolution runs on every task state change. `dependencies.check_ready(run)` scans all `pending` tasks and transitions qualifying ones to `ready`.

Cycle detection runs at task graph creation time (planning output validation) and again at any manual task addition.

---

## 13. Human Control Loop

**Approval gates:** any `Dependency` with `type=approval` pauses execution. Task stays `blocked` until approval recorded. Approvals stored as Events (`approval.granted` / `approval.rejected`).

**CLI verbs:**

```
# Mission
orch mission new
orch mission show <id>
orch mission list
orch mission update <id>

# Plan
orch plan create --mode <plan_first|direct|review_loop>
orch plan show <id>
orch plan list
orch plan approve <id>
orch plan edit <id>

# Run
orch run start <plan_id>
orch run show <id>
orch run list
orch run cancel <id>
orch run resume <id>

# Task
orch task list [--run <id>] [--status <status>]
orch task show <id>
orch task next
orch task retry <id>
orch task cancel <id>
orch task reassign <id> --worker <worker_id>
orch task unblock <id>

# Execution / supervision
orch status
orch events [--run <id>]
orch result <task_id>
orch approve <task_id>
orch reject <task_id>
orch escalate <task_id>
```

**Minimal operator flow:**
```
orch mission new
orch plan create --mode plan_first
orch plan approve <id>
orch run start <plan_id>
orch status
orch task show <id>
orch approve / retry / reassign / escalate
```

**Interface progression:** CLI + HTTP backend (v1) → lightweight read-only status page (v1.5) → full dashboard (later). Not chat-first.

---

## 14. Test Areas + Priority Order

**Priority order:**
1. task/attempt state consistency
2. late result / stale attempt overwrite protection
3. dependency unlock correctness
4. scope / constraint enforcement
5. done_criteria validation truth
6. human approval gates
7. restart / persistence recovery

**Test areas:**

**A. State machine** — legal/illegal task transitions, terminal state immutability, task/attempt consistency

**B. Retry / attempt** — failed attempt → new attempt, escalation creates clean attempt, late stale result dropped, task status reflects latest valid attempt only

**C. Dependency** — completion/artifact/approval unlock, mixed deps, cycle detection, upstream retry does not incorrectly unlock downstream

**D. Scope / constraint** — edits outside scope rejected, mission constraints inherited, task constraints narrowed, planning_only task blocks edits

**E. done_criteria / validation** — worker claims done but verification fails, artifact missing, validator passes but approval still required, completion is orchestrator truth

**F. Blocking / escalation** — auto-recovery vs human-required block, failed extension escalates to Claude, no ping-pong, stale attempt cannot overwrite current truth

**G. Human control** — approval gate pauses execution, cancel beats late completion, manual retry creates clean attempt, human override respected

**H. Persistence / restart / race** — restart during active attempt, duplicate events, out-of-order events, stale result after escalation, persisted state reconstructs truth, task not completed twice by race

---

## 15. Open Items (v1.5+)

- `done_criteria` becomes a structured object (output_criterion + verification_criterion)
- Re-parse / edit-and-retry flow for malformed planning output
- Lightweight read-only status web page
- Ollama worker adapter
- Full dashboard
- `planning_only` constraint extended to cover all constraint enforcement patterns
