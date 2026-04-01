# Planner Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Smoke test the existing orchestrator, add the missing mission/plan CRUD HTTP endpoints, then build a Planner module that takes a mission and auto-generates a task graph via Ollama.

**Architecture:** The planner is a pure library module (`orchestrator/planning/`) that calls Ollama's `/api/chat` with `format: "json"`, parses and validates the response, and saves `Task` objects to the store. It is wired into a new `POST /missions/{id}/generate` endpoint and a new `orch plan generate` CLI command. All existing executor/worker/routing code is untouched.

**Tech Stack:** Python 3.11+, aiosqlite, httpx, FastAPI, Typer, Ollama (`qwen3:8b` at `http://localhost:11434`), pytest-asyncio

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/orchestrator/planning/__init__.py` | package marker |
| Create | `backend/orchestrator/planning/prompt.py` | qwen3:8b system prompt template |
| Create | `backend/orchestrator/planning/validator.py` | required-field checks + DAG cycle detection |
| Create | `backend/orchestrator/planning/planner.py` | Ollama call → parse → validate → return Task list |
| Modify | `backend/orchestrator/service/app.py` | add mission/plan CRUD endpoints + `/missions/{id}/generate` |
| Modify | `backend/orchestrator/cli/commands/plan.py` | add `orch plan generate` command |
| Create | `tests/orchestrator_v2/test_planner_validator.py` | unit tests for validator.py |
| Create | `tests/orchestrator_v2/test_planner.py` | integration tests for planner.py (mocked Ollama) |
| Modify | `tests/orchestrator_v2/test_app.py` | tests for new endpoints |

---

## Task 1: Smoke Test the Existing System

No code changes. Run the system, observe what breaks, fix what's broken before adding anything new.

**Files:** None (diagnostic only)

- [ ] **Step 1: Start Ollama**

```bash
ollama serve &
ollama pull qwen3:8b
```

Expected: Ollama running at `http://localhost:11434`

- [ ] **Step 2: Start the orchestrator service**

```bash
cd ~/Projects/ollama-runtime
source venv/bin/activate
uvicorn backend.orchestrator.service.app:app --port 8001 --reload
```

Expected: FastAPI starts. If it crashes, read the traceback.

- [ ] **Step 3: Run the full test suite**

```bash
cd ~/Projects/ollama-runtime
source venv/bin/activate
pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -30
```

Expected: 95 tests pass. If any fail, fix them before proceeding.

- [ ] **Step 4: Try the CLI**

In a new terminal:
```bash
cd ~/Projects/ollama-runtime && source venv/bin/activate
python -m backend.orchestrator.cli.main mission new --title "Test" --goal "Test goal"
```

Expected: will fail with HTTP 404 or 422 because `POST /missions` does not exist in `app.py` yet. That's fine — this confirms Task 2 is needed.

- [ ] **Step 5: Commit** (only if you fixed test failures in Step 3)

```bash
git add -A
git commit -m "fix: resolve smoke test failures before planner work"
```

---

## Task 2: Add Mission + Plan CRUD Endpoints

The CLI assumes `POST /missions` and `POST /plans` exist. They don't. Add them now.

**Files:**
- Modify: `backend/orchestrator/service/app.py`
- Modify: `tests/orchestrator_v2/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/orchestrator_v2/test_app.py`:

```python
@pytest.mark.asyncio
async def test_create_mission(store, registry, client):
    resp = await client.post("/missions", json={
        "title": "Build REST API",
        "goal": "Create a user authentication API",
        "background": "",
        "success_condition": "All endpoints return correct responses",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["title"] == "Build REST API"


@pytest.mark.asyncio
async def test_list_missions_empty(store, registry, client):
    resp = await client.get("/missions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_mission_not_found(store, registry, client):
    resp = await client.get("/missions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_plan(store, registry, client):
    # Create a mission first
    m_resp = await client.post("/missions", json={
        "title": "Test Mission",
        "goal": "Test goal",
    })
    assert m_resp.status_code == 201
    mission_id = m_resp.json()["id"]

    resp = await client.post("/plans", json={
        "mission_id": mission_id,
        "mode": "direct",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "plan_id" in data
    assert "run_id" in data
    assert data["run_status"] == "paused"
```

- [ ] **Step 2: Run tests to see them fail**

```bash
pytest tests/orchestrator_v2/test_app.py -k "test_create_mission or test_list_missions or test_get_mission or test_create_plan" -v
```

Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Add endpoints to app.py**

In `backend/orchestrator/service/app.py`, add after the existing imports:

```python
from ..domain.models import Mission, Plan, Run, RunMode, RunStatus, TaskStatus
```

(Check the existing import line — `Mission`, `Plan`, `Run`, `RunMode` may not all be imported yet. Add only what's missing.)

Then add these request models near the top of the file with the other Pydantic models:

```python
class CreateMissionRequest(BaseModel):
    title: str
    goal: str
    background: str = ""
    success_condition: str = ""


class CreatePlanRequest(BaseModel):
    mission_id: str
    mode: str = "direct"
```

Then add these routes at the end of the file:

```python
# ── missions ──────────────────────────────────────────────────────────────────

@app.post("/missions", status_code=201)
async def create_mission(req: CreateMissionRequest, store: StoreDep):
    mission = Mission.new(
        title=req.title,
        goal=req.goal,
        background=req.background,
        success_condition=req.success_condition,
    )
    await store.missions.save(mission)
    return {"id": mission.id, "title": mission.title, "status": mission.status}


@app.get("/missions/{mission_id}")
async def get_mission(mission_id: str, store: StoreDep):
    mission = await store.missions.get(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"id": mission.id, "title": mission.title, "goal": mission.goal,
            "background": mission.background, "success_condition": mission.success_condition,
            "status": mission.status}


@app.get("/missions")
async def list_missions(store: StoreDep):
    missions = await store.missions.list()
    return [{"id": m.id, "title": m.title, "status": m.status} for m in missions]


# ── plans ─────────────────────────────────────────────────────────────────────

@app.post("/plans", status_code=201)
async def create_plan(req: CreatePlanRequest, store: StoreDep):
    mission = await store.missions.get(req.mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    plan = Plan.new(mission_id=req.mission_id)
    run = Run.new(mission_id=req.mission_id, plan_id=plan.id, mode=RunMode(req.mode))
    await store.missions.save_plan(plan)
    await store.missions.save_run(run)
    return {"plan_id": plan.id, "run_id": run.id, "run_status": run.status}


@app.get("/plans/{plan_id}")
async def get_plan(plan_id: str, store: StoreDep):
    plan = await store.missions.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"id": plan.id, "mission_id": plan.mission_id, "status": plan.status,
            "version": plan.version}


@app.get("/plans")
async def list_plans(store: StoreDep, mission_id: str | None = None):
    if mission_id:
        plans = await store.missions.list_plans(mission_id)
    else:
        plans = []
        missions = await store.missions.list()
        for m in missions:
            plans.extend(await store.missions.list_plans(m.id))
    return [{"id": p.id, "mission_id": p.mission_id, "status": p.status,
             "version": p.version} for p in plans]
```

- [ ] **Step 4: Run tests to see them pass**

```bash
pytest tests/orchestrator_v2/test_app.py -k "test_create_mission or test_list_missions or test_get_mission or test_create_plan" -v
```

Expected: 4 PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -10
```

Expected: all existing tests still pass

- [ ] **Step 6: Verify CLI works end-to-end**

```bash
python -m backend.orchestrator.cli.main mission new --title "Test" --goal "Build something"
# Copy the mission ID from output
python -m backend.orchestrator.cli.main plan create --mission <mission_id>
```

Expected: mission created, plan created with run_id

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/service/app.py tests/orchestrator_v2/test_app.py
git commit -m "feat(orch): add mission and plan CRUD endpoints"
```

---

## Task 3: validator.py — Task List Validation

Pure logic module. No Ollama, no store. Reuses `domain/dependencies.detect_cycles`.

**Files:**
- Create: `backend/orchestrator/planning/validator.py`
- Create: `backend/orchestrator/planning/__init__.py`
- Create: `tests/orchestrator_v2/test_planner_validator.py`

- [ ] **Step 1: Create the package marker**

Create `backend/orchestrator/planning/__init__.py` (empty file).

- [ ] **Step 2: Write the failing tests**

Create `tests/orchestrator_v2/test_planner_validator.py`:

```python
import pytest
from backend.orchestrator.planning.validator import validate_raw_tasks, ValidationError


def test_valid_task_list_passes():
    tasks = [
        {"title": "Set up project", "goal": "Create directory structure", "dependencies": []},
        {"title": "Write code", "goal": "Implement the feature", "dependencies": ["Set up project"]},
    ]
    validate_raw_tasks(tasks)  # should not raise


def test_empty_title_raises():
    tasks = [{"title": "", "goal": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="title"):
        validate_raw_tasks(tasks)


def test_missing_title_raises():
    tasks = [{"goal": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="title"):
        validate_raw_tasks(tasks)


def test_empty_goal_raises():
    tasks = [{"title": "Do something", "goal": "", "dependencies": []}]
    with pytest.raises(ValidationError, match="goal"):
        validate_raw_tasks(tasks)


def test_missing_goal_raises():
    tasks = [{"title": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="goal"):
        validate_raw_tasks(tasks)


def test_unknown_dependency_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Nonexistent Task"]},
    ]
    with pytest.raises(ValidationError, match="Nonexistent Task"):
        validate_raw_tasks(tasks)


def test_cycle_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Task B"]},
        {"title": "Task B", "goal": "Do B", "dependencies": ["Task A"]},
    ]
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        validate_raw_tasks(tasks)


def test_empty_task_list_passes():
    validate_raw_tasks([])  # degenerate case — planner returned nothing


def test_self_dependency_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Task A"]},
    ]
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        validate_raw_tasks(tasks)
```

- [ ] **Step 3: Run tests to see them fail**

```bash
pytest tests/orchestrator_v2/test_planner_validator.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement validator.py**

Create `backend/orchestrator/planning/validator.py`:

```python
from __future__ import annotations
from backend.orchestrator.domain.dependencies import detect_cycles, CycleError
from backend.orchestrator.domain.models import Dependency, DependencyType, Task


class ValidationError(ValueError):
    pass


def validate_raw_tasks(tasks: list[dict]) -> None:
    """Validate a list of raw task dicts from the planner before saving.

    Checks:
    - Every task has non-empty 'title' and 'goal'
    - All dependency references name a title that exists in the batch
    - No cycles in the dependency graph

    Raises ValidationError describing the first failure found.
    Raises nothing if the list is empty (valid degenerate case).
    """
    titles = {t.get("title", "") for t in tasks}

    for i, task in enumerate(tasks):
        title = task.get("title", "")
        if not title or not title.strip():
            raise ValidationError(f"Task at index {i} is missing a non-empty 'title'")
        goal = task.get("goal", "")
        if not goal or not goal.strip():
            raise ValidationError(f"Task '{title}' is missing a non-empty 'goal'")
        for dep_title in task.get("dependencies", []):
            if dep_title not in titles:
                raise ValidationError(
                    f"Task '{title}' depends on '{dep_title}', which does not exist in this batch"
                )

    # Build stub Task objects solely to reuse detect_cycles
    title_to_id = {t["title"]: str(i) for i, t in enumerate(tasks)}
    stub_tasks: list[Task] = []
    for t in tasks:
        task_id = title_to_id[t["title"]]
        deps = [
            Dependency(task_id=title_to_id[dep_title], type=DependencyType.completion)
            for dep_title in t.get("dependencies", [])
        ]
        stub = Task.new(run_id="stub", title=t["title"], goal=t["goal"])
        import dataclasses
        stub = dataclasses.replace(stub, id=task_id, dependencies=deps)
        stub_tasks.append(stub)

    try:
        detect_cycles(stub_tasks)
    except CycleError as e:
        raise ValidationError(str(e)) from e
```

- [ ] **Step 5: Run tests to see them pass**

```bash
pytest tests/orchestrator_v2/test_planner_validator.py -v
```

Expected: 8 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/planning/ tests/orchestrator_v2/test_planner_validator.py
git commit -m "feat(planner): add task list validator with cycle detection"
```

---

## Task 4: prompt.py — System Prompt for qwen3:8b

Static template. No tests — it's configuration, not logic.

**Files:**
- Create: `backend/orchestrator/planning/prompt.py`

- [ ] **Step 1: Create prompt.py**

Create `backend/orchestrator/planning/prompt.py`:

```python
SYSTEM_PROMPT = """\
You are a task decomposition assistant. Given a mission, decompose it into 3-8 concrete, executable tasks.

Rules:
- Output ONLY valid JSON. No explanation, no markdown, no prose.
- Each task must have: title (short string), goal (clear sentence), dependencies (list of title strings from this batch), done_criteria (one sentence definition of done).
- Dependencies must reference exact titles of other tasks in your output.
- Form a valid DAG: no cycles, no self-dependencies.
- Tasks should be small enough to execute reliably but large enough to be meaningful.

Output schema:
{
  "tasks": [
    {
      "title": "...",
      "goal": "...",
      "dependencies": [],
      "done_criteria": "..."
    }
  ]
}
"""


def build_user_message(title: str, goal: str, success_condition: str = "") -> str:
    parts = [f"Mission: {title}", f"Goal: {goal}"]
    if success_condition:
        parts.append(f"Success condition: {success_condition}")
    return "\n".join(parts)
```

- [ ] **Step 2: Commit**

```bash
git add backend/orchestrator/planning/prompt.py
git commit -m "feat(planner): add qwen3:8b system prompt template"
```

---

## Task 5: planner.py — Core Planner Module

Calls Ollama, parses JSON, validates, returns Task list. Tested with a mocked HTTP client.

**Files:**
- Create: `backend/orchestrator/planning/planner.py`
- Create: `tests/orchestrator_v2/test_planner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator_v2/test_planner.py`:

```python
from __future__ import annotations
import json
import pytest
import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.planning.planner import generate_tasks, OllamaUnavailable, PlannerError
from backend.orchestrator.domain.models import Mission, Task


def make_mission(**kwargs) -> Mission:
    m = Mission.new(title="Build REST API", goal="Create user auth endpoints",
                    success_condition="All endpoints return correct responses")
    return dataclasses.replace(m, **kwargs) if kwargs else m


def _mock_ollama_response(tasks: list[dict]) -> MagicMock:
    """Build a mock httpx.AsyncClient that returns a planner response."""
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "message": {
                "role": "assistant",
                "content": json.dumps({"tasks": tasks}),
            }
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_generate_tasks_returns_task_list():
    raw = [
        {"title": "Set up project", "goal": "Create directories", "dependencies": [], "done_criteria": "Dirs exist"},
        {"title": "Write code", "goal": "Implement auth", "dependencies": ["Set up project"], "done_criteria": "Tests pass"},
    ]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="run_1")

    assert len(tasks) == 2
    assert tasks[0].title == "Set up project"
    assert tasks[1].title == "Write code"
    assert tasks[1].dependencies[0].task_id == tasks[0].id


@pytest.mark.asyncio
async def test_generate_tasks_sets_run_id():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="my_run")

    assert all(t.run_id == "my_run" for t in tasks)


@pytest.mark.asyncio
async def test_generate_tasks_context_refs_empty():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="r1")

    assert tasks[0].context_refs == []


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_ollama_unavailable():
    import httpx
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=client):
        with pytest.raises(OllamaUnavailable):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_invalid_json():
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "message": {"role": "assistant", "content": "not json at all"}
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=client):
        with pytest.raises(PlannerError, match="[Pp]arse"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_validation_failure():
    # Cycle: A depends on B, B depends on A
    raw = [
        {"title": "A", "goal": "Do A", "dependencies": ["B"], "done_criteria": "done"},
        {"title": "B", "goal": "Do B", "dependencies": ["A"], "done_criteria": "done"},
    ]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(PlannerError, match="[Vv]alidat"):
            await generate_tasks(make_mission(), run_id="r1")
```

- [ ] **Step 2: Run tests to see them fail**

```bash
pytest tests/orchestrator_v2/test_planner.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement planner.py**

Create `backend/orchestrator/planning/planner.py`:

```python
from __future__ import annotations
import dataclasses
import json

import httpx

from ..domain.models import Dependency, DependencyType, Mission, Task
from .prompt import SYSTEM_PROMPT, build_user_message
from .validator import ValidationError, validate_raw_tasks


class OllamaUnavailable(RuntimeError):
    """Raised when the Ollama server cannot be reached."""


class PlannerError(RuntimeError):
    """Raised when the planner produces unusable output."""


async def generate_tasks(
    mission: Mission,
    run_id: str,
    base_url: str = "http://localhost:11434",
    model: str = "qwen3:8b",
) -> list[Task]:
    """Call Ollama to decompose a mission into Task objects.

    Returns a list of Task objects with dependencies resolved to IDs,
    ready to be saved to the store.

    Raises:
        OllamaUnavailable: if the Ollama server is unreachable.
        PlannerError: if the model output cannot be parsed or fails validation.
    """
    user_msg = build_user_message(
        title=mission.title,
        goal=mission.goal,
        success_condition=mission.success_condition,
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120.0) as client:
            resp = await client.post("/api/chat", json=payload)
            resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise OllamaUnavailable(f"Ollama unreachable at {base_url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PlannerError(f"Ollama HTTP error: {exc}") from exc

    raw_content = resp.json().get("message", {}).get("content", "")
    try:
        data = json.loads(raw_content)
        raw_tasks: list[dict] = data["tasks"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlannerError(
            f"Parse error — model output was not valid JSON with a 'tasks' key. "
            f"Raw output: {raw_content!r}"
        ) from exc

    try:
        validate_raw_tasks(raw_tasks)
    except ValidationError as exc:
        raise PlannerError(f"Validation failed: {exc}") from exc

    return _build_tasks(raw_tasks, run_id)


def _build_tasks(raw_tasks: list[dict], run_id: str) -> list[Task]:
    """Convert validated raw task dicts into Task domain objects with resolved IDs."""
    # First pass: create tasks without dependencies to get IDs
    tasks_by_title: dict[str, Task] = {}
    for raw in raw_tasks:
        task = Task.new(
            run_id=run_id,
            title=raw["title"],
            goal=raw["goal"],
            done_criteria=raw.get("done_criteria", ""),
            context_refs=[],
        )
        tasks_by_title[raw["title"]] = task

    # Second pass: resolve dependency titles → IDs
    result: list[Task] = []
    for raw in raw_tasks:
        task = tasks_by_title[raw["title"]]
        deps = [
            Dependency(
                task_id=tasks_by_title[dep_title].id,
                type=DependencyType.completion,
            )
            for dep_title in raw.get("dependencies", [])
        ]
        result.append(dataclasses.replace(task, dependencies=deps))

    return result
```

- [ ] **Step 4: Run tests to see them pass**

```bash
pytest tests/orchestrator_v2/test_planner.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -10
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/planning/planner.py tests/orchestrator_v2/test_planner.py
git commit -m "feat(planner): add planner module — Ollama task decomposition"
```

---

## Task 6: POST /missions/{id}/generate Endpoint

Wire the planner into the service. Saves tasks to the store in one atomic call.

**Files:**
- Modify: `backend/orchestrator/service/app.py`
- Modify: `tests/orchestrator_v2/test_app.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/orchestrator_v2/test_app.py`:

```python
from unittest.mock import AsyncMock, patch
from backend.orchestrator.domain.models import Task


@pytest.mark.asyncio
async def test_generate_plan_creates_tasks(store, registry, client):
    # Create mission + plan + run
    m_resp = await client.post("/missions", json={"title": "Build API", "goal": "Make endpoints"})
    mission_id = m_resp.json()["id"]

    stub_tasks = [
        Task.new(run_id="stub", title="Set up project", goal="Create structure"),
        Task.new(run_id="stub", title="Write code", goal="Implement feature"),
    ]

    with patch(
        "backend.orchestrator.service.app.generate_tasks",
        new=AsyncMock(return_value=stub_tasks),
    ):
        resp = await client.post(f"/missions/{mission_id}/generate")

    assert resp.status_code == 201
    data = resp.json()
    assert "plan_id" in data
    assert "run_id" in data
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["title"] == "Set up project"


@pytest.mark.asyncio
async def test_generate_plan_mission_not_found(store, registry, client):
    resp = await client.post("/missions/nonexistent/generate")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to see them fail**

```bash
pytest tests/orchestrator_v2/test_app.py -k "test_generate_plan" -v
```

Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Add the endpoint to app.py**

At the top of `backend/orchestrator/service/app.py`, add the planner import:

```python
from ..planning.planner import generate_tasks, OllamaUnavailable, PlannerError
```

Then add the endpoint at the end of the missions section:

```python
@app.post("/missions/{mission_id}/generate", status_code=201)
async def generate_plan(mission_id: str, store: StoreDep, background_tasks: BackgroundTasks):
    mission = await store.missions.get(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    plan = Plan.new(mission_id=mission_id)
    run = Run.new(mission_id=mission_id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_plan(plan)
    await store.missions.save_run(run)

    try:
        tasks = await generate_tasks(mission, run_id=run.id)
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except PlannerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    for task in tasks:
        await store.tasks.save(task)

    return {
        "plan_id": plan.id,
        "run_id": run.id,
        "tasks": [{"id": t.id, "title": t.title, "goal": t.goal} for t in tasks],
    }
```

- [ ] **Step 4: Run tests to see them pass**

```bash
pytest tests/orchestrator_v2/test_app.py -k "test_generate_plan" -v
```

Expected: 2 PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -10
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/service/app.py tests/orchestrator_v2/test_app.py
git commit -m "feat(orch): add POST /missions/{id}/generate endpoint"
```

---

## Task 7: orch plan generate CLI Command

Add `orch plan generate --mission <id>` to the CLI.

**Files:**
- Modify: `backend/orchestrator/cli/commands/plan.py`

- [ ] **Step 1: Add the command**

In `backend/orchestrator/cli/commands/plan.py`, add after the existing imports:

```python
import json as _json
```

Then add this command at the end of the file:

```python
@app.command("generate")
def plan_generate(
    mission_id: str = typer.Option(..., "--mission", "-m", help="Mission ID to decompose"),
):
    """Auto-generate a task plan for a mission using the local Ollama planner."""
    typer.echo(f"Generating plan for mission {mission_id[:8]}...")
    try:
        resp = httpx.post(f"{_BASE}/missions/{mission_id}/generate", timeout=180.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} — {exc.response.text}", err=True)
        raise typer.Exit(code=1)
    except httpx.ConnectError:
        typer.echo(f"Error: could not connect to {_BASE}. Is the service running?", err=True)
        raise typer.Exit(code=1)

    data = resp.json()
    typer.echo(f"Plan:  {data['plan_id']}")
    typer.echo(f"Run:   {data['run_id']}")
    typer.echo(f"\nTasks ({len(data['tasks'])}):")
    for i, task in enumerate(data["tasks"], 1):
        typer.echo(f"  {i}. [{task['id'][:8]}] {task['title']}")
        typer.echo(f"     Goal: {task['goal']}")
```

- [ ] **Step 2: Manual test**

With the service running:
```bash
python -m backend.orchestrator.cli.main mission new --title "Build a calculator" --goal "Create a Python CLI calculator with add subtract multiply divide"
# Copy the mission_id
python -m backend.orchestrator.cli.main plan generate --mission <mission_id>
```

Expected: prints plan_id, run_id, and a numbered task list

- [ ] **Step 3: Commit**

```bash
git add backend/orchestrator/cli/commands/plan.py
git commit -m "feat(cli): add 'orch plan generate' command"
```

---

## Task 8: End-to-End Smoke Test

Run the full workflow: mission → generate plan → start run → watch execution.

**Files:** None (diagnostic)

- [ ] **Step 1: Create a real mission**

```bash
python -m backend.orchestrator.cli.main mission new \
  --title "Write a Python utility" \
  --goal "Create a script that counts lines of code in a directory" \
  --success-condition "Script runs and prints a count"
```

Note the `mission_id`.

- [ ] **Step 2: Generate the plan**

```bash
python -m backend.orchestrator.cli.main plan generate --mission <mission_id>
```

Expected: task list printed. Note the `run_id`.

- [ ] **Step 3: Start the run**

```bash
python -m backend.orchestrator.cli.main run start <run_id>
```

Wait for completion (Ollama will execute each task).

- [ ] **Step 4: Check results**

```bash
python -m backend.orchestrator.cli.main status
python -m backend.orchestrator.cli.main events <run_id>
```

Expected: tasks show `completed` or at worst `blocked` (not `failed`).

- [ ] **Step 5: Final test suite run**

```bash
pytest tests/orchestrator_v2/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: planner agent complete — mission → auto task graph → execution"
```

---

## Summary

After this plan:

```
orch mission new "Build X"
orch plan generate --mission <id>    ← NEW: auto-generates tasks via Ollama
orch run start <run_id>              ← existing: executes the task graph
```

The system is autonomous for task decomposition. Vector layer and adaptive re-planning are explicitly out of scope — add them once you see real failure modes from actual runs.
