from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..domain.models import TaskStatus
from ..domain.transitions import IllegalTransition
from ..store.base import Store
from ..workers.claude import ClaudeWorker
from ..workers.extension import ExtensionWorker
from ..workers.registry import WorkerRegistry
from .approvals import grant_approval, reject_approval
from .executor import run_task as _run_task
from ..workers.ollama import OllamaWorker
from .run_executor import run_run

# ── singletons (replaced via dependency_overrides in tests) ──────────────────

_store: Store | None = None
_registry: WorkerRegistry | None = None


def get_store() -> Store:
    assert _store is not None, "Store not initialised"
    return _store


def get_registry() -> WorkerRegistry:
    assert _registry is not None, "Registry not initialised"
    return _registry


StoreDep = Annotated[Store, Depends(get_store)]
RegistryDep = Annotated[WorkerRegistry, Depends(get_registry)]


# ── lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _registry
    _store = await Store.connect()
    _registry = WorkerRegistry()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        _registry.register(ClaudeWorker(api_key=api_key))
    _registry.register(ExtensionWorker(
        base_url=os.getenv("EXTENSION_URL", "http://localhost:3000")
    ))
    _registry.register(OllamaWorker(
        model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        base_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    ))

    # Orphan recovery: tasks left in_progress from a crashed previous run
    for orphan in await _store.tasks.list_by_status(TaskStatus.in_progress):
        await _store.tasks.update_status(orphan.id, TaskStatus.failed)

    yield
    await _store.close()


app = FastAPI(title="Orchestrator v2", lifespan=lifespan)


# ── request / response models ─────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    run_id: str


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/tasks/{task_id}")
async def get_task(task_id: str, store: StoreDep):
    task = await store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/runs/{run_id}/tasks")
async def list_run_tasks(run_id: str, store: StoreDep):
    return await store.tasks.list_by_run(run_id)


@app.get("/runs/{run_id}/events")
async def list_run_events(run_id: str, store: StoreDep):
    return await store.events.list_by_run(run_id)


@app.get("/tasks/{task_id}/events")
async def list_task_events(task_id: str, store: StoreDep):
    return await store.events.list_by_task(task_id)


@app.get("/tasks/{task_id}/attempts")
async def list_task_attempts(task_id: str, store: StoreDep):
    return await store.tasks.list_attempts(task_id)


@app.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, req: ApprovalRequest, store: StoreDep):
    try:
        await grant_approval(req.run_id, task_id, store)
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "approved"}


@app.post("/tasks/{task_id}/reject")
async def reject_task(task_id: str, req: ApprovalRequest, store: StoreDep):
    try:
        await reject_approval(req.run_id, task_id, store)
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "rejected"}


@app.post("/tasks/{task_id}/run", status_code=202)
async def execute_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    store: StoreDep,
    registry: RegistryDep,
):
    task = await store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.ready, TaskStatus.blocked):
        raise HTTPException(status_code=409, detail="Task is not in a runnable state")
    background_tasks.add_task(_run_task, task_id, store, registry)
    return {"task_id": task_id, "status": "queued"}


@app.get("/workers/health")
async def workers_health(registry: RegistryDep):
    results = await registry.health_all()
    return {worker_id: {"worker_id": h.worker_id, "healthy": h.healthy, "detail": h.detail}
            for worker_id, h in results.items()}


@app.post("/runs/{plan_id}/start", status_code=202)
async def start_run(
    plan_id: str,
    background_tasks: BackgroundTasks,
    store: StoreDep,
    registry: RegistryDep,
):
    plan = await store.missions.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    from ..domain.models import Run, RunMode
    run = Run.new(mission_id=plan.mission_id, plan_id=plan_id, mode=RunMode.direct)
    await store.missions.save_run(run)
    background_tasks.add_task(run_run, run.id, store, registry)
    return {"run_id": run.id, "status": "queued"}


@app.get("/runs/{run_id}")
async def get_run(run_id: str, store: StoreDep):
    run = await store.missions.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/runs")
async def list_runs(store: StoreDep, mission_id: str | None = None):
    if mission_id:
        return await store.missions.list_runs(mission_id)
    return await store.missions.list_all_runs()


@app.delete("/runs/{run_id}")
async def cancel_run(run_id: str, store: StoreDep):
    run = await store.missions.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    from ..domain.models import RunStatus
    await store.missions.update_run_status(run_id, RunStatus.cancelled)
    return {"run_id": run_id, "status": "cancelled"}
