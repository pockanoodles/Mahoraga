# Rankings, Evaluation, and Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local-first agent rankings, A/B routing evaluation, and one-command agent onboarding — answering "which agent is best for what" and "does Mahoraga routing actually help."

**Architecture:** Four phases. Phase 1 adds eval infrastructure: task suite YAML, a `/api/eval/task` endpoint that calls real workers, and the `orch eval ab` CLI that measures routing ON vs OFF. Phase 2 adds ranking aggregation over live task_metrics + benchmark harness data, exposing `orch rankings` and `/api/rankings`. Phase 3 adds `orch agent add` (one-command onboarding: health + smoke + benchmark) and `orch benchmark refresh`. Phase 4 adds a Rankings sidebar tab in the web UI. All state is in local SQLite via two new store classes that share the main `_store._conn`, following the existing `AdaptiveStore` pattern.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, Typer, PyYAML, httpx (already in requirements). No new dependencies.

---

## File Map

```
NEW:
eval/tasks/default_ab.yaml                           — 18-task A/B eval suite
eval/tasks/smoke.yaml                                — 3-task smoke suite for onboarding
backend/orchestrator/eval/__init__.py                — package marker
backend/orchestrator/eval/task_suite.py              — TaskDef/TaskSuite dataclasses + load_suite()
backend/orchestrator/eval/runner.py                  — run_ab_eval(), summarize_ab(), print_ab_report()
backend/orchestrator/store/eval_store.py             — EvalStore: routing_runs + routing_run_tasks
backend/orchestrator/store/rankings_store.py         — RankingsStore: benchmark_runs + model_rankings
backend/orchestrator/rankings/__init__.py            — package marker
backend/orchestrator/rankings/aggregator.py          — wilson_interval(), rebuild_rankings()
backend/orchestrator/routing/benchmark/agent_benchmark.py — run_agent_benchmark() for onboarding
backend/orchestrator/cli/commands/eval.py            — orch eval ab
backend/orchestrator/cli/commands/rankings.py        — orch rankings
backend/orchestrator/cli/commands/agent_cmd.py       — orch agent add

MODIFIED:
backend/orchestrator/cli/main.py                     — register eval_app, rankings_app, agent_app
backend/orchestrator/service/app.py                  — add /api/eval/* and /api/rankings endpoints, add EvalStore/RankingsStore to lifespan
backend/orchestrator/routing/benchmark/benchmark.py  — add `refresh` subcommand
static/index.html                                    — add Rankings collapsible section
static/sidebar.js                                    — fetch /api/rankings, render table + filters

TESTS:
tests/orchestrator_v2/test_eval_store.py
tests/orchestrator_v2/test_rankings_store.py
tests/orchestrator_v2/test_rankings_aggregator.py
tests/orchestrator_v2/test_task_suite.py
```

---

## Phase 1: Eval Infrastructure

### Task 1: Task Suite Files + Loader

**Files:**
- Create: `eval/tasks/default_ab.yaml`
- Create: `eval/tasks/smoke.yaml`
- Create: `backend/orchestrator/eval/__init__.py`
- Create: `backend/orchestrator/eval/task_suite.py`
- Test: `tests/orchestrator_v2/test_task_suite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator_v2/test_task_suite.py
from pathlib import Path
import pytest
from backend.orchestrator.eval.task_suite import load_suite, TaskSuite, TaskDef

_YAML = """
suite: test_suite
seed: 42
tasks:
  - id: code_1
    text: "write a hello world function"
    bucket: code
    difficulty: simple
    tags: [easy]
  - id: debug_1
    text: "find the bug in this code"
    bucket: debug
    difficulty: medium
"""

def test_load_suite_from_string(tmp_path):
    f = tmp_path / "suite.yaml"
    f.write_text(_YAML)
    suite = load_suite(f)
    assert suite.name == "test_suite"
    assert suite.seed == 42
    assert len(suite.tasks) == 2
    assert suite.tasks[0].id == "code_1"
    assert suite.tasks[0].bucket == "code"
    assert suite.tasks[0].difficulty == "simple"
    assert suite.tasks[1].bucket == "debug"

def test_task_def_defaults():
    t = TaskDef(id="x", text="hello", bucket="code", difficulty="simple")
    assert t.tags == []
    assert t.timeout_s is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/orchestrator_v2/test_task_suite.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create the task suite files**

Create `eval/tasks/default_ab.yaml`:
```yaml
suite: default_ab
seed: 42
tasks:
  - id: code_simple_1
    text: "Write a Python function that returns the median of a list of numbers"
    bucket: code
    difficulty: simple
  - id: code_medium_1
    text: "Implement a Python class for a thread-safe LRU cache with get and put methods"
    bucket: code
    difficulty: medium
  - id: debug_simple_1
    text: "Find the bug in this function: def add(a, b): return a - b"
    bucket: debug
    difficulty: simple
  - id: debug_medium_1
    text: "Explain what causes this error and how to fix it: AttributeError: 'NoneType' object has no attribute 'split'"
    bucket: debug
    difficulty: medium
  - id: refactor_simple_1
    text: "Rename the variable x to count in this function: def f(x): return x * 2"
    bucket: refactor
    difficulty: simple
  - id: refactor_medium_1
    text: "Refactor this code to use a list comprehension: results = []\nfor item in items:\n    if item > 0:\n        results.append(item * 2)"
    bucket: refactor
    difficulty: medium
  - id: research_simple_1
    text: "What is the difference between a list and a tuple in Python?"
    bucket: research
    difficulty: simple
  - id: research_medium_1
    text: "Compare SQLite and PostgreSQL for a local single-user development tool. Which should you choose and why?"
    bucket: research
    difficulty: medium
  - id: plan_simple_1
    text: "Outline the steps to set up a Python virtual environment and install a requirements.txt"
    bucket: plan
    difficulty: simple
  - id: plan_medium_1
    text: "Design a simple REST API for a todo list app: list the endpoints, HTTP methods, and data model"
    bucket: plan
    difficulty: medium
  - id: test_simple_1
    text: "Write a pytest test for a function add(a, b) that returns a + b"
    bucket: test
    difficulty: simple
  - id: test_medium_1
    text: "Write pytest tests for a Stack class with push, pop, peek, and is_empty methods, covering edge cases including empty stack"
    bucket: test
    difficulty: medium
  - id: review_simple_1
    text: "Review this code for obvious problems: def divide(a, b): return a / b"
    bucket: review
    difficulty: simple
  - id: review_medium_1
    text: "Review this Python function for security issues: def get_user(uid): return db.execute(f'SELECT * FROM users WHERE id={uid}')"
    bucket: review
    difficulty: medium
  - id: security_simple_1
    text: "What is SQL injection and how do you prevent it?"
    bucket: security
    difficulty: simple
  - id: security_medium_1
    text: "Review this authentication check for vulnerabilities: if password == stored_password: grant_access()"
    bucket: security
    difficulty: medium
  - id: code_complex_1
    text: "Implement a generic event bus in Python with subscribe, unsubscribe, and publish methods, with type-safe handlers"
    bucket: code
    difficulty: complex
  - id: debug_complex_1
    text: "Given this traceback from a FastAPI app with async SQLite, diagnose and fix the 'RuntimeError: no running event loop' error that occurs only under load"
    bucket: debug
    difficulty: complex
```

Create `eval/tasks/smoke.yaml`:
```yaml
suite: smoke
seed: 42
tasks:
  - id: smoke_explain_1
    text: "What is 2 + 2? Answer with just the number."
    bucket: research
    difficulty: simple
    timeout_s: 30
  - id: smoke_code_1
    text: "Write a Python function called hello() that returns the string 'hello world'"
    bucket: code
    difficulty: simple
    timeout_s: 60
  - id: smoke_general_1
    text: "What does the ls command do in Unix? One sentence answer."
    bucket: research
    difficulty: simple
    timeout_s: 30
```

Create `backend/orchestrator/eval/__init__.py`:
```python
```

- [ ] **Step 4: Create the task suite loader**

Create `backend/orchestrator/eval/task_suite.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class TaskDef:
    id: str
    text: str
    bucket: str
    difficulty: str
    tags: list[str] = field(default_factory=list)
    timeout_s: float | None = None
    expected_artifacts: list[str] = field(default_factory=list)


@dataclass
class TaskSuite:
    name: str
    seed: int
    tasks: list[TaskDef]


def load_suite(path: Path) -> TaskSuite:
    raw = yaml.safe_load(path.read_text())
    tasks = [
        TaskDef(
            id=t["id"],
            text=t["text"],
            bucket=t["bucket"],
            difficulty=t["difficulty"],
            tags=t.get("tags", []),
            timeout_s=t.get("timeout_s"),
            expected_artifacts=t.get("expected_artifacts", []),
        )
        for t in raw["tasks"]
    ]
    return TaskSuite(name=raw["suite"], seed=raw.get("seed", 42), tasks=tasks)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/orchestrator_v2/test_task_suite.py -v
```
Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add eval/tasks/default_ab.yaml eval/tasks/smoke.yaml \
    backend/orchestrator/eval/__init__.py \
    backend/orchestrator/eval/task_suite.py \
    tests/orchestrator_v2/test_task_suite.py
git commit -m "feat: add task suite schema, default_ab.yaml, smoke.yaml, and loader"
```

---

### Task 2: EvalStore

**Files:**
- Create: `backend/orchestrator/store/eval_store.py`
- Test: `tests/orchestrator_v2/test_eval_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator_v2/test_eval_store.py
import asyncio
import pytest
import aiosqlite
from backend.orchestrator.store.eval_store import EvalStore


@pytest.fixture
async def store():
    async with aiosqlite.connect(":memory:") as conn:
        s = EvalStore(conn)
        await s.migrate()
        yield s


@pytest.mark.asyncio
async def test_create_and_finish_run(store):
    run_id = await store.create_run(
        run_type="ab_off",
        routing_enabled=False,
        baseline_policy="fixed:ollama:general",
        suite_name="default_ab",
    )
    assert isinstance(run_id, int)
    assert run_id > 0
    await store.finish_run(run_id)


@pytest.mark.asyncio
async def test_insert_run_task(store):
    run_id = await store.create_run("ab_on", True, None, "default_ab")
    await store.insert_run_task(
        run_id=run_id,
        task_id="code_simple_1",
        task_text="write hello world",
        bucket="code",
        difficulty="simple",
        selected_agent="ollama:general",
        latency_ms=1200.0,
        success=True,
        reward=0.7,
    )
    results = await store.get_run_tasks(run_id)
    assert len(results) == 1
    assert results[0]["selected_agent"] == "ollama:general"
    assert results[0]["success"] == 1


@pytest.mark.asyncio
async def test_get_ab_summary(store):
    off_id = await store.create_run("ab_off", False, "fixed:ollama:general", "default_ab")
    on_id = await store.create_run("ab_on", True, None, "default_ab")

    for run_id, agent, latency, success in [
        (off_id, "ollama:general", 2000.0, True),
        (off_id, "ollama:general", 3000.0, False),
        (on_id, "claude:sonnet", 1500.0, True),
        (on_id, "ollama:general", 1200.0, True),
    ]:
        await store.insert_run_task(
            run_id=run_id, task_id="t1", task_text="x",
            bucket="code", difficulty="simple",
            selected_agent=agent, latency_ms=latency, success=success,
        )

    off_summary = await store.get_run_summary(off_id)
    on_summary = await store.get_run_summary(on_id)
    assert off_summary["success_rate"] == pytest.approx(0.5)
    assert on_summary["success_rate"] == pytest.approx(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/orchestrator_v2/test_eval_store.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement EvalStore**

Create `backend/orchestrator/store/eval_store.py`:
```python
from __future__ import annotations
import time
import aiosqlite


def _now() -> str:
    return str(time.time())


class EvalStore:
    """Stores A/B evaluation runs and per-task results."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS routing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                routing_enabled INTEGER NOT NULL DEFAULT 1,
                baseline_policy TEXT,
                task_suite_name TEXT NOT NULL DEFAULT '',
                repeat_index INTEGER NOT NULL DEFAULT 0,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS routing_run_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                task_text TEXT NOT NULL,
                bucket TEXT NOT NULL DEFAULT 'general',
                difficulty TEXT NOT NULL DEFAULT 'medium',
                selected_agent TEXT NOT NULL,
                worker_id TEXT,
                ttft_ms REAL,
                latency_ms REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                quality_score REAL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                escalation_count INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                reward REAL,
                final_status TEXT NOT NULL DEFAULT 'complete',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES routing_runs(id)
            );
        """)
        await self._conn.commit()

    async def create_run(
        self,
        run_type: str,
        routing_enabled: bool,
        baseline_policy: str | None,
        suite_name: str,
        repeat_index: int = 0,
        notes: str | None = None,
    ) -> int:
        cursor = await self._conn.execute(
            """INSERT INTO routing_runs
               (run_type, started_at, routing_enabled, baseline_policy, task_suite_name, repeat_index, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_type, _now(), int(routing_enabled), baseline_policy, suite_name, repeat_index, notes),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def finish_run(self, run_id: int) -> None:
        await self._conn.execute(
            "UPDATE routing_runs SET finished_at = ? WHERE id = ?",
            (_now(), run_id),
        )
        await self._conn.commit()

    async def insert_run_task(
        self,
        run_id: int,
        task_id: str,
        task_text: str,
        bucket: str,
        difficulty: str,
        selected_agent: str,
        latency_ms: float,
        success: bool,
        reward: float | None = None,
        ttft_ms: float | None = None,
        quality_score: float | None = None,
        retry_count: int = 0,
        escalation_count: int = 0,
        cost_usd: float = 0.0,
        worker_id: str | None = None,
        final_status: str = "complete",
    ) -> None:
        await self._conn.execute(
            """INSERT INTO routing_run_tasks
               (run_id, task_id, task_text, bucket, difficulty, selected_agent, worker_id,
                ttft_ms, latency_ms, success, quality_score, retry_count, escalation_count,
                cost_usd, reward, final_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, task_text, bucket, difficulty, selected_agent, worker_id,
             ttft_ms, latency_ms, int(success), quality_score, retry_count,
             escalation_count, cost_usd, reward, final_status, _now()),
        )
        await self._conn.commit()

    async def get_run_tasks(self, run_id: int) -> list[dict]:
        async with self._conn.execute(
            "SELECT * FROM routing_run_tasks WHERE run_id = ? ORDER BY id",
            (run_id,),
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]

    async def get_run_summary(self, run_id: int) -> dict:
        tasks = await self.get_run_tasks(run_id)
        if not tasks:
            return {"n": 0, "success_rate": 0.0, "median_latency_ms": None, "mean_reward": None}
        latencies = sorted(t["latency_ms"] for t in tasks)
        successes = [t["success"] for t in tasks]
        rewards = [t["reward"] for t in tasks if t["reward"] is not None]
        return {
            "n": len(tasks),
            "success_rate": sum(successes) / len(successes),
            "median_latency_ms": latencies[len(latencies) // 2],
            "p90_latency_ms": latencies[int(len(latencies) * 0.9)],
            "mean_reward": sum(rewards) / len(rewards) if rewards else None,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/orchestrator_v2/test_eval_store.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/store/eval_store.py \
    tests/orchestrator_v2/test_eval_store.py
git commit -m "feat: add EvalStore with routing_runs and routing_run_tasks tables"
```

---

### Task 3: Eval FastAPI Endpoints

**Files:**
- Modify: `backend/orchestrator/service/app.py`

The eval endpoint calls workers directly using the pattern already established by `apply_implicit_reward` in `bandit_router.py`: construct a minimal task object with `title` and `goal`, then use `TaskAttempt.new(task_id, worker_id)`.

- [ ] **Step 1: Add imports and global to app.py**

At the top of `backend/orchestrator/service/app.py`, add to existing imports:
```python
from ..store.eval_store import EvalStore
from ..domain.models import TaskAttempt
```

After the existing globals block (after `_implicit_tracker`), add:
```python
_eval_store: EvalStore | None = None


def get_eval_store() -> EvalStore:
    assert _eval_store is not None
    return _eval_store


EvalStoreDep = Annotated[EvalStore, Depends(get_eval_store)]
```

- [ ] **Step 2: Add EvalStore to lifespan in app.py**

Inside `lifespan()`, after the existing `_cost_ledger` block (around line 219), add:
```python
    global _eval_store
    _eval_store = EvalStore(_store._conn)
    await _eval_store.migrate()
```

- [ ] **Step 3: Add Pydantic models and eval endpoints to app.py**

Add after the existing Pydantic request models (search for the `_ChatRequest` class, add after it):
```python
class _EvalStartRequest(BaseModel):
    run_type: str
    routing_enabled: bool
    baseline_policy: str | None = None
    suite_name: str
    repeat_index: int = 0


class _EvalTaskRequest(BaseModel):
    text: str
    bucket: str = "general"
    difficulty: str = "medium"
    routing_mode: str = "adaptive"  # "adaptive" | "fixed:agent_id"
    run_id: int | None = None
    task_id: str | None = None


class _EvalTaskResult(BaseModel):
    agent: str
    latency_ms: float
    ttft_ms: float | None
    success: bool
    reward: float | None
    output_preview: str


class _EvalFinishRequest(BaseModel):
    run_id: int
```

Add the three eval endpoints (place them near the existing `/api/` routes):
```python
@app.post("/api/eval/start")
async def eval_start(
    req: _EvalStartRequest,
    eval_store: EvalStoreDep,
) -> dict:
    run_id = await eval_store.create_run(
        run_type=req.run_type,
        routing_enabled=req.routing_enabled,
        baseline_policy=req.baseline_policy,
        suite_name=req.suite_name,
        repeat_index=req.repeat_index,
    )
    return {"run_id": run_id}


@app.post("/api/eval/task", response_model=_EvalTaskResult)
async def eval_task(
    req: _EvalTaskRequest,
    registry: RegistryDep,
    bandit: Annotated[BanditRouter, Depends(get_bandit_router)],
    eval_store: EvalStoreDep,
) -> _EvalTaskResult:
    import dataclasses
    import time

    # Select agent
    if req.routing_mode.startswith("fixed:"):
        agent_id = req.routing_mode.removeprefix("fixed:")
    else:
        @dataclasses.dataclass
        class _EvalTask:
            title: str
            goal: str
        agent_id = bandit.route(_EvalTask(title=req.text, goal=req.text))

    # Execute via worker
    worker = registry.get(agent_id)
    task_id = req.task_id or str(__import__("uuid").uuid4())

    @dataclasses.dataclass
    class _EvalTaskObj:
        id: str
        goal: str
        title: str
        scope: str = ""
        context_refs: list = dataclasses.field(default_factory=list)
        constraints: list = dataclasses.field(default_factory=list)
        done_criteria: str = ""

    task_obj = _EvalTaskObj(id=task_id, goal=req.text, title=req.text[:80])
    attempt = TaskAttempt.new(task_id=task_id, worker_id=agent_id)

    start = time.monotonic()
    ttft_ms: float | None = None
    output_parts: list[str] = []
    success = False

    try:
        async for event in worker.execute(attempt, task_obj, None):
            if ttft_ms is None:
                ttft_ms = (time.monotonic() - start) * 1000
            if event.type == "attempt.completed":
                success = True
                output_parts.append(event.payload.get("summary", ""))
            elif event.type == "attempt.failed":
                success = False
    except Exception:
        success = False

    latency_ms = (time.monotonic() - start) * 1000
    reward = 0.7 if success else 0.0

    if req.run_id is not None:
        await eval_store.insert_run_task(
            run_id=req.run_id,
            task_id=task_id,
            task_text=req.text,
            bucket=req.bucket,
            difficulty=req.difficulty,
            selected_agent=agent_id,
            latency_ms=latency_ms,
            success=success,
            reward=reward,
            ttft_ms=ttft_ms,
        )

    return _EvalTaskResult(
        agent=agent_id,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        success=success,
        reward=reward,
        output_preview="".join(output_parts)[:200],
    )


@app.post("/api/eval/finish")
async def eval_finish(
    req: _EvalFinishRequest,
    eval_store: EvalStoreDep,
) -> dict:
    await eval_store.finish_run(req.run_id)
    return {"ok": True}
```

- [ ] **Step 4: Start the server and smoke-test the endpoints**

```bash
cd ~/Projects/Mahoraga && python -m backend.main &
sleep 3
curl -s -X POST http://localhost:8001/api/eval/start \
  -H "Content-Type: application/json" \
  -d '{"run_type":"test","routing_enabled":false,"suite_name":"smoke"}' | python3 -m json.tool
```
Expected: `{"run_id": 1}` (or any positive integer)

```bash
curl -s -X POST http://localhost:8001/api/eval/finish \
  -H "Content-Type: application/json" \
  -d '{"run_id": 1}' | python3 -m json.tool
```
Expected: `{"ok": true}`

Kill the server after testing: `kill %1`

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/service/app.py \
    backend/orchestrator/store/eval_store.py
git commit -m "feat: add /api/eval/start, /api/eval/task, /api/eval/finish endpoints"
```

---

### Task 4: A/B Eval Runner + orch eval ab CLI

**Files:**
- Create: `backend/orchestrator/eval/runner.py`
- Create: `backend/orchestrator/cli/commands/eval.py`

- [ ] **Step 1: Create the eval runner**

Create `backend/orchestrator/eval/runner.py`:
```python
from __future__ import annotations
import asyncio
import statistics
from dataclasses import dataclass
from pathlib import Path

import httpx

from .task_suite import TaskSuite, load_suite

BASE_URL = "http://localhost:8001"


@dataclass
class ABSummary:
    suite_name: str
    n_tasks: int
    baseline_agent: str
    off_median_latency_ms: float | None
    on_median_latency_ms: float | None
    off_p90_latency_ms: float | None
    on_p90_latency_ms: float | None
    off_success_rate: float
    on_success_rate: float
    off_mean_reward: float | None
    on_mean_reward: float | None
    off_results: list[dict]
    on_results: list[dict]


async def run_suite(
    suite: TaskSuite,
    routing_mode: str,
    baseline_policy: str | None,
    run_type: str,
    client: httpx.AsyncClient,
) -> tuple[int, list[dict]]:
    r = await client.post(f"{BASE_URL}/api/eval/start", json={
        "run_type": run_type,
        "routing_enabled": routing_mode == "adaptive",
        "baseline_policy": baseline_policy,
        "suite_name": suite.name,
    })
    r.raise_for_status()
    run_id = r.json()["run_id"]

    results = []
    for task in suite.tasks:
        try:
            resp = await client.post(f"{BASE_URL}/api/eval/task", json={
                "run_id": run_id,
                "task_id": task.id,
                "text": task.text,
                "bucket": task.bucket,
                "difficulty": task.difficulty,
                "routing_mode": routing_mode,
            }, timeout=task.timeout_s or 120.0)
            resp.raise_for_status()
            results.append({"task_id": task.id, "bucket": task.bucket,
                            "difficulty": task.difficulty, **resp.json()})
        except Exception as e:
            results.append({"task_id": task.id, "bucket": task.bucket,
                            "difficulty": task.difficulty, "success": False,
                            "latency_ms": 0.0, "reward": 0.0, "error": str(e)})

    await client.post(f"{BASE_URL}/api/eval/finish", json={"run_id": run_id})
    return run_id, results


def _summarize_results(results: list[dict]) -> tuple[float | None, float | None, float, float | None]:
    latencies = sorted(r["latency_ms"] for r in results if r.get("latency_ms"))
    successes = [int(r.get("success", False)) for r in results]
    rewards = [r["reward"] for r in results if r.get("reward") is not None]
    median_lat = latencies[len(latencies) // 2] if latencies else None
    p90_lat = latencies[int(len(latencies) * 0.9)] if latencies else None
    success_rate = sum(successes) / len(successes) if successes else 0.0
    mean_reward = statistics.mean(rewards) if rewards else None
    return median_lat, p90_lat, success_rate, mean_reward


async def run_ab_eval(
    suite_path: Path,
    baseline_agent: str = "ollama:general",
    repeat: int = 1,
) -> ABSummary:
    suite = load_suite(suite_path)
    async with httpx.AsyncClient(timeout=300.0) as client:
        _, off_results = await run_suite(
            suite, f"fixed:{baseline_agent}", f"fixed:{baseline_agent}", "ab_off", client
        )
        _, on_results = await run_suite(suite, "adaptive", None, "ab_on", client)

    off_med, off_p90, off_sr, off_rwd = _summarize_results(off_results)
    on_med, on_p90, on_sr, on_rwd = _summarize_results(on_results)

    return ABSummary(
        suite_name=suite.name,
        n_tasks=len(suite.tasks),
        baseline_agent=baseline_agent,
        off_median_latency_ms=off_med,
        on_median_latency_ms=on_med,
        off_p90_latency_ms=off_p90,
        on_p90_latency_ms=on_p90,
        off_success_rate=off_sr,
        on_success_rate=on_sr,
        off_mean_reward=off_rwd,
        on_mean_reward=on_rwd,
        off_results=off_results,
        on_results=on_results,
    )


def print_ab_report(summary: ABSummary, json_output: bool = False) -> None:
    if json_output:
        import json
        print(json.dumps({
            "suite": summary.suite_name,
            "n_tasks": summary.n_tasks,
            "baseline": summary.baseline_agent,
            "off": {
                "median_latency_ms": summary.off_median_latency_ms,
                "p90_latency_ms": summary.off_p90_latency_ms,
                "success_rate": summary.off_success_rate,
                "mean_reward": summary.off_mean_reward,
            },
            "on": {
                "median_latency_ms": summary.on_median_latency_ms,
                "p90_latency_ms": summary.on_p90_latency_ms,
                "success_rate": summary.on_success_rate,
                "mean_reward": summary.on_mean_reward,
            },
        }, indent=2))
        return

    def fmt_ms(v: float | None) -> str:
        return f"{v/1000:.2f}s" if v is not None else "n/a"

    def fmt_rate(v: float) -> str:
        return f"{v:.0%}"

    def delta(off: float | None, on: float | None, lower_is_better: bool = False) -> str:
        if off is None or on is None:
            return "n/a"
        d = on - off
        sign = "+" if d > 0 else ""
        if lower_is_better:
            indicator = " ✓" if d < 0 else (" ✗" if d > 0 else "")
        else:
            indicator = " ✓" if d > 0 else (" ✗" if d < 0 else "")
        if lower_is_better and off != 0:
            pct = f"{sign}{d/off:.0%}"
        elif not lower_is_better and off != 0:
            pct = f"{sign}{d/off:.0%}"
        else:
            pct = f"{sign}{d:.3f}"
        return f"{pct}{indicator}"

    print(f"\nMahoraga A/B Evaluation — {summary.suite_name}")
    print(f"Routing OFF baseline: {summary.baseline_agent}")
    print(f"Routing ON: adaptive (bandit)")
    print(f"Tasks: {summary.n_tasks}\n")
    print(f"{'Metric':<25} {'OFF':>10} {'ON':>10} {'Delta':>12}")
    print("-" * 60)
    print(f"{'Median latency':<25} {fmt_ms(summary.off_median_latency_ms):>10} {fmt_ms(summary.on_median_latency_ms):>10} {delta(summary.off_median_latency_ms, summary.on_median_latency_ms, lower_is_better=True):>12}")
    print(f"{'P90 latency':<25} {fmt_ms(summary.off_p90_latency_ms):>10} {fmt_ms(summary.on_p90_latency_ms):>10} {delta(summary.off_p90_latency_ms, summary.on_p90_latency_ms, lower_is_better=True):>12}")
    print(f"{'Success rate':<25} {fmt_rate(summary.off_success_rate):>10} {fmt_rate(summary.on_success_rate):>10} {delta(summary.off_success_rate, summary.on_success_rate):>12}")
    print(f"{'Mean reward':<25} {summary.off_mean_reward or 0:.3f}     {summary.on_mean_reward or 0:.3f}     {delta(summary.off_mean_reward, summary.on_mean_reward):>12}")
    print()
```

- [ ] **Step 2: Create the eval CLI command**

Create `backend/orchestrator/cli/commands/eval.py`:
```python
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="eval", help="Routing evaluation commands", no_args_is_help=True)


@app.command("ab")
def ab_eval(
    tasks: Path = typer.Option(
        Path("eval/tasks/default_ab.yaml"),
        "--tasks", "-t",
        help="Path to task suite YAML",
    ),
    baseline: str = typer.Option(
        "ollama:general",
        "--baseline",
        help="Baseline agent for routing-OFF run. Format: fixed:<agent_id>",
    ),
    repeat: int = typer.Option(1, "--repeat", "-n", help="Number of repeat runs"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Run A/B evaluation: compare routing OFF vs ON on a fixed task suite."""
    from ..eval.runner import run_ab_eval, print_ab_report

    if not tasks.exists():
        typer.echo(f"Task suite not found: {tasks}", err=True)
        raise typer.Exit(1)

    agent = baseline.removeprefix("fixed:")

    if not json_output:
        typer.echo(f"Running A/B eval on {tasks.name} ({repeat} repeat(s))...")

    summary = asyncio.run(run_ab_eval(tasks, baseline_agent=agent, repeat=repeat))
    print_ab_report(summary, json_output=json_output)
```

- [ ] **Step 3: Register eval command in main.py**

In `backend/orchestrator/cli/main.py`, add after the existing imports:
```python
from .commands.eval import app as eval_app
```

And add to the command registration block:
```python
app.add_typer(eval_app, name="eval")
```

- [ ] **Step 4: Test the CLI wiring (dry-run without server)**

```bash
python -m backend.orchestrator.cli.main eval --help
```
Expected: shows `ab` subcommand

```bash
python -m backend.orchestrator.cli.main eval ab --help
```
Expected: shows `--tasks`, `--baseline`, `--repeat`, `--json` options

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/eval/runner.py \
    backend/orchestrator/cli/commands/eval.py \
    backend/orchestrator/cli/main.py
git commit -m "feat: add A/B eval runner and orch eval ab CLI command"
```

---

## Phase 2: Rankings

### Task 5: RankingsStore

**Files:**
- Create: `backend/orchestrator/store/rankings_store.py`
- Test: `tests/orchestrator_v2/test_rankings_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator_v2/test_rankings_store.py
import pytest
import aiosqlite
from backend.orchestrator.store.rankings_store import RankingsStore


@pytest.fixture
async def store():
    async with aiosqlite.connect(":memory:") as conn:
        s = RankingsStore(conn)
        await s.migrate()
        yield s


@pytest.mark.asyncio
async def test_upsert_and_get_benchmark_run(store):
    await store.upsert_benchmark_run(
        agent="ollama:general",
        bucket="code",
        difficulty="simple",
        avg_latency_ms=1200.0,
        median_latency_ms=1100.0,
        p90_latency_ms=1800.0,
        win_rate=0.75,
        reward_mean=0.72,
        sample_count=20,
        source="harness",
    )
    rows = await store.get_benchmark_runs(agent="ollama:general")
    assert len(rows) >= 1
    assert rows[0]["win_rate"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_replace_scope_rankings(store):
    rankings = [
        {"agent": "ollama:general", "rank": 1, "win_rate": 0.8, "ci_low": 0.65, "ci_high": 0.90,
         "avg_latency_ms": 1200.0, "avg_reward": 0.75, "sample_count": 30},
        {"agent": "claude:sonnet", "rank": 2, "win_rate": 0.7, "ci_low": 0.55, "ci_high": 0.82,
         "avg_latency_ms": 2800.0, "avg_reward": 0.78, "sample_count": 25},
    ]
    await store.replace_scope_rankings("overall", "all", rankings)
    rows = await store.get_rankings(scope_type="overall", scope_value="all")
    assert len(rows) == 2
    assert rows[0]["agent"] == "ollama:general"
    assert rows[0]["rank"] == 1


@pytest.mark.asyncio
async def test_get_rankings_filters(store):
    rankings = [
        {"agent": "aider:default", "rank": 1, "win_rate": 0.85, "ci_low": 0.7, "ci_high": 0.94,
         "avg_latency_ms": 4000.0, "avg_reward": 0.80, "sample_count": 15},
    ]
    await store.replace_scope_rankings("bucket", "code", rankings)
    rows = await store.get_rankings(scope_type="bucket", scope_value="code")
    assert len(rows) == 1
    assert rows[0]["agent"] == "aider:default"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/orchestrator_v2/test_rankings_store.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement RankingsStore**

Create `backend/orchestrator/store/rankings_store.py`:
```python
from __future__ import annotations
import time
import aiosqlite


def _now() -> str:
    return str(time.time())


class RankingsStore:
    """Stores benchmark run summaries and materialized ranking snapshots."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                bucket TEXT,
                difficulty TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                avg_latency_ms REAL,
                median_latency_ms REAL,
                p90_latency_ms REAL,
                win_rate REAL,
                reward_mean REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'harness'
            );
            CREATE TABLE IF NOT EXISTS model_rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL,
                rank INTEGER NOT NULL,
                win_rate REAL,
                ci_low REAL,
                ci_high REAL,
                avg_latency_ms REAL,
                avg_reward REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)
        await self._conn.commit()

    async def upsert_benchmark_run(
        self,
        agent: str,
        bucket: str | None,
        difficulty: str | None,
        avg_latency_ms: float | None,
        median_latency_ms: float | None,
        p90_latency_ms: float | None,
        win_rate: float | None,
        reward_mean: float | None,
        sample_count: int,
        source: str = "harness",
    ) -> None:
        now = _now()
        await self._conn.execute(
            """INSERT INTO benchmark_runs
               (agent, bucket, difficulty, started_at, finished_at,
                avg_latency_ms, median_latency_ms, p90_latency_ms,
                win_rate, reward_mean, sample_count, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent, bucket, difficulty, now, now, avg_latency_ms, median_latency_ms,
             p90_latency_ms, win_rate, reward_mean, sample_count, source),
        )
        await self._conn.commit()

    async def get_benchmark_runs(
        self,
        agent: str | None = None,
        bucket: str | None = None,
        difficulty: str | None = None,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if bucket:
            conditions.append("bucket = ?")
            params.append(bucket)
        if difficulty:
            conditions.append("difficulty = ?")
            params.append(difficulty)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with self._conn.execute(
            f"SELECT * FROM benchmark_runs {where} ORDER BY id DESC",
            params,
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]

    async def replace_scope_rankings(
        self, scope_type: str, scope_value: str, rankings: list[dict]
    ) -> None:
        await self._conn.execute(
            "DELETE FROM model_rankings WHERE scope_type = ? AND scope_value = ?",
            (scope_type, scope_value),
        )
        now = _now()
        for row in rankings:
            await self._conn.execute(
                """INSERT INTO model_rankings
                   (agent, scope_type, scope_value, rank, win_rate, ci_low, ci_high,
                    avg_latency_ms, avg_reward, sample_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["agent"], scope_type, scope_value, row["rank"],
                 row.get("win_rate"), row.get("ci_low"), row.get("ci_high"),
                 row.get("avg_latency_ms"), row.get("avg_reward"),
                 row.get("sample_count", 0), now),
            )
        await self._conn.commit()

    async def get_rankings(
        self,
        scope_type: str = "overall",
        scope_value: str = "all",
        limit: int = 20,
    ) -> list[dict]:
        async with self._conn.execute(
            """SELECT * FROM model_rankings
               WHERE scope_type = ? AND scope_value = ?
               ORDER BY rank ASC LIMIT ?""",
            (scope_type, scope_value, limit),
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/orchestrator_v2/test_rankings_store.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/store/rankings_store.py \
    tests/orchestrator_v2/test_rankings_store.py
git commit -m "feat: add RankingsStore with benchmark_runs and model_rankings tables"
```

---

### Task 6: Rankings Aggregator

**Files:**
- Create: `backend/orchestrator/rankings/__init__.py`
- Create: `backend/orchestrator/rankings/aggregator.py`
- Test: `tests/orchestrator_v2/test_rankings_aggregator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator_v2/test_rankings_aggregator.py
import pytest
from backend.orchestrator.rankings.aggregator import wilson_interval, build_rankings_rows


def test_wilson_interval_majority_success():
    lo, hi = wilson_interval(80, 100)
    assert lo > 0.70
    assert hi < 1.0
    assert lo < hi


def test_wilson_interval_zero_samples():
    lo, hi = wilson_interval(0, 0)
    assert lo == 0.0
    assert hi == 0.0


def test_wilson_interval_all_fail():
    lo, hi = wilson_interval(0, 50)
    assert lo == pytest.approx(0.0, abs=0.01)
    assert hi < 0.10


def test_build_rankings_rows_sorts_by_reward():
    metrics = [
        {"agent": "a", "sample_count": 30, "success_count": 25,
         "mean_reward": 0.85, "median_latency_ms": 1200.0},
        {"agent": "b", "sample_count": 40, "success_count": 28,
         "mean_reward": 0.70, "median_latency_ms": 900.0},
        {"agent": "c", "sample_count": 10, "success_count": 9,
         "mean_reward": 0.90, "median_latency_ms": 2000.0},
    ]
    rows = build_rankings_rows(metrics)
    assert rows[0]["agent"] == "c"   # highest reward
    assert rows[1]["agent"] == "a"
    assert rows[2]["agent"] == "b"
    assert rows[0]["rank"] == 1
    assert "ci_low" in rows[0]
    assert "ci_high" in rows[0]
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/orchestrator_v2/test_rankings_aggregator.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create packages and aggregator**

Create `backend/orchestrator/rankings/__init__.py`:
```python
```

Create `backend/orchestrator/rankings/aggregator.py`:
```python
from __future__ import annotations
import logging
from math import sqrt

log = logging.getLogger(__name__)

LIVE_WEIGHT = 0.7
HARNESS_WEIGHT = 0.3

_BUCKETS = ["code", "test", "refactor", "debug", "research", "plan", "review", "security"]
_DIFFICULTIES = ["simple", "medium", "complex"]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binary success rate."""
    if total == 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    margin = z * sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_rankings_rows(metrics: list[dict]) -> list[dict]:
    """Sort agent metrics into a ranked list with CI. Input dicts must have:
    agent, sample_count, success_count, mean_reward, median_latency_ms."""
    rows = []
    for m in metrics:
        n = m["sample_count"]
        s = m.get("success_count", 0)
        ci_low, ci_high = wilson_interval(s, n)
        win_rate = s / n if n > 0 else 0.0
        rows.append({
            "agent": m["agent"],
            "win_rate": win_rate,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "avg_reward": m.get("mean_reward"),
            "avg_latency_ms": m.get("median_latency_ms"),
            "sample_count": n,
        })
    rows.sort(key=lambda r: (
        -(r["avg_reward"] or 0.0),
        -(r["win_rate"] or 0.0),
        (r["avg_latency_ms"] or float("inf")),
        -(r["sample_count"] or 0),
    ))
    for i, row in enumerate(rows):
        row["rank"] = i + 1
    return rows


async def rebuild_rankings(metrics_store, rankings_store) -> None:
    """Recompute and persist all ranking scopes from live + harness data."""

    # ── Pull live history from task_metrics ──────────────────────────────────
    live_history = await metrics_store.get_history(limit=5000)

    def _aggregate_live(rows: list[dict]) -> dict[str, dict]:
        """Aggregate task_metrics rows by agent."""
        agents: dict[str, dict] = {}
        for row in rows:
            a = row.get("agent_name") or row.get("agent") or ""
            if not a:
                continue
            if a not in agents:
                agents[a] = {"sample_count": 0, "success_count": 0,
                             "rewards": [], "latencies": [], "buckets": {}, "difficulties": {}}
            agents[a]["sample_count"] += 1
            if row.get("success"):
                agents[a]["success_count"] += 1
            if row.get("reward_score") is not None:
                agents[a]["rewards"].append(row["reward_score"])
            if row.get("wall_time_ms") is not None:
                agents[a]["latencies"].append(row["wall_time_ms"])
            bucket = row.get("capability_bucket", "general")
            agents[a]["buckets"].setdefault(bucket, {"sample_count": 0, "success_count": 0,
                                                      "rewards": [], "latencies": []})
            agents[a]["buckets"][bucket]["sample_count"] += 1
            if row.get("success"):
                agents[a]["buckets"][bucket]["success_count"] += 1
            if row.get("reward_score") is not None:
                agents[a]["buckets"][bucket]["rewards"].append(row["reward_score"])
            if row.get("wall_time_ms") is not None:
                agents[a]["buckets"][bucket]["latencies"].append(row["wall_time_ms"])
        return agents

    live_agents = _aggregate_live(live_history)

    # ── Pull harness data from benchmark_runs ────────────────────────────────
    harness_rows = await rankings_store.get_benchmark_runs()

    def _aggregate_harness(rows: list[dict]) -> dict[str, dict]:
        agents: dict[str, dict] = {}
        for row in rows:
            a = row["agent"]
            agents.setdefault(a, {"sample_count": 0, "success_count": 0, "rewards": [], "latencies": []})
            n = row.get("sample_count", 0)
            wr = row.get("win_rate") or 0.0
            agents[a]["sample_count"] += n
            agents[a]["success_count"] += int(wr * n)
            if row.get("reward_mean") is not None:
                agents[a]["rewards"].extend([row["reward_mean"]] * n)
            if row.get("median_latency_ms") is not None:
                agents[a]["latencies"].extend([row["median_latency_ms"]] * n)
        return agents

    harness_agents = _aggregate_harness(harness_rows)

    # ── Merge live + harness ─────────────────────────────────────────────────
    all_agents = set(live_agents) | set(harness_agents)

    def _merge_agent(agent: str) -> dict:
        live = live_agents.get(agent, {})
        harness = harness_agents.get(agent, {})
        ln = live.get("sample_count", 0)
        hn = harness.get("sample_count", 0)
        total = ln + hn

        def _wmean(l_vals, h_vals):
            if not l_vals and not h_vals:
                return None
            lm = sum(l_vals) / len(l_vals) if l_vals else None
            hm = sum(h_vals) / len(h_vals) if h_vals else None
            if lm is None:
                return hm
            if hm is None:
                return lm
            return LIVE_WEIGHT * lm + HARNESS_WEIGHT * hm

        return {
            "agent": agent,
            "sample_count": total,
            "success_count": live.get("success_count", 0) + harness.get("success_count", 0),
            "mean_reward": _wmean(live.get("rewards", []), harness.get("rewards", [])),
            "median_latency_ms": _wmean(live.get("latencies", []), harness.get("latencies", [])),
        }

    overall_metrics = [_merge_agent(a) for a in all_agents if _merge_agent(a)["sample_count"] > 0]
    if overall_metrics:
        ranked = build_rankings_rows(overall_metrics)
        await rankings_store.replace_scope_rankings("overall", "all", ranked)

    # ── Bucket scopes ────────────────────────────────────────────────────────
    for bucket in _BUCKETS:
        bucket_metrics = []
        for agent in all_agents:
            live_bucket = live_agents.get(agent, {}).get("buckets", {}).get(bucket, {})
            lm = {
                "agent": agent,
                "sample_count": live_bucket.get("sample_count", 0),
                "success_count": live_bucket.get("success_count", 0),
                "mean_reward": sum(live_bucket.get("rewards", [])) / len(live_bucket["rewards"]) if live_bucket.get("rewards") else None,
                "median_latency_ms": sorted(live_bucket.get("latencies", []))[len(live_bucket.get("latencies", [])) // 2] if live_bucket.get("latencies") else None,
            }
            if lm["sample_count"] > 0:
                bucket_metrics.append(lm)
        if bucket_metrics:
            ranked = build_rankings_rows(bucket_metrics)
            await rankings_store.replace_scope_rankings("bucket", bucket, ranked)

    log.info("rankings rebuilt: %d agents, %d buckets", len(all_agents), len(_BUCKETS))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/orchestrator_v2/test_rankings_aggregator.py -v
```
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/rankings/__init__.py \
    backend/orchestrator/rankings/aggregator.py \
    tests/orchestrator_v2/test_rankings_aggregator.py
git commit -m "feat: add rankings aggregator with Wilson CI and rebuild_rankings()"
```

---

### Task 7: Rankings Endpoint + CLI

**Files:**
- Modify: `backend/orchestrator/service/app.py`
- Create: `backend/orchestrator/cli/commands/rankings.py`
- Modify: `backend/orchestrator/cli/main.py`

- [ ] **Step 1: Add RankingsStore and MetricsStore to app.py lifespan**

In `backend/orchestrator/service/app.py`, add to imports:
```python
from ..store.rankings_store import RankingsStore
from ..store.metrics import MetricsStore
```

Add globals (after `_eval_store`):
```python
_rankings_store: RankingsStore | None = None
_metrics_store: MetricsStore | None = None


def get_rankings_store() -> RankingsStore:
    assert _rankings_store is not None
    return _rankings_store


def get_metrics_store() -> MetricsStore:
    assert _metrics_store is not None
    return _metrics_store


RankingsStoreDep = Annotated[RankingsStore, Depends(get_rankings_store)]
MetricsStoreDep = Annotated[MetricsStore, Depends(get_metrics_store)]
```

In `lifespan()`, after `_eval_store` setup:
```python
    global _rankings_store, _metrics_store
    _rankings_store = RankingsStore(_store._conn)
    await _rankings_store.migrate()
    _metrics_store = MetricsStore(_store._conn)
    await _metrics_store.migrate()
```

- [ ] **Step 2: Add /api/rankings endpoint to app.py**

Add near the other `/api/` routes:
```python
@app.get("/api/rankings")
async def get_rankings(
    rankings_store: RankingsStoreDep,
    metrics_store: MetricsStoreDep,
    scope_type: str = "overall",
    scope_value: str = "all",
    bucket: str | None = None,
    difficulty: str | None = None,
    agent: str | None = None,
    limit: int = 20,
    refresh: bool = False,
) -> dict:
    from ..rankings.aggregator import rebuild_rankings
    if refresh:
        await rebuild_rankings(metrics_store, rankings_store)

    if bucket:
        scope_type, scope_value = "bucket", bucket
    elif difficulty:
        scope_type, scope_value = "difficulty", difficulty

    rows = await rankings_store.get_rankings(
        scope_type=scope_type, scope_value=scope_value, limit=limit
    )
    if agent:
        rows = [r for r in rows if r["agent"] == agent]
    return {"scope_type": scope_type, "scope_value": scope_value, "rankings": rows}
```

- [ ] **Step 3: Create rankings CLI command**

Create `backend/orchestrator/cli/commands/rankings.py`:
```python
from __future__ import annotations
from typing import Optional
import httpx
import typer

BASE_URL = "http://localhost:8001"

app = typer.Typer(name="rankings", help="Agent rankings", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def rankings(
    ctx: typer.Context,
    bucket: Optional[str] = typer.Option(None, "--bucket", "-b", help="Filter by task bucket"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty", "-d", help="Filter by difficulty"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Show only one agent"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Rebuild rankings first"),
) -> None:
    """Show local agent rankings (from live routing history + benchmark harness)."""
    if ctx.invoked_subcommand is not None:
        return

    params: dict = {"limit": limit, "refresh": str(refresh).lower()}
    if bucket:
        params["bucket"] = bucket
    if difficulty:
        params["difficulty"] = difficulty
    if agent:
        params["agent"] = agent

    try:
        r = httpx.get(f"{BASE_URL}/api/rankings", params=params, timeout=30.0)
        r.raise_for_status()
    except httpx.ConnectError:
        typer.echo("Cannot connect to Mahoraga server. Is it running?", err=True)
        raise typer.Exit(1)

    data = r.json()
    rows = data["rankings"]

    if json_output:
        import json
        print(json.dumps(data, indent=2))
        return

    if not rows:
        scope_label = f"{data['scope_type']}={data['scope_value']}"
        typer.echo(f"No rankings data for scope: {scope_label}. Run 'orch benchmark refresh' to populate.")
        return

    scope_label = data["scope_value"] if data["scope_value"] != "all" else "overall"
    typer.echo(f"\nLocal Rankings ({scope_label})\n")

    header = f"{'Rank':<6} {'Agent':<22} {'Win Rate':<10} {'95% CI':<16} {'Avg Latency':<14} {'Avg Reward':<12} {'N':<6}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for row in rows:
        rank = row["rank"]
        a = row["agent"][:21]
        wr = f"{row['win_rate']:.2f}" if row.get("win_rate") is not None else "n/a"
        ci = f"{row.get('ci_low', 0):.2f}–{row.get('ci_high', 0):.2f}" if row.get("ci_low") is not None else "n/a"
        lat_ms = row.get("avg_latency_ms")
        lat = f"{lat_ms/1000:.1f}s" if lat_ms else "n/a"
        rwd = f"{row['avg_reward']:.2f}" if row.get("avg_reward") is not None else "n/a"
        n = row.get("sample_count", 0)
        typer.echo(f"{rank:<6} {a:<22} {wr:<10} {ci:<16} {lat:<14} {rwd:<12} {n:<6}")

    if verbose:
        typer.echo("\n(verbose: source breakdown coming in --refresh mode)")
    typer.echo()
```

- [ ] **Step 4: Register rankings in main.py**

In `backend/orchestrator/cli/main.py`, add:
```python
from .commands.rankings import app as rankings_app
```

And:
```python
app.add_typer(rankings_app, name="rankings")
```

- [ ] **Step 5: Verify CLI wiring**

```bash
python -m backend.orchestrator.cli.main rankings --help
```
Expected: shows `--bucket`, `--difficulty`, `--agent`, `--limit`, `--json`, `--verbose`, `--refresh`

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/store/rankings_store.py \
    backend/orchestrator/rankings/__init__.py \
    backend/orchestrator/rankings/aggregator.py \
    backend/orchestrator/service/app.py \
    backend/orchestrator/cli/commands/rankings.py \
    backend/orchestrator/cli/main.py
git commit -m "feat: add /api/rankings endpoint and orch rankings CLI"
```

---

## Phase 3: Agent Onboarding + Benchmark Refresh

### Task 8: Agent Benchmark + orch agent add + orch benchmark refresh

**Files:**
- Create: `backend/orchestrator/routing/benchmark/agent_benchmark.py`
- Create: `backend/orchestrator/cli/commands/agent_cmd.py`
- Modify: `backend/orchestrator/routing/benchmark/benchmark.py`
- Modify: `backend/orchestrator/cli/main.py`

- [ ] **Step 1: Create the per-agent benchmark runner**

Create `backend/orchestrator/routing/benchmark/agent_benchmark.py`:
```python
"""Short benchmark sweep for a single agent, used during orch agent add."""
from __future__ import annotations
import asyncio
import statistics
from dataclasses import dataclass

import httpx

BASE_URL = "http://localhost:8001"

_SMOKE_TASKS = [
    {"text": "What is 2 + 2? Reply with just the number.", "bucket": "research", "difficulty": "simple"},
    {"text": "Write a Python function called hello() that returns 'hello world'.", "bucket": "code", "difficulty": "simple"},
    {"text": "What does the ls command do?", "bucket": "research", "difficulty": "simple"},
]

_BENCHMARK_TASKS = [
    {"text": "Write a Python function that returns the sum of a list.", "bucket": "code", "difficulty": "simple"},
    {"text": "Find the bug: def add(a, b): return a - b", "bucket": "debug", "difficulty": "simple"},
    {"text": "What is the difference between a list and a tuple?", "bucket": "research", "difficulty": "simple"},
    {"text": "Implement a Python class for a stack with push, pop, and is_empty.", "bucket": "code", "difficulty": "medium"},
    {"text": "Explain what causes a KeyError in Python and how to handle it.", "bucket": "debug", "difficulty": "medium"},
    {"text": "Compare SQLite and PostgreSQL for a local single-user app.", "bucket": "research", "difficulty": "medium"},
]


@dataclass
class BenchmarkResult:
    agent: str
    smoke_passed: bool
    smoke_details: list[dict]
    benchmark_n: int
    benchmark_success_rate: float
    benchmark_mean_latency_ms: float | None
    benchmark_mean_reward: float | None


async def run_smoke(agent_id: str, client: httpx.AsyncClient) -> tuple[bool, list[dict]]:
    results = []
    for task in _SMOKE_TASKS:
        try:
            r = await client.post(f"{BASE_URL}/api/eval/task", json={
                "text": task["text"],
                "bucket": task["bucket"],
                "difficulty": task["difficulty"],
                "routing_mode": f"fixed:{agent_id}",
            }, timeout=60.0)
            r.raise_for_status()
            results.append({"ok": r.json()["success"], **task})
        except Exception as e:
            results.append({"ok": False, "error": str(e), **task})
    passed = all(r["ok"] for r in results)
    return passed, results


async def run_short_benchmark(agent_id: str, client: httpx.AsyncClient) -> list[dict]:
    results = []
    run_id_resp = await client.post(f"{BASE_URL}/api/eval/start", json={
        "run_type": "benchmark",
        "routing_enabled": False,
        "baseline_policy": f"fixed:{agent_id}",
        "suite_name": "agent_onboarding",
    })
    run_id = run_id_resp.json()["run_id"]

    for task in _BENCHMARK_TASKS:
        try:
            r = await client.post(f"{BASE_URL}/api/eval/task", json={
                "run_id": run_id,
                "text": task["text"],
                "bucket": task["bucket"],
                "difficulty": task["difficulty"],
                "routing_mode": f"fixed:{agent_id}",
            }, timeout=120.0)
            r.raise_for_status()
            results.append(r.json())
        except Exception as e:
            results.append({"success": False, "latency_ms": 0.0, "reward": 0.0})

    await client.post(f"{BASE_URL}/api/eval/finish", json={"run_id": run_id})
    return results


async def run_agent_benchmark(agent_id: str) -> BenchmarkResult:
    async with httpx.AsyncClient(timeout=300.0) as client:
        smoke_ok, smoke_details = await run_smoke(agent_id, client)
        if not smoke_ok:
            return BenchmarkResult(
                agent=agent_id, smoke_passed=False, smoke_details=smoke_details,
                benchmark_n=0, benchmark_success_rate=0.0,
                benchmark_mean_latency_ms=None, benchmark_mean_reward=None,
            )
        bench_results = await run_short_benchmark(agent_id, client)

    latencies = [r["latency_ms"] for r in bench_results if r.get("latency_ms")]
    successes = [r["success"] for r in bench_results]
    rewards = [r["reward"] for r in bench_results if r.get("reward") is not None]

    return BenchmarkResult(
        agent=agent_id,
        smoke_passed=True,
        smoke_details=smoke_details,
        benchmark_n=len(bench_results),
        benchmark_success_rate=sum(successes) / len(successes) if successes else 0.0,
        benchmark_mean_latency_ms=statistics.mean(latencies) if latencies else None,
        benchmark_mean_reward=statistics.mean(rewards) if rewards else None,
    )
```

- [ ] **Step 2: Create orch agent add command**

Create `backend/orchestrator/cli/commands/agent_cmd.py`:
```python
"""orch agent — agent onboarding commands."""
from __future__ import annotations
import asyncio

import httpx
import typer

BASE_URL = "http://localhost:8001"

app = typer.Typer(name="agent", help="Agent management", no_args_is_help=True)


@app.command("add")
def add_agent(
    model: str = typer.Argument(..., help="Agent ID to add, e.g. ollama:qwen3 or gemini:flash"),
    skip_benchmark: bool = typer.Option(False, "--skip-benchmark", help="Skip benchmark, just smoke test"),
) -> None:
    """Register a new agent, run smoke test, benchmark it, and update rankings."""

    async def _run():
        from ..routing.benchmark.agent_benchmark import run_agent_benchmark

        # 1. Check the agent is registered and healthy
        typer.echo(f"Checking agent: {model}")
        try:
            r = httpx.get(f"{BASE_URL}/workers/health", timeout=10.0)
            r.raise_for_status()
            workers = r.json()
            registered = any(
                w.get("worker_id") == model or w.get("id") == model
                for w in (workers if isinstance(workers, list) else workers.get("workers", []))
            )
        except httpx.ConnectError:
            typer.echo("Cannot connect to Mahoraga server. Start it first: python -m backend.main", err=True)
            raise typer.Exit(1)

        if not registered:
            typer.echo(f"Agent '{model}' is not registered in the server.", err=True)
            typer.echo("To add a new agent type, register it in backend/orchestrator/service/app.py lifespan()", err=True)
            typer.echo("Then restart the server and run this command again.", err=True)
            raise typer.Exit(1)

        # 2. Run smoke + benchmark
        typer.echo(f"Running smoke test...")
        result = await run_agent_benchmark(model)

        if not result.smoke_passed:
            typer.echo(f"\nSmoke test FAILED for {model}")
            for s in result.smoke_details:
                status = "✓" if s.get("ok") else "✗"
                typer.echo(f"  {status} {s['text'][:60]}")
            raise typer.Exit(1)

        typer.echo(f"Smoke test: PASSED")

        # run_agent_benchmark already ran smoke + benchmark above.
        # Now trigger rankings rebuild so the new agent appears.
        if not skip_benchmark:
            typer.echo("Rebuilding rankings...")
            httpx.get(f"{BASE_URL}/api/rankings", params={"refresh": "true"}, timeout=60.0)

        # 3. Show summary
        typer.echo(f"\nAgent: {model}")
        typer.echo(f"Smoke test: PASSED")
        if not skip_benchmark:
            typer.echo(f"Benchmark tasks: {result.benchmark_n}")
            typer.echo(f"Success rate: {result.benchmark_success_rate:.0%}")
            if result.benchmark_mean_latency_ms:
                typer.echo(f"Mean latency: {result.benchmark_mean_latency_ms/1000:.1f}s")
            if result.benchmark_mean_reward:
                typer.echo(f"Mean reward: {result.benchmark_mean_reward:.2f}")
        typer.echo(f"\nRankings updated. Run 'orch rankings' to see current standings.")

    asyncio.run(_run())
```

- [ ] **Step 3: Add orch benchmark refresh to benchmark.py**

In `backend/orchestrator/routing/benchmark/benchmark.py`, add at the end of the file:
```python
@app.command("refresh")
def refresh(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Re-run the local harness and refresh stored rankings."""
    import httpx
    typer.echo("Refreshing rankings from live history + harness data...")
    try:
        r = httpx.get(f"{BASE_URL}/api/rankings", params={"refresh": "true"}, timeout=120.0)
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError:
        typer.echo("Cannot connect to server. Is it running?", err=True)
        raise typer.Exit(1)

    if json_output:
        import json
        print(json.dumps(data, indent=2))
    else:
        rows = data.get("rankings", [])
        typer.echo(f"Rankings refreshed. {len(rows)} agents ranked overall.")
        if rows:
            typer.echo(f"Top agent: {rows[0]['agent']}")
```

Also add the BASE_URL import at the top of benchmark.py if not already there:
```python
BASE_URL = "http://localhost:8001"
```

- [ ] **Step 4: Register agent command in main.py**

In `backend/orchestrator/cli/main.py`, add:
```python
from .commands.agent_cmd import app as agent_app
```

And:
```python
app.add_typer(agent_app, name="agent")
```

- [ ] **Step 5: Verify all CLI commands**

```bash
python -m backend.orchestrator.cli.main --help
```
Expected: shows `eval`, `rankings`, `agent`, `benchmark` in the command list

```bash
python -m backend.orchestrator.cli.main agent add --help
python -m backend.orchestrator.cli.main benchmark refresh --help
```
Expected: both show help without errors

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/routing/benchmark/agent_benchmark.py \
    backend/orchestrator/cli/commands/agent_cmd.py \
    backend/orchestrator/routing/benchmark/benchmark.py \
    backend/orchestrator/cli/main.py
git commit -m "feat: add orch agent add, orch benchmark refresh, and per-agent benchmark runner"
```

---

## Phase 4: UI Rankings Tab

### Task 9: Rankings Sidebar Tab

**Files:**
- Modify: `static/index.html`
- Modify: `static/sidebar.js`

- [ ] **Step 1: Add Rankings section to index.html**

In `static/index.html`, find the existing `<div class="sidebar-section" id="logs-section">` block. Add the Rankings section immediately BEFORE it:

```html
        <!-- Rankings section -->
        <div class="sidebar-section" id="rankings-section">
          <div class="section-header">
            <span class="section-title">Rankings</span>
            <button class="section-chevron" data-section="rankings">▾</button>
          </div>
          <div class="section-body" id="rankings-section-body">
            <div class="rankings-filters">
              <select id="rankings-bucket-filter" style="font-size:11px;margin-right:4px;">
                <option value="">All buckets</option>
                <option value="code">code</option>
                <option value="debug">debug</option>
                <option value="refactor">refactor</option>
                <option value="research">research</option>
                <option value="plan">plan</option>
                <option value="test">test</option>
                <option value="review">review</option>
                <option value="security">security</option>
              </select>
              <select id="rankings-difficulty-filter" style="font-size:11px;">
                <option value="">All difficulties</option>
                <option value="simple">simple</option>
                <option value="medium">medium</option>
                <option value="complex">complex</option>
              </select>
              <button id="rankings-refresh-btn" style="font-size:10px;margin-left:4px;">↺</button>
            </div>
            <div id="rankings-table-container" style="margin-top:6px;">
              <span class="sidebar-empty">Loading rankings…</span>
            </div>
            <div id="rankings-updated" style="font-size:9px;color:#888;margin-top:4px;"></div>
          </div>
        </div>
```

- [ ] **Step 2: Add rankings fetch and render to sidebar.js**

At the end of `static/sidebar.js`, add:

```javascript
// ── Rankings ──────────────────────────────────────────────────────────────

async function fetchRankings(bucket, difficulty, refresh) {
  const params = new URLSearchParams({ limit: 10 });
  if (bucket) params.set('bucket', bucket);
  if (difficulty) params.set('difficulty', difficulty);
  if (refresh) params.set('refresh', 'true');
  const res = await fetch('/api/rankings?' + params.toString());
  if (!res.ok) throw new Error('rankings fetch failed');
  return res.json();
}

function renderRankingsTable(rankings) {
  if (!rankings || rankings.length === 0) {
    return '<span class="sidebar-empty">No ranking data. Run <code>orch benchmark refresh</code> to populate.</span>';
  }
  const rows = rankings.map(r => {
    const wr = r.win_rate != null ? (r.win_rate * 100).toFixed(0) + '%' : 'n/a';
    const ci = (r.ci_low != null && r.ci_high != null)
      ? `${(r.ci_low * 100).toFixed(0)}–${(r.ci_high * 100).toFixed(0)}%`
      : 'n/a';
    const lat = r.avg_latency_ms != null ? (r.avg_latency_ms / 1000).toFixed(1) + 's' : 'n/a';
    const rwd = r.avg_reward != null ? r.avg_reward.toFixed(2) : 'n/a';
    return `<tr>
      <td style="padding:2px 4px;color:#888;">${r.rank}</td>
      <td style="padding:2px 4px;font-weight:500;">${r.agent}</td>
      <td style="padding:2px 4px;">${wr}</td>
      <td style="padding:2px 4px;color:#888;">${ci}</td>
      <td style="padding:2px 4px;">${lat}</td>
      <td style="padding:2px 4px;">${rwd}</td>
      <td style="padding:2px 4px;color:#888;">${r.sample_count}</td>
    </tr>`;
  }).join('');
  return `<table style="width:100%;border-collapse:collapse;font-size:10px;">
    <thead><tr style="color:#888;text-align:left;">
      <th style="padding:2px 4px;">#</th>
      <th style="padding:2px 4px;">Agent</th>
      <th style="padding:2px 4px;">Win%</th>
      <th style="padding:2px 4px;">95% CI</th>
      <th style="padding:2px 4px;">Latency</th>
      <th style="padding:2px 4px;">Reward</th>
      <th style="padding:2px 4px;">N</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function refreshRankingsUI(forceRefresh) {
  const container = document.getElementById('rankings-table-container');
  const updatedEl = document.getElementById('rankings-updated');
  const bucket = document.getElementById('rankings-bucket-filter')?.value || '';
  const difficulty = document.getElementById('rankings-difficulty-filter')?.value || '';
  if (!container) return;
  try {
    const data = await fetchRankings(bucket, difficulty, forceRefresh);
    container.innerHTML = renderRankingsTable(data.rankings);
    const scope = [bucket, difficulty].filter(Boolean).join('/') || 'overall';
    updatedEl.textContent = `${scope} · updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    container.innerHTML = '<span class="sidebar-empty">Rankings unavailable</span>';
  }
}

// Wire up filter changes and refresh button
document.addEventListener('DOMContentLoaded', () => {
  const bucketFilter = document.getElementById('rankings-bucket-filter');
  const diffFilter = document.getElementById('rankings-difficulty-filter');
  const refreshBtn = document.getElementById('rankings-refresh-btn');

  if (bucketFilter) bucketFilter.addEventListener('change', () => refreshRankingsUI(false));
  if (diffFilter) diffFilter.addEventListener('change', () => refreshRankingsUI(false));
  if (refreshBtn) refreshBtn.addEventListener('click', () => refreshRankingsUI(true));

  // Initial load
  refreshRankingsUI(false);
});
```

- [ ] **Step 3: Start the server and verify the Rankings tab appears**

```bash
cd ~/Projects/Mahoraga && python -m backend.main &
sleep 3
open http://localhost:8001
```

Verify:
- Rankings section appears in the sidebar (above the Recent section)
- Bucket and difficulty dropdowns are present
- Refresh button (↺) is clickable
- "No ranking data" message shows if DB is empty (expected on fresh install)
- No JS console errors

Kill server: `kill %1`

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/sidebar.js
git commit -m "feat: add Rankings sidebar tab with bucket/difficulty filters"
```

---

## Phase 5: Final Wiring + End-to-End Test

### Task 10: Run full tests and commit

- [ ] **Step 1: Run all tests**

```bash
pytest tests/orchestrator_v2/test_task_suite.py \
       tests/orchestrator_v2/test_eval_store.py \
       tests/orchestrator_v2/test_rankings_store.py \
       tests/orchestrator_v2/test_rankings_aggregator.py \
       -v
```
Expected: all tests PASS

- [ ] **Step 2: Run the full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: no new failures beyond any pre-existing ones

- [ ] **Step 3: End-to-end smoke test (requires running server + Ollama)**

```bash
python -m backend.main &
sleep 5

# Test rankings endpoint
curl -s http://localhost:8001/api/rankings | python3 -m json.tool

# Test eval start/finish cycle
RUN_ID=$(curl -s -X POST http://localhost:8001/api/eval/start \
  -H "Content-Type: application/json" \
  -d '{"run_type":"test","routing_enabled":false,"suite_name":"smoke"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $RUN_ID"

curl -s -X POST http://localhost:8001/api/eval/finish \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": $RUN_ID}" | python3 -m json.tool

# Test CLI help
python -m backend.orchestrator.cli.main eval ab --help
python -m backend.orchestrator.cli.main rankings --help
python -m backend.orchestrator.cli.main agent add --help
python -m backend.orchestrator.cli.main benchmark refresh --help

kill %1
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: rankings, eval, and onboarding — all phases complete"
```

---

## Known Limitations (MVP)

- **`orch agent add` benchmark data in rankings:** The short benchmark in `run_agent_benchmark` writes to `routing_run_tasks` (via `/api/eval/task`), not to `task_metrics`. The aggregator reads from `task_metrics`. Result: newly added agents won't appear in rankings until they accumulate live routing history in `task_metrics`. Workaround: run `orch eval ab` after adding an agent, then `orch benchmark refresh`.
- **Difficulty scopes in rankings:** The aggregator writes "overall" and "bucket" scopes. "difficulty" and "bucket+difficulty" scopes are not yet populated. The CLI accepts `--difficulty` but may return empty results until these scopes are added to `rebuild_rankings()`.

---

## Acceptance Checklist

- [ ] `orch eval ab --tasks eval/tasks/default_ab.yaml` runs OFF vs ON and prints comparison table
- [ ] `orch rankings` shows ranked table with win rate, CI, latency, reward, N
- [ ] `orch rankings --bucket code --json` outputs filterable JSON
- [ ] `orch agent add <model>` validates health, runs smoke, benchmarks, prints summary
- [ ] `orch benchmark refresh` rebuilds rankings from live + harness data
- [ ] Rankings sidebar tab visible in web UI with working filters
- [ ] All 4 new test files pass
- [ ] No regressions in existing test suite
