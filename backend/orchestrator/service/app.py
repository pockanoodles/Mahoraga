from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel

import anthropic

from ..domain.models import Mission, Plan, Run, RunMode, RunStatus, TaskStatus
from ..domain.transitions import IllegalTransition
from ..store.base import Store
from ..verifier.verifier import Verifier
from ..workers.claude import ClaudeWorker
from ..workers.registry import WorkerRegistry
from .approvals import grant_approval, reject_approval
from .executor import run_task as _run_task
from .run_executor import run_run as _run_run
from ..planning.planner import generate_tasks, PlannerError

# ── singletons (replaced via dependency_overrides in tests) ──────────────────

_store: Store | None = None
_registry: WorkerRegistry | None = None
_verifier: Verifier | None = None


def get_store() -> Store:
    assert _store is not None, "Store not initialised"
    return _store


def get_registry() -> WorkerRegistry:
    assert _registry is not None, "Registry not initialised"
    return _registry


def get_verifier() -> Verifier:
    assert _verifier is not None, "Verifier not initialised"
    return _verifier


StoreDep = Annotated[Store, Depends(get_store)]
RegistryDep = Annotated[WorkerRegistry, Depends(get_registry)]
VerifierDep = Annotated[Verifier, Depends(get_verifier)]


# ── lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    global _store, _registry, _verifier
    _store = await Store.connect()
    _registry = WorkerRegistry()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        _registry.register(ClaudeWorker(api_key=api_key))  # claude:sonnet
        _registry.register(ClaudeWorker(
            api_key=api_key,
            model="claude-opus-4-6",
            worker_id="claude:opus",
            capabilities=["complex_reasoning", "deep_reasoning", "general"],
        ))
        _verifier = Verifier(client=anthropic.Anthropic(api_key=api_key))
    else:
        # No Anthropic key: passthrough verifier (dev/test mode only)
        class _PassthroughVerifier(Verifier):
            def __init__(self) -> None:
                pass
            async def verify(self, task, output):
                from ..verifier.verifier import VerificationResult
                return VerificationResult(score=10, passed=True, feedback="", action="pass")
        _verifier = _PassthroughVerifier()

    # Orphan recovery: tasks left in_progress from a crashed previous run
    for orphan in await _store.tasks.list_by_status(TaskStatus.in_progress):
        await _store.tasks.update_status(orphan.id, TaskStatus.failed)

    yield
    await _store.close()


app = FastAPI(title="Orchestrator v2", lifespan=lifespan)


# ── request / response models ─────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    run_id: str


class CreateMissionRequest(BaseModel):
    title: str
    goal: str
    background: str = ""
    success_condition: str = ""


class CreatePlanRequest(BaseModel):
    mission_id: str
    mode: str = "direct"


class LogEventItem(BaseModel):
    id: str
    type: str
    task_id: str | None
    attempt_id: str | None
    ts: float


class LogRunItem(BaseModel):
    id: str
    mission_id: str
    status: str
    created_at: float
    events: list[LogEventItem]


class LogsResponse(BaseModel):
    runs: list[LogRunItem]


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
    verifier: VerifierDep,
):
    task = await store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.ready, TaskStatus.blocked):
        raise HTTPException(status_code=409, detail="Task is not in a runnable state")
    background_tasks.add_task(_run_task, task_id, store, registry, verifier)
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
    verifier: VerifierDep,
):
    plan = await store.missions.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Reuse the paused run created by /generate if it has tasks, otherwise create fresh
    existing_runs = await store.missions.list_runs(plan.mission_id)
    run = None
    for r in existing_runs:
        if r.plan_id == plan_id and r.status == RunStatus.paused:
            tasks = await store.tasks.list_by_run(r.id)
            if tasks:
                run = r
                break

    if run is None:
        run = Run.new(mission_id=plan.mission_id, plan_id=plan_id, mode=RunMode.direct)
        await store.missions.save_run(run)

    background_tasks.add_task(_run_run, run.id, store, registry, verifier)
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


@app.get("/logs", response_model=LogsResponse)
async def get_logs(store: StoreDep, limit: int = 5) -> LogsResponse:
    limit = min(limit, 20)
    all_runs = await store.missions.list_all_runs()  # already DESC by created_at
    runs = all_runs[:limit]
    run_items = []
    for run in runs:
        events = await store.events.list_by_run(run.id)
        run_items.append(LogRunItem(
            id=run.id,
            mission_id=run.mission_id,
            status=run.status.value,
            created_at=run.created_at,
            events=[
                LogEventItem(
                    id=e.id,
                    type=e.type,
                    task_id=e.task_id,
                    attempt_id=e.attempt_id,
                    ts=e.ts,
                )
                for e in events
            ],
        ))
    return LogsResponse(runs=run_items)


@app.delete("/runs/{run_id}")
async def cancel_run(run_id: str, store: StoreDep):
    run = await store.missions.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await store.missions.update_run_status(run_id, RunStatus.cancelled)
    return {"run_id": run_id, "status": "cancelled"}


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
    try:
        mode = RunMode(req.mode)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid mode {req.mode!r}. Valid values: {[m.value for m in RunMode]}")
    plan = Plan.new(mission_id=req.mission_id)
    run = Run.new(mission_id=req.mission_id, plan_id=plan.id, mode=mode)
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


@app.post("/missions/{mission_id}/generate", status_code=201)
async def generate_plan(mission_id: str, store: StoreDep):
    mission = await store.missions.get(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    plan = Plan.new(mission_id=mission_id)
    run = Run.new(mission_id=mission_id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_plan(plan)
    await store.missions.save_run(run)

    try:
        tasks = await generate_tasks(mission, run_id=run.id)
    except NotImplementedError as exc:
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
