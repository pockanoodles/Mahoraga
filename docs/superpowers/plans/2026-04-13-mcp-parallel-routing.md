# MCP Server + Parallel Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Mahoraga's bandit router and wave executor as an MCP stdio server with 7 tools, and add wave-based concurrent batch execution with resource-group-aware scheduling.

**Architecture:** Three coupled components: (1) `score_all()` on BanditRouter enables read-only UCB scoring for the dry-run tool; (2) `wave_executor.py` groups ready tasks into resource-group-constrained concurrent waves; (3) `backend/mcp/server.py` bridges everything as a stdio MCP server. Tasks 1–4 give a working sequential MCP server. Tasks 5–6 add parallelism and congestion awareness.

**Tech Stack:** Python 3.12, FastAPI, httpx, `mcp>=1.0.0` (Anthropic's MCP SDK), asyncio, numpy (LinUCB)

---

## File Structure

**New files:**
- `backend/orchestrator/resource_groups.py` — resource group dict + `get_resource_group()`, `get_group_concurrency()`
- `backend/orchestrator/service/wave_executor.py` — `WaveExecutor._build_waves()` + `execute_batch()`
- `backend/mcp/__init__.py` — package marker
- `backend/mcp/server.py` — stdio bridge, 7 MCP tools
- `backend/mcp/test_server.py` — unit tests for MCP handlers

**Modified files:**
- `backend/orchestrator/routing/strategies/base.py` — add `compute_scores()` (non-abstract, default `{}`)
- `backend/orchestrator/routing/strategies/linucb.py` — implement `compute_scores()`; `d=9` in Task 6
- `backend/orchestrator/routing/context.py` — add `queue_depth_norm` field in Task 6
- `backend/orchestrator/routing/bandit_router.py` — add `score_all()` method
- `backend/orchestrator/routing/decision_log.py` — add `get_recent()` method
- `backend/orchestrator/service/app.py` — 5 new endpoints + 3 new Pydantic models
- `requirements.txt` — add `mcp>=1.0.0`

---

### Task 1: score_all() on BanditRouter

**Files:**
- Modify: `backend/orchestrator/routing/strategies/base.py`
- Modify: `backend/orchestrator/routing/strategies/linucb.py`
- Modify: `backend/orchestrator/routing/bandit_router.py`
- Test: `backend/orchestrator/routing/tests/test_score_all.py` (new)

**Goal:** Add read-only UCB scoring without incrementing `t` or logging a decision. Powers `POST /api/routing/dry-run`.

- [ ] **Step 1: Write the failing tests**

Create `backend/orchestrator/routing/tests/test_score_all.py`:

```python
import pytest
from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext


def _ctx(goal: str = "write a function") -> TaskContext:
    class _T:
        tier = 2
        def __init__(self, g): self.goal = g
    return TaskContext.from_task(_T(goal))


def test_compute_scores_does_not_increment_t():
    router = LinUCBRouter()
    ctx = _ctx()
    t_before = router.t
    scores = router.compute_scores(ctx, ["aider", "ollama"])
    assert router.t == t_before, "compute_scores must not increment t"
    assert set(scores.keys()) == {"aider", "ollama"}
    assert "ucb" in scores["aider"]


def test_compute_scores_is_idempotent():
    router = LinUCBRouter()
    ctx = _ctx()
    s1 = router.compute_scores(ctx, ["aider", "ollama"])
    s2 = router.compute_scores(ctx, ["aider", "ollama"])
    assert s1 == s2


def test_compute_scores_does_not_corrupt_select_agent():
    """After compute_scores, select_agent still works and increments t."""
    router = LinUCBRouter()
    ctx = _ctx()
    router.compute_scores(ctx, ["aider", "ollama"])
    t_before = router.t
    winner = router.select_agent(ctx, ["aider", "ollama"])
    assert router.t == t_before + 1
    assert winner in ["aider", "ollama"]


def test_bandit_score_all_returns_scores_and_strategy():
    bandit = BanditRouter(strategy="linucb")

    class _T:
        goal = "create a dockerfile"
        tier = 2

    result = bandit.score_all(_T())
    assert "strategy" in result
    assert "scores" in result
    assert isinstance(result["scores"], dict)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/kaitosoeno/Projects/Mahoraga
pytest backend/orchestrator/routing/tests/test_score_all.py -v 2>&1 | head -20
```

Expected: `AttributeError: 'LinUCBRouter' object has no attribute 'compute_scores'`

- [ ] **Step 3: Add compute_scores() to RoutingStrategy base**

In `backend/orchestrator/routing/strategies/base.py`, add after `get_scores()`:

```python
def compute_scores(self, context, available_agents: list[str]) -> dict:
    """Read-only UCB scoring without state mutation.

    Override in strategies that support true read-only computation.
    Default returns empty dict (non-contextual strategies).
    """
    return {}
```

- [ ] **Step 4: Implement compute_scores() in LinUCBRouter**

In `backend/orchestrator/routing/strategies/linucb.py`, add after `select_agent()`:

```python
def compute_scores(self, context, available_agents: list[str]) -> dict:
    """Compute UCB scores for all agents without incrementing t or storing _last_scores."""
    if not available_agents:
        return {}
    x = context.to_vector().reshape(-1, 1)
    scores = {}
    for a in available_agents:
        self._init_agent(a)  # idempotent — only initialises on first call
        theta = np.linalg.solve(self.A[a], self.b[a])
        exploit = float((x.T @ theta).item())
        explore_sq = float((x.T @ np.linalg.solve(self.A[a], x)).item())
        explore = self.alpha * float(np.sqrt(max(0.0, explore_sq)))
        scores[a] = {
            "ucb": round(exploit + explore, 4),
            "exploit": round(exploit, 4),
            "explore": round(explore, 4),
        }
    return scores
```

- [ ] **Step 5: Add score_all() to BanditRouter**

In `backend/orchestrator/routing/bandit_router.py`, add after `get_stats()`:

```python
def score_all(self, task, available_agents: list[str] | None = None) -> dict:
    """Read-only UCB scoring — no logged decision, no state mutation.

    Used by POST /api/routing/dry-run.
    """
    context = TaskContext.from_task(task)
    available = available_agents if available_agents is not None else self._available_agents()
    scores = self.strategy.compute_scores(context, available)
    return {
        "strategy": self.strategy.name,
        "scores": scores,
    }
```

- [ ] **Step 6: Run tests to confirm pass**

```bash
pytest backend/orchestrator/routing/tests/test_score_all.py -v
```

Expected:
```
PASSED test_compute_scores_does_not_increment_t
PASSED test_compute_scores_is_idempotent
PASSED test_compute_scores_does_not_corrupt_select_agent
PASSED test_bandit_score_all_returns_scores_and_strategy
4 passed
```

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/routing/strategies/base.py \
        backend/orchestrator/routing/strategies/linucb.py \
        backend/orchestrator/routing/bandit_router.py \
        backend/orchestrator/routing/tests/test_score_all.py
git commit -m "feat: add score_all() to BanditRouter for read-only dry-run scoring"
```

---

### Task 2: resource_groups.py + DecisionLogger.get_recent()

**Files:**
- Create: `backend/orchestrator/resource_groups.py`
- Modify: `backend/orchestrator/routing/decision_log.py`
- Test: `backend/orchestrator/routing/tests/test_resource_groups.py` (new)

**Goal:** Resource group registry (single dict + two lookup functions) and `get_recent()` for the decisions endpoint.

- [ ] **Step 1: Write the failing tests**

Create `backend/orchestrator/routing/tests/test_resource_groups.py`:

```python
import pytest
from backend.orchestrator.resource_groups import (
    RESOURCE_GROUPS, get_resource_group, get_group_concurrency,
)
from backend.orchestrator.routing.decision_log import DecisionLogger
import numpy as np


def test_get_resource_group_known_agents():
    assert get_resource_group("ollama") == "local_ollama"
    assert get_resource_group("aider") == "local_ollama"
    assert get_resource_group("codex-cli") == "openai_api"
    assert get_resource_group("gemini-cli") == "google_api"
    assert get_resource_group("claude") == "anthropic_api"


def test_get_resource_group_unknown():
    assert get_resource_group("mystery-agent") == "unknown"


def test_get_group_concurrency():
    assert get_group_concurrency("local_ollama") == 1
    assert get_group_concurrency("openai_api") == 2
    assert get_group_concurrency("google_api") == 3
    assert get_group_concurrency("no_such_group") == 1  # conservative default


def test_decision_logger_get_recent_empty():
    logger = DecisionLogger(db_path=":memory:")
    assert logger.get_recent(limit=10) == []


def test_decision_logger_get_recent_returns_decisions():
    logger = DecisionLogger(db_path=":memory:")

    class _Task:
        id = "t1"
        goal = "create a function"

    class _Ctx:
        def to_vector(self):
            return np.array([0.1] * 8)

    logger.log_decision(_Task(), _Ctx(), "aider", ["aider", "ollama"], "linucb")
    recent = logger.get_recent(limit=10)
    assert len(recent) == 1
    assert recent[0]["selected_agent"] == "aider"
    assert recent[0]["task_goal"] == "create a function"


def test_decision_logger_get_recent_agent_filter():
    logger = DecisionLogger(db_path=":memory:")

    class _Task:
        id = "t1"
        goal = "write code"

    class _Ctx:
        def to_vector(self):
            return np.array([0.1] * 8)

    logger.log_decision(_Task(), _Ctx(), "aider", ["aider", "ollama"], "linucb")
    logger.log_decision(_Task(), _Ctx(), "ollama", ["aider", "ollama"], "linucb")

    aider_only = logger.get_recent(limit=10, agent="aider")
    assert len(aider_only) == 1
    assert aider_only[0]["selected_agent"] == "aider"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest backend/orchestrator/routing/tests/test_resource_groups.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.resource_groups'`

- [ ] **Step 3: Create resource_groups.py**

Create `backend/orchestrator/resource_groups.py`:

```python
"""
Resource group registry for Mahoraga's wave executor.

A resource group represents a physical backend shared by one or more agents.
The wave executor enforces max_concurrent per group to prevent overloading
shared infrastructure (e.g., the single local GPU serving both ollama and aider).

To add a second Ollama instance: split local_ollama into local_ollama_0 and
local_ollama_1, reassign agents. No executor changes needed.
"""

RESOURCE_GROUPS: dict[str, dict] = {
    "local_ollama": {
        "agents": ["ollama", "aider"],
        "max_concurrent": 1,
        "description": "Single local GPU, shared Ollama server",
    },
    "openai_api": {
        "agents": ["codex-cli"],
        "max_concurrent": 2,
        "description": "OpenAI cloud API, rate-limited",
    },
    "google_api": {
        "agents": ["gemini-cli"],
        "max_concurrent": 3,
        "description": "Google cloud API, rate-limited",
    },
    "anthropic_api": {
        "agents": ["claude"],
        "max_concurrent": 2,
        "description": "Anthropic cloud API, rate-limited",
    },
    "unknown": {
        "agents": ["goose", "opencode"],
        "max_concurrent": 1,
        "description": "Conservative default for uncharacterized agents",
    },
}


def get_resource_group(agent_name: str) -> str:
    """Return the resource group an agent belongs to."""
    for group_name, group in RESOURCE_GROUPS.items():
        if agent_name in group["agents"]:
            return group_name
    return "unknown"


def get_group_concurrency(group_name: str) -> int:
    """Return the max concurrent tasks for a resource group."""
    return RESOURCE_GROUPS.get(group_name, RESOURCE_GROUPS["unknown"])["max_concurrent"]
```

- [ ] **Step 4: Add get_recent() to DecisionLogger**

In `backend/orchestrator/routing/decision_log.py`, add after `export_csv()`:

```python
def get_recent(
    self,
    limit: int = 10,
    agent: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Return recent routing decisions, newest first.

    Columns returned: id, timestamp, task_id, task_goal, strategy,
    selected_agent, scores, success, latency_s, reward, error_message.
    """
    with self._lock:
        filters, params = [], []
        if agent:
            filters.append("selected_agent = ?")
            params.append(agent)
        if since:
            filters.append("timestamp >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        cur = self._conn.execute(
            f"SELECT id, timestamp, task_id, task_goal, strategy, selected_agent, "
            f"scores, success, latency_s, reward, error_message "
            f"FROM decisions {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        )
        col_names = [d[0] for d in cur.description]
        return [dict(zip(col_names, row)) for row in cur.fetchall()]
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
pytest backend/orchestrator/routing/tests/test_resource_groups.py -v
```

Expected:
```
PASSED test_get_resource_group_known_agents
PASSED test_get_resource_group_unknown
PASSED test_get_group_concurrency
PASSED test_decision_logger_get_recent_empty
PASSED test_decision_logger_get_recent_returns_decisions
PASSED test_decision_logger_get_recent_agent_filter
6 passed
```

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/resource_groups.py \
        backend/orchestrator/routing/decision_log.py \
        backend/orchestrator/routing/tests/test_resource_groups.py
git commit -m "feat: resource_groups registry and DecisionLogger.get_recent()"
```

---

### Task 3: New FastAPI Endpoints

**Files:**
- Modify: `backend/orchestrator/service/app.py`

**Goal:** 5 new endpoints: `POST /api/task`, `POST /api/routing/dry-run`, `GET /api/routing/decisions`, `GET /api/resource-groups`, `POST /api/batch` (sequential first — parallel added in Task 5).

**`/api/task` flow:** Creates a minimal Mission/Plan/Run/Task in the store, runs it through the existing executor (which handles assignment, streaming, verification, retry), then reads the result from artifacts.

- [ ] **Step 1: Add Pydantic request models**

In `backend/orchestrator/service/app.py`, find the existing `class CreateMissionRequest(BaseModel):` block and add these models in the same section:

```python
class TaskRequest(BaseModel):
    prompt: str
    capability_hint: str | None = None
    agent_override: str | None = None


class BatchTaskItem(BaseModel):
    prompt: str
    depends_on: list[int] = []
    expected_files: list[str] = []
    capability_hint: str | None = None


class BatchRequest(BaseModel):
    tasks: list[BatchTaskItem]
    parallel: bool = True
    max_concurrent: int = 2
```

- [ ] **Step 2: Add POST /api/routing/dry-run**

Append after the existing `POST /api/routing/strategy` endpoint in `app.py`:

```python
@app.post("/api/routing/dry-run")
async def routing_dry_run(body: dict):
    """Score all available agents for a prompt without committing a routing decision."""
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    router = get_bandit_router()

    class _FakeTask:
        goal = prompt
        tier = 2

    result = router.score_all(_FakeTask())
    scores_list = [
        {
            "agent": agent,
            "ucb_score": s["ucb"],
            "exploit": s["exploit"],
            "explore": s["explore"],
        }
        for agent, s in sorted(
            result["scores"].items(), key=lambda x: x[1]["ucb"], reverse=True
        )
    ]
    selected = scores_list[0]["agent"] if scores_list else None

    from ..routing.context import CODE_KEYWORDS, RESEARCH_KEYWORDS
    words = set(prompt.lower().split())
    bucket = "code" if words & CODE_KEYWORDS else "general"

    return {
        "prompt": prompt,
        "keyword_classification": {"capability_bucket": bucket},
        "bandit_selection": {
            "strategy": result["strategy"],
            "selected_agent": selected,
            "scores": scores_list,
        },
    }
```

- [ ] **Step 3: Add GET /api/routing/decisions**

```python
@app.get("/api/routing/decisions")
async def routing_decisions(
    limit: int = 10,
    agent: str | None = None,
    since: str | None = None,
):
    """Query recent routing decisions from the decision log."""
    limit = min(limit, 50)
    router = get_bandit_router()
    decisions = router.logger.get_recent(limit=limit, agent=agent, since=since)
    return {
        "decisions": decisions,
        "total_available": router.logger.count(),
        "filters_applied": {k: v for k, v in {"agent": agent, "since": since}.items() if v},
    }
```

- [ ] **Step 4: Add GET /api/resource-groups**

```python
@app.get("/api/resource-groups")
async def resource_groups_endpoint():
    """Resource group config. current_load is populated in Task 5."""
    from ..resource_groups import RESOURCE_GROUPS
    return {
        name: {
            "agents": group["agents"],
            "max_concurrent": group["max_concurrent"],
            "description": group["description"],
            "current_load": 0,
        }
        for name, group in RESOURCE_GROUPS.items()
    }
```

- [ ] **Step 5: Add POST /api/task**

```python
@app.post("/api/task")
async def run_api_task(
    req: TaskRequest,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    """Execute a single task synchronously. Waits for completion (up to 5 min)."""
    import dataclasses
    import time as _time
    from ..domain.models import Mission, Plan, Run, Task, RunMode, TaskStatus
    from ..routing.reward import TaskOutcome
    from ..resource_groups import get_resource_group

    router = get_bandit_router()
    adapter_reg = get_adapter_registry()

    # Minimal infrastructure: one mission → plan → run → task
    mission = Mission.new(title=f"MCP: {req.prompt[:40]}", goal=req.prompt)
    await store.missions.save(mission)
    plan = Plan.new(mission_id=mission.id)
    await store.missions.save_plan(plan)
    run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_run(run)

    task = Task.new(run_id=run.id, title=req.prompt[:80], goal=req.prompt)
    await store.tasks.save(task)

    # Route via bandit (logs the decision, populates _last_scores)
    selected_agent = req.agent_override or router.route(task)
    scores = router.strategy.get_scores()  # populated by route() above

    # Map adapter name → worker_id so executor uses the bandit's choice
    adapter = adapter_reg.get(selected_agent)
    if adapter:
        task = dataclasses.replace(task, preferred_worker_type=adapter.worker_id)

    # Transition task to ready so executor can pick it up
    await store.tasks.update_status(task.id, TaskStatus.ready)
    # update the in-memory object status too (executor re-fetches from store)

    t0 = _time.time()
    await _run_task(task.id, store, registry, verifier)
    elapsed = round(_time.time() - t0, 2)

    # Collect result
    task = await store.tasks.get(task.id)
    attempts = await store.tasks.list_attempts(task.id)
    artifacts = await store.artifacts.list_by_task(task.id)

    output = next(
        (a.location.get("content", "") for a in artifacts if a.type == "text_output"), ""
    )
    used_worker = attempts[-1].worker_id if attempts else selected_agent
    status = "success" if task.status == TaskStatus.completed else "failed"

    # Update bandit with the outcome
    outcome = TaskOutcome(
        success=(status == "success"),
        latency_s=elapsed,
        cost_usd=0.0,
        quality_score=0.8 if status == "success" else 0.0,
        agent_name=selected_agent,
    )
    router.observe(task, outcome)

    # Build runner-up from scores
    runner_up = None
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1]["ucb"], reverse=True)
        if len(sorted_scores) > 1:
            runner_up = {"agent": sorted_scores[1][0], "ucb_score": sorted_scores[1][1]["ucb"]}

    return {
        "task_id": task.id,
        "status": status,
        "agent": selected_agent,
        "worker_id": used_worker,
        "resource_group": get_resource_group(selected_agent),
        "elapsed_s": elapsed,
        "output": output,
        "routing": {
            "strategy": router.strategy.name,
            "ucb_score": scores.get(selected_agent, {}).get("ucb") if scores else None,
            "runner_up": runner_up,
        },
    }
```

- [ ] **Step 6: Add POST /api/batch (sequential)**

```python
@app.post("/api/batch")
async def run_batch(
    req: BatchRequest,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    """Batch task execution. Sequential in this version — parallel added in Task 5."""
    import dataclasses
    import time as _time
    import uuid as _uuid
    from ..domain.models import (
        Mission, Plan, Run, Task, RunMode, TaskStatus,
        Dependency, DependencyType,
    )
    from ..resource_groups import get_resource_group

    batch_id = f"b_{_uuid.uuid4().hex[:8]}"
    t_batch_start = _time.time()

    router = get_bandit_router()
    adapter_reg = get_adapter_registry()

    # Create shared run for the batch
    mission = Mission.new(title=f"Batch {batch_id}", goal=f"{len(req.tasks)} tasks")
    await store.missions.save(mission)
    plan = Plan.new(mission_id=mission.id)
    await store.missions.save_plan(plan)
    run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_run(run)

    # Create all tasks upfront (scope stores expected_files)
    created: list[Task] = []
    for i, item in enumerate(req.tasks):
        deps = [
            Dependency(task_id=created[j].id, type=DependencyType.completion)
            for j in item.depends_on
            if j < i
        ]
        task = Task.new(
            run_id=run.id,
            title=item.prompt[:80],
            goal=item.prompt,
            dependencies=deps,
            scope=item.expected_files,
        )
        await store.tasks.save(task)
        created.append(task)

    # Execute tasks in dependency order, sequentially
    results: list[dict] = []
    sequential_s = 0.0
    completed_ids: set[str] = set()
    remaining = list(range(len(created)))

    while remaining:
        ready_indices = [
            i for i in remaining
            if all(created[j].id in completed_ids for j in req.tasks[i].depends_on if j < i)
        ]
        if not ready_indices:
            break

        i = ready_indices[0]
        task = created[i]

        selected = router.route(task)
        adapter = adapter_reg.get(selected)
        if adapter:
            task = dataclasses.replace(task, preferred_worker_type=adapter.worker_id)
        await store.tasks.update_status(task.id, TaskStatus.ready)

        t0 = _time.time()
        await _run_task(task.id, store, registry, verifier)
        elapsed = round(_time.time() - t0, 2)
        sequential_s += elapsed

        task = await store.tasks.get(task.id)
        artifacts = await store.artifacts.list_by_task(task.id)
        output = next(
            (a.location.get("content", "") for a in artifacts if a.type == "text_output"), ""
        )
        status = "success" if task.status == TaskStatus.completed else "failed"

        results.append({
            "task_index": i,
            "status": status,
            "agent": selected,
            "resource_group": get_resource_group(selected),
            "wave": len(results) + 1,
            "elapsed_s": elapsed,
            "output": output,
        })
        completed_ids.add(task.id)
        remaining.remove(i)

    total_elapsed = round(_time.time() - t_batch_start, 2)
    speedup = round(sequential_s / total_elapsed, 2) if total_elapsed > 0 else 1.0

    return {
        "batch_id": batch_id,
        "total_wall_clock_s": total_elapsed,
        "sequential_estimate_s": round(sequential_s, 2),
        "speedup": f"{speedup}x",
        "waves_executed": len(results),
        "results": results,
    }
```

- [ ] **Step 7: Verify endpoints work**

With Mahoraga running (`python -m backend.main`):

```bash
# Dry-run
curl -s -X POST http://localhost:8000/api/routing/dry-run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "write a dockerfile"}' | python -m json.tool

# Decisions log
curl -s "http://localhost:8000/api/routing/decisions?limit=5" | python -m json.tool

# Resource groups
curl -s http://localhost:8000/api/resource-groups | python -m json.tool
```

Each should return valid JSON without errors.

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/service/app.py
git commit -m "feat: add /api/task, /api/batch, /api/routing/dry-run, /api/routing/decisions, /api/resource-groups"
```

---

### Task 4: MCP Server (stdio bridge)

**Files:**
- Create: `backend/mcp/__init__.py`
- Create: `backend/mcp/server.py`
- Create: `backend/mcp/test_server.py`
- Modify: `requirements.txt`

**Goal:** Thin stdio bridge — 7 tools, each 3–8 lines: build params, call HTTP endpoint, return result. No business logic here.

- [ ] **Step 1: Install mcp and update requirements.txt**

```bash
cd /Users/kaitosoeno/Projects/Mahoraga
pip install "mcp>=1.0.0"
echo "mcp>=1.0.0" >> requirements.txt
```

- [ ] **Step 2: Write the failing tests**

Create `backend/mcp/test_server.py`:

```python
"""Unit tests for MCP server tool handlers."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_handle_run_task_basic():
    from backend.mcp.server import _handle_run_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success", "agent": "ollama"}
        result = await _handle_run_task({"prompt": "create test.py"})
        mock.assert_called_once_with("/api/task", {"prompt": "create test.py"})
        assert result["status"] == "success"


@pytest.mark.asyncio
async def test_handle_run_task_with_overrides():
    from backend.mcp.server import _handle_run_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success"}
        await _handle_run_task({
            "prompt": "research JWT",
            "capability_hint": "general",
            "agent_override": "gemini-cli",
        })
        mock.assert_called_once_with("/api/task", {
            "prompt": "research JWT",
            "capability_hint": "general",
            "agent_override": "gemini-cli",
        })


@pytest.mark.asyncio
async def test_handle_route_task():
    from backend.mcp.server import _handle_route_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"bandit_selection": {"selected_agent": "aider"}}
        await _handle_route_task({"prompt": "refactor auth"})
        mock.assert_called_once_with("/api/routing/dry-run", {"prompt": "refactor auth"})


@pytest.mark.asyncio
async def test_handle_agent_status_merges_responses():
    from backend.mcp.server import _handle_agent_status
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            [{"name": "ollama", "available": True}],
            {"local_ollama": {"max_concurrent": 1}},
        ]
        result = await _handle_agent_status({})
        assert "agents" in result
        assert "resource_groups" in result


@pytest.mark.asyncio
async def test_handle_not_running_returns_error():
    from backend.mcp.server import _handle_routing_stats
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"error": "Mahoraga is not running. Start it with: ..."}
        result = await _handle_routing_stats({})
        assert "error" in result


@pytest.mark.asyncio
async def test_handle_switch_strategy():
    from backend.mcp.server import _handle_switch_strategy
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"strategy": "thompson"}
        await _handle_switch_strategy({"strategy": "thompson"})
        mock.assert_called_once_with("/api/routing/strategy", {"strategy": "thompson"})


@pytest.mark.asyncio
async def test_handle_recent_decisions_with_agent_filter():
    from backend.mcp.server import _handle_recent_decisions
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"decisions": [], "total_available": 0}
        await _handle_recent_decisions({"limit": 5, "agent_filter": "aider"})
        mock.assert_called_once_with(
            "/api/routing/decisions", {"limit": 5, "agent": "aider"}
        )
```

- [ ] **Step 3: Run to confirm failure**

```bash
pytest backend/mcp/test_server.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'backend.mcp.server'`

- [ ] **Step 4: Create backend/mcp/__init__.py**

Create `backend/mcp/__init__.py`:

```python
"""Mahoraga MCP Server — stdio bridge to Mahoraga's orchestration API."""
```

- [ ] **Step 5: Create backend/mcp/server.py**

Create `backend/mcp/server.py`:

```python
"""
Mahoraga MCP Server — stdio bridge to Mahoraga's orchestration API.

Usage:
    python -m backend.mcp.server

Claude Code config (~/.claude/settings.json):
    {
        "mcpServers": {
            "mahoraga": {
                "command": "python",
                "args": ["-m", "backend.mcp.server"],
                "cwd": "/Users/kaitosoeno/Projects/Mahoraga"
            }
        }
    }
"""
from __future__ import annotations
import asyncio
import json
import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

MAHORAGA_BASE = os.environ.get("MAHORAGA_BASE", "http://localhost:8000")
TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
_NOT_RUNNING = (
    "Mahoraga is not running. "
    "Start it with: cd ~/Projects/Mahoraga && python -m backend.main"
)

server = Server("mahoraga")


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
        try:
            resp = await client.post(path, json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"error": _NOT_RUNNING}
        except httpx.HTTPStatusError as e:
            return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
        try:
            resp = await client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"error": _NOT_RUNNING}
        except httpx.HTTPStatusError as e:
            return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_task",
            description=(
                "Route and execute a single task through Mahoraga. The bandit selects the best "
                "available agent based on task type and learned performance. Returns the agent's "
                "output, routing decision, and execution metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "capability_hint": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                        "description": "Optional. Override keyword classification.",
                    },
                    "agent_override": {
                        "type": "string",
                        "description": "Optional. Force a specific agent (e.g. 'aider', 'codex-cli').",
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="run_batch",
            description=(
                "Execute multiple tasks concurrently through Mahoraga. Tasks are routed through "
                "the bandit, grouped into resource-aware execution waves, and run in parallel where "
                "safe. Supports dependency chains via depends_on (0-indexed). Returns all results "
                "in a single response."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "default": [],
                                },
                                "expected_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "default": [],
                                },
                                "capability_hint": {
                                    "type": "string",
                                    "enum": ["code", "plan", "general"],
                                },
                            },
                            "required": ["prompt"],
                        },
                        "minItems": 1,
                        "maxItems": 10,
                    },
                    "parallel": {"type": "boolean", "default": True},
                    "max_concurrent": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="route_task",
            description=(
                "Dry-run: show which agent Mahoraga would select for a task without executing it. "
                "Returns keyword classification and UCB scores for all candidate agents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="agent_status",
            description=(
                "Show the status of all registered agents: online/offline, model info, "
                "resource group, and current queue depth."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="routing_stats",
            description=(
                "Get aggregate routing statistics: selection counts, reward distributions, "
                "and strategy performance."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["1h", "24h", "7d", "30d", "all"],
                        "default": "24h",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="switch_strategy",
            description="Switch Mahoraga's routing strategy at runtime (linucb, ucb1, thompson, static).",
            inputSchema={
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "enum": ["linucb", "ucb1", "thompson", "static"],
                    }
                },
                "required": ["strategy"],
            },
        ),
        Tool(
            name="recent_decisions",
            description="Retrieve recent routing decisions from Mahoraga's decision log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "agent_filter": {"type": "string"},
                    "capability_filter": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                    },
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "run_task": _handle_run_task,
        "run_batch": _handle_run_batch,
        "route_task": _handle_route_task,
        "agent_status": _handle_agent_status,
        "routing_stats": _handle_routing_stats,
        "switch_strategy": _handle_switch_strategy,
        "recent_decisions": _handle_recent_decisions,
    }
    handler = handlers.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    result = await handler(arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_run_task(args: dict) -> dict:
    body = {"prompt": args["prompt"]}
    if "capability_hint" in args:
        body["capability_hint"] = args["capability_hint"]
    if "agent_override" in args:
        body["agent_override"] = args["agent_override"]
    return await _post("/api/task", body)


async def _handle_run_batch(args: dict) -> dict:
    return await _post("/api/batch", args)


async def _handle_route_task(args: dict) -> dict:
    return await _post("/api/routing/dry-run", {"prompt": args["prompt"]})


async def _handle_agent_status(args: dict) -> dict:
    agents = await _get("/api/agents/status")
    if isinstance(agents, dict) and "error" in agents:
        return agents
    groups = await _get("/api/resource-groups")
    return {"agents": agents, "resource_groups": groups}


async def _handle_routing_stats(args: dict) -> dict:
    return await _get("/api/routing/stats", {"window": args.get("window", "24h")})


async def _handle_switch_strategy(args: dict) -> dict:
    return await _post("/api/routing/strategy", {"strategy": args["strategy"]})


async def _handle_recent_decisions(args: dict) -> dict:
    params: dict = {"limit": args.get("limit", 10)}
    if "agent_filter" in args:
        params["agent"] = args["agent_filter"]
    if "capability_filter" in args:
        params["capability"] = args["capability_filter"]
    return await _get("/api/routing/decisions", params)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run tests to confirm pass**

```bash
pytest backend/mcp/test_server.py -v
```

Expected:
```
PASSED test_handle_run_task_basic
PASSED test_handle_run_task_with_overrides
PASSED test_handle_route_task
PASSED test_handle_agent_status_merges_responses
PASSED test_handle_not_running_returns_error
PASSED test_handle_switch_strategy
PASSED test_handle_recent_decisions_with_agent_filter
7 passed
```

- [ ] **Step 7: Smoke test via stdio**

```bash
cd /Users/kaitosoeno/Projects/Mahoraga
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python -m backend.mcp.server
```

Expected: JSON response listing all 7 tools by name.

- [ ] **Step 8: Commit**

```bash
git add backend/mcp/__init__.py backend/mcp/server.py backend/mcp/test_server.py requirements.txt
git commit -m "feat: MCP stdio server with 7 tools — run_task, run_batch, route_task, agent_status, routing_stats, switch_strategy, recent_decisions"
```

---

### Task 5: wave_executor.py + parallel /api/batch

**Files:**
- Create: `backend/orchestrator/service/wave_executor.py`
- Modify: `backend/orchestrator/service/app.py` (replace sequential loop in `run_batch`)
- Test: `tests/orchestrator_v2/test_wave_executor.py` (new)

**Goal:** `WaveExecutor._build_waves()` partitions ready tasks into concurrent waves respecting (1) resource group caps, (2) file-path overlap, (3) global `max_concurrent` cap. `execute_batch()` runs waves with `asyncio.gather`.

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator_v2/test_wave_executor.py`:

```python
import pytest
from backend.orchestrator.service.wave_executor import WaveExecutor


class _FakeTask:
    def __init__(self, id: str, agent: str, files: list[str] | None = None, deps: list = None):
        self.id = id
        self.scope = files or []
        self.dependencies = deps or []
    # agent is stored in assignments dict, not on the task


def test_single_task_one_wave():
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama")]
    waves = ex._build_waves(tasks, {"t0": "ollama"})
    assert len(waves) == 1
    assert len(waves[0]) == 1


def test_two_tasks_different_resource_groups_same_wave():
    """ollama (local_ollama) + codex-cli (openai_api) can run concurrently."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama"), _FakeTask("t1", "codex-cli")]
    waves = ex._build_waves(tasks, {"t0": "ollama", "t1": "codex-cli"})
    assert len(waves) == 1
    assert len(waves[0]) == 2


def test_ollama_aider_must_be_sequential():
    """ollama + aider both hit local_ollama (max=1) — must be in separate waves."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama"), _FakeTask("t1", "aider")]
    waves = ex._build_waves(tasks, {"t0": "ollama", "t1": "aider"})
    assert len(waves) == 2


def test_file_overlap_forces_sequential():
    """Tasks writing to the same file go in separate waves."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli", files=["src/auth.py"]),
        _FakeTask("t1", "gemini-cli", files=["src/auth.py"]),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli"})
    assert len(waves) == 2


def test_no_file_overlap_same_wave():
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli", files=["src/hash.py"]),
        _FakeTask("t1", "gemini-cli", files=["src/validation.py"]),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli"})
    assert len(waves) == 1


def test_global_cap_limits_wave_size():
    """Even with heterogeneous groups, global max_concurrent=2 caps wave at 2."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli"),
        _FakeTask("t1", "gemini-cli"),
        _FakeTask("t2", "claude"),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli", "t2": "claude"})
    assert all(len(w) <= 2 for w in waves)


@pytest.mark.asyncio
async def test_execute_batch_calls_run_single():
    """execute_batch should call run_single for each task."""
    ex = WaveExecutor(max_concurrent=2)
    called = []

    class _FakeDep:
        def __init__(self, task_id):
            self.task_id = task_id

    t0 = _FakeTask("t0", "ollama")
    t1 = _FakeTask("t1", "codex-cli")

    async def _run_single(task, agent):
        called.append((task.id, agent))
        return {"status": "success", "task_index": 0}

    results = await ex.execute_batch([t0, t1], {"t0": "ollama", "t1": "codex-cli"}, _run_single)
    assert len(called) == 2
    assert len(results) == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/orchestrator_v2/test_wave_executor.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.service.wave_executor'`

- [ ] **Step 3: Create wave_executor.py**

Create `backend/orchestrator/service/wave_executor.py`:

```python
"""
Wave executor for concurrent batch task execution.

Groups ready tasks into waves constrained by:
  1. Resource group concurrency limits (ollama+aider share local_ollama, max=1)
  2. File-path overlap (tasks writing same file → different waves)
  3. Global max_concurrent cap (default: 2)

Each wave runs with asyncio.gather; waves execute sequentially.
"""
from __future__ import annotations
import asyncio
import logging

from ..resource_groups import get_resource_group, get_group_concurrency

logger = logging.getLogger(__name__)


class WaveExecutor:
    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent

    def _build_waves(self, ready_tasks: list, assignments: dict[str, str]) -> list[list]:
        """Partition tasks into concurrently-executable waves.

        A task moves to the next wave if:
        - Its resource group is already at capacity in the current wave, OR
        - The current wave is at the global max_concurrent cap, OR
        - Its expected_files (task.scope) overlap with a file already claimed this wave.
        """
        waves: list[list] = []
        unscheduled = list(ready_tasks)

        while unscheduled:
            wave: list = []
            group_counts: dict[str, int] = {}
            wave_files: set[str] = set()

            for task in list(unscheduled):
                agent = assignments.get(task.id, "unknown")
                group = get_resource_group(agent)
                group_limit = get_group_concurrency(group)
                current_count = group_counts.get(group, 0)

                if current_count >= group_limit:
                    continue
                if len(wave) >= self.max_concurrent:
                    continue

                task_files = set(getattr(task, "scope", None) or [])
                if task_files & wave_files:
                    continue  # file overlap — defer to next wave

                wave.append(task)
                unscheduled.remove(task)
                group_counts[group] = current_count + 1
                wave_files |= task_files

            if not wave:
                # Safety valve: can't schedule any remaining task together — run first one alone
                wave = [unscheduled.pop(0)]
                logger.warning(
                    "wave builder stalled — running task %s alone as fallback", wave[0].id
                )

            waves.append(wave)

        return waves

    async def execute_batch(
        self,
        tasks: list,
        assignments: dict[str, str],
        run_single,
    ) -> list[dict]:
        """Execute all tasks in dependency-aware waves.

        Args:
            tasks: ordered list of task objects (must have .id, .scope, .dependencies)
            assignments: task.id → agent_name (pre-computed by bandit)
            run_single: async callable(task, agent) -> dict result
        Returns:
            list of result dicts in task order
        """
        completed: dict[str, dict] = {}
        remaining = list(tasks)
        wave_num = 0

        while remaining:
            ready = [t for t in remaining if self._deps_satisfied(t, completed)]
            if not ready:
                logger.warning(
                    "no ready tasks with %d remaining — possible dependency cycle", len(remaining)
                )
                break

            waves = self._build_waves(ready, {t.id: assignments.get(t.id, "unknown") for t in ready})

            for wave in waves:
                wave_num += 1
                logger.info(
                    "wave %d: executing %d tasks %s",
                    wave_num, len(wave), [t.id for t in wave],
                )
                wave_results = await asyncio.gather(
                    *[run_single(t, assignments.get(t.id, "unknown")) for t in wave],
                    return_exceptions=True,
                )
                for task, result in zip(wave, wave_results):
                    if isinstance(result, Exception):
                        result = {"status": "failed", "error": str(result), "task_index": 0}
                    completed[task.id] = {**result, "wave": wave_num}
                    remaining.remove(task)

        return [completed[t.id] for t in tasks if t.id in completed]

    @staticmethod
    def _deps_satisfied(task, completed: dict[str, dict]) -> bool:
        for dep in getattr(task, "dependencies", []):
            if dep.task_id not in completed:
                return False
        return True
```

- [ ] **Step 4: Run wave_executor tests**

```bash
pytest tests/orchestrator_v2/test_wave_executor.py -v
```

Expected:
```
PASSED test_single_task_one_wave
PASSED test_two_tasks_different_resource_groups_same_wave
PASSED test_ollama_aider_must_be_sequential
PASSED test_file_overlap_forces_sequential
PASSED test_no_file_overlap_same_wave
PASSED test_global_cap_limits_wave_size
PASSED test_execute_batch_calls_run_single
7 passed
```

- [ ] **Step 5: Replace sequential loop in run_batch with wave executor**

In `backend/orchestrator/service/app.py`, replace the `run_batch` function with this implementation (keep the same signature — only the body changes after task creation):

After creating all tasks and saving them, replace the `while remaining:` sequential loop with:

```python
    # Pre-route all tasks through bandit
    assignments: dict[str, str] = {}
    for task in created:
        assignments[task.id] = router.route(task)

    sequential_s = 0.0

    async def _run_single(task: Task, agent: str) -> dict:
        nonlocal sequential_s
        adapter = adapter_reg.get(agent)
        t_run = dataclasses.replace(task, preferred_worker_type=adapter.worker_id) if adapter else task
        await store.tasks.update_status(t_run.id, TaskStatus.ready)

        t0 = _time.time()
        await _run_task(t_run.id, store, registry, verifier)
        elapsed = round(_time.time() - t0, 2)
        sequential_s += elapsed

        t_result = await store.tasks.get(t_run.id)
        artifacts = await store.artifacts.list_by_task(t_run.id)
        output = next(
            (a.location.get("content", "") for a in artifacts if a.type == "text_output"), ""
        )
        task_index = next(i for i, t in enumerate(created) if t.id == task.id)
        return {
            "task_index": task_index,
            "status": "success" if t_result.status == TaskStatus.completed else "failed",
            "agent": agent,
            "resource_group": get_resource_group(agent),
            "elapsed_s": elapsed,
            "output": output,
        }

    if req.parallel:
        from .wave_executor import WaveExecutor
        wave_exec = WaveExecutor(max_concurrent=req.max_concurrent)
        all_results = await wave_exec.execute_batch(created, assignments, _run_single)
        waves_executed = max((r.get("wave", 1) for r in all_results), default=1)
    else:
        # Sequential fallback (parallel=false safety valve)
        all_results = []
        for i, task in enumerate(created):
            result = await _run_single(task, assignments[task.id])
            result["wave"] = i + 1
            all_results.append(result)
        waves_executed = len(all_results)

    total_elapsed = round(_time.time() - t_batch_start, 2)
    speedup = round(sequential_s / total_elapsed, 2) if total_elapsed > 0 else 1.0

    return {
        "batch_id": batch_id,
        "total_wall_clock_s": total_elapsed,
        "sequential_estimate_s": round(sequential_s, 2),
        "speedup": f"{speedup}x",
        "waves_executed": waves_executed,
        "results": sorted(all_results, key=lambda r: r.get("task_index", 0)),
    }
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ backend/orchestrator/routing/tests/ backend/mcp/ -v --tb=short 2>&1 | tail -20
```

Expected: All previously-passing tests still pass; new wave executor tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/service/wave_executor.py \
        backend/orchestrator/service/app.py \
        tests/orchestrator_v2/test_wave_executor.py
git commit -m "feat: WaveExecutor with resource-group + file-overlap constraints; parallel /api/batch"
```

---

### Task 6: queue_depth_norm as 9th LinUCB Feature

**Files:**
- Modify: `backend/orchestrator/routing/context.py`
- Modify: `backend/orchestrator/routing/strategies/linucb.py`
- Modify: `backend/orchestrator/routing/bandit_router.py`

**Goal:** Add `queue_depth_norm` as the 9th feature in `TaskContext`. The bandit learns organically that routing to a congested resource group produces lower speed rewards. No hard scheduling rules.

**Breaking change:** `d` changes from 8 → 9. Delete `~/.mahoraga/bandit_state.json` after deploying (the load path has a try/except for exactly this case — it will silently start fresh).

- [ ] **Step 1: Write the failing tests**

Add to `backend/orchestrator/routing/tests/test_score_all.py`:

```python
def test_context_has_9_features():
    class _T:
        goal = "write a function"
        tier = 2
    ctx = TaskContext.from_task(_T())
    assert ctx.d == 9
    assert ctx.to_vector().shape == (9,)


def test_linucb_default_d_is_9():
    router = LinUCBRouter()
    assert router.d == 9


def test_queue_depth_norm_defaults_to_zero():
    class _T:
        goal = "create a file"
        tier = 2
    ctx = TaskContext.from_task(_T())
    assert ctx.queue_depth_norm == 0.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest backend/orchestrator/routing/tests/test_score_all.py::test_context_has_9_features \
       backend/orchestrator/routing/tests/test_score_all.py::test_linucb_default_d_is_9 -v 2>&1 | head -15
```

Expected: `AssertionError: assert 8 == 9`

- [ ] **Step 3: Add queue_depth_norm to TaskContext**

In `backend/orchestrator/routing/context.py`, replace the `@dataclass` definition:

```python
@dataclass
class TaskContext:
    word_count_norm: float
    code_keyword_density: float
    is_question: float
    complexity_tier: float
    file_count: float
    has_error_keywords: float
    has_creation_keywords: float
    has_research_keywords: float
    queue_depth_norm: float = 0.0  # fraction of resource group capacity in use at selection time

    QUEUE_DEPTH_CAP: float = 5.0   # normalize queue depth by this cap

    @property
    def d(self) -> int:
        return 9

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.word_count_norm,
            self.code_keyword_density,
            self.is_question,
            self.complexity_tier,
            self.file_count,
            self.has_error_keywords,
            self.has_creation_keywords,
            self.has_research_keywords,
            self.queue_depth_norm,
        ], dtype=np.float64)
```

The `from_task()` classmethod is unchanged — it defaults `queue_depth_norm=0.0`.

- [ ] **Step 4: Update LinUCB default dimension**

In `backend/orchestrator/routing/strategies/linucb.py`, change:

```python
def __init__(
    self,
    d: int = 9,   # was 8; added queue_depth_norm feature
    alpha: float = 1.0,
    decay: float = 1.0,
    priors: dict[str, float] | None = None,
):
```

- [ ] **Step 5: Accept queue_depth_norm in BanditRouter.route() and score_all()**

In `backend/orchestrator/routing/bandit_router.py`, update `route()`:

```python
def route(self, task, available_agents: list[str] | None = None, queue_depth_norm: float = 0.0) -> str:
    """Select the best agent for this task. Returns agent name."""
    context = TaskContext.from_task(task)
    if queue_depth_norm > 0.0:
        import dataclasses as _dc
        context = _dc.replace(context, queue_depth_norm=queue_depth_norm)
    available = available_agents if available_agents is not None else self._available_agents()
    if not available:
        raise RuntimeError("No agents registered in the adapter registry")
    agent = self.strategy.select_agent(context, available)
    self.logger.log_decision(
        task=task, context=context, selected_agent=agent,
        available_agents=available, strategy=self.strategy.name,
        scores=self.strategy.get_scores(),
    )
    return agent
```

Update `score_all()`:

```python
def score_all(self, task, available_agents: list[str] | None = None, queue_depth_norm: float = 0.0) -> dict:
    """Read-only UCB scoring for dry-run routing."""
    context = TaskContext.from_task(task)
    if queue_depth_norm > 0.0:
        import dataclasses as _dc
        context = _dc.replace(context, queue_depth_norm=queue_depth_norm)
    available = available_agents if available_agents is not None else self._available_agents()
    scores = self.strategy.compute_scores(context, available)
    return {"strategy": self.strategy.name, "scores": scores}
```

- [ ] **Step 6: Delete stale bandit state (d=8 incompatible)**

```bash
rm -f ~/.mahoraga/bandit_state.json
```

- [ ] **Step 7: Run full routing test suite**

```bash
pytest backend/orchestrator/routing/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: All tests pass. The LinUCB state tests re-run with `d=9`.

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/routing/context.py \
        backend/orchestrator/routing/strategies/linucb.py \
        backend/orchestrator/routing/bandit_router.py
git commit -m "feat: add queue_depth_norm as 9th LinUCB feature for congestion-aware routing"
```

**Ablation note (manual):** After a run of 50+ tasks, compare the regret curve from `routing_decisions.db` with the pre-d9 baseline. The sequential benchmark should show comparable or better regret — `queue_depth_norm` is always 0.0 in sequential mode, so it adds one zero-valued feature that the bandit ignores. Concurrent batches should eventually show load-spreading behaviour as the speed component of the reward signal penalises congested backends.

---

### Task 7: Superpowers Skill — mahoraga.md

**Files:**
- Create: `docs/superpowers/mahoraga-skill.md` (in-repo reference copy)

**Goal:** A skill that tells Claude Code when and how to use Mahoraga's 7 MCP tools, so it invokes them without being asked.

- [ ] **Step 1: Create the in-repo skill file**

Create `docs/superpowers/mahoraga-skill.md`:

```markdown
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
```

- [ ] **Step 2: Install as Superpowers skill**

```bash
# Locate the superpowers skill directory (adjust if different)
SKILL_DIR=$(ls ~/.claude/plugins/*/skills/ 2>/dev/null | head -1 | xargs dirname)
echo "Installing to: $SKILL_DIR"

mkdir -p "$SKILL_DIR"
cp docs/superpowers/mahoraga-skill.md "$SKILL_DIR/mahoraga.md"
```

- [ ] **Step 3: Verify skill appears**

In a fresh Claude Code session:
```
/skills
```

Expected: `mahoraga` appears in the skill list.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/mahoraga-skill.md
git commit -m "docs: mahoraga Superpowers skill — MCP tool usage guide"
```

---

## Self-Review Against Specs

### PARALLEL_ROUTING_SPEC

| Requirement | Task |
|---|---|
| Resource group registry + lookup functions | Task 2 |
| local_ollama(1), openai_api(2), google_api(3), anthropic_api(2), unknown(1) | Task 2 |
| Wave executor with resource group caps | Task 5 |
| File-path overlap detection (`scope` field) | Task 5 |
| Global `max_concurrent=2` cap | Task 5 |
| `asyncio.gather` per wave | Task 5 |
| `queue_depth_norm` as 9th LinUCB feature | Task 6 |
| `POST /api/batch` with `parallel` + `max_concurrent` params | Tasks 3 & 5 |
| Batch response: `speedup`, `waves_executed`, per-task `wave` | Tasks 3 & 5 |

### MCP_SERVER_SPEC

| Requirement | Task |
|---|---|
| `score_all()` on BanditRouter (read-only, no side effects) | Task 1 |
| `POST /api/routing/dry-run` | Task 3 |
| `GET /api/routing/decisions` | Task 3 |
| `GET /api/resource-groups` | Task 3 |
| `POST /api/task` | Task 3 |
| `POST /api/batch` | Tasks 3 & 5 |
| `backend/mcp/server.py` stdio bridge | Task 4 |
| All 7 MCP tools implemented | Task 4 |
| `mcp>=1.0.0` in requirements.txt | Task 4 |
| Superpowers skill `mahoraga.md` | Task 7 |

### Gaps / Deferred

- **Concurrent reward discounting** (spec §5): rewards from concurrent execution are not discounted. The bandit still learns, but noisy concurrent rewards aren't flagged. v2 item.
- **Ablation re-run**: Task 6 deletes `bandit_state.json` and resets the model. The ablation comparison is a manual step after accumulating 50+ decisions in the new format.
- **`/api/task` quality score**: hardcoded at `0.8` for successful tasks. Full quality scoring requires verifier integration with the `quality_score` field in `TaskOutcome`.
