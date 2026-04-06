from __future__ import annotations
import datetime
import logging
import time
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import anthropic

from ..adaptive.store import AdaptiveStore
from ..channels.base import ChannelMessage
from ..channels.web import _STATIC_DIR
from ..domain.models import Mission, Plan, Run, RunMode, RunStatus, TaskStatus
from ..domain.transitions import IllegalTransition
from ..gateway import Gateway
from ..store.base import Store
from ..store.chat_log import ChatLogStore
from ..tracking.ledger import CostLedger
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
_gateway: Gateway | None = None
_adaptive_store: AdaptiveStore | None = None
_cost_ledger: CostLedger | None = None


def get_store() -> Store:
    assert _store is not None, "Store not initialised"
    return _store


def get_registry() -> WorkerRegistry:
    assert _registry is not None, "Registry not initialised"
    return _registry


def get_verifier() -> Verifier:
    assert _verifier is not None, "Verifier not initialised"
    return _verifier


def get_gateway() -> Gateway:
    assert _gateway is not None, "Gateway not initialised"
    return _gateway


StoreDep = Annotated[Store, Depends(get_store)]
RegistryDep = Annotated[WorkerRegistry, Depends(get_registry)]
VerifierDep = Annotated[Verifier, Depends(get_verifier)]
GatewayDep = Annotated[Gateway, Depends(get_gateway)]


# ── lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    global _store, _registry, _verifier, _gateway, _adaptive_store, _cost_ledger
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

    # Adaptive store + cost ledger share the same DB connection as the main store
    _adaptive_store = AdaptiveStore(_store._conn)
    await _adaptive_store.migrate()

    _cost_ledger = CostLedger(_store._conn)
    await _cost_ledger.migrate()

    _gateway = Gateway(
        store=_store,
        registry=_registry,
        verifier=_verifier,
        adaptive_store=_adaptive_store,
        cost_ledger=_cost_ledger,
    )

    yield
    await _store.close()


app = FastAPI(title="Orchestrator v2", lifespan=lifespan)

# ── web chat routes ───────────────────────────────────────────────────────────


class _ChatRequest(BaseModel):
    message: str
    user_id: str = "web-user"


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse(content="<html><body><h1>Mahoraga</h1></body></html>")
    return HTMLResponse(content=index_path.read_text())


@app.post("/chat")
async def chat(request: _ChatRequest, gateway: GatewayDep) -> StreamingResponse:
    msg = ChannelMessage.new(
        user_id=request.user_id,
        channel="web",
        text=request.message,
    )

    async def event_stream():
        try:
            async for chunk in gateway.handle_message(msg):
                yield f"data: {chunk}\n\n"
        except Exception as exc:
            yield f"data: [ERROR] {exc}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Mount static files if the static directory exists
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


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


@app.get("/cost/summary")
async def cost_summary(store: StoreDep, user_id: str = "web-user"):
    if _cost_ledger is None:
        return {"session_usd": 0.0, "total_usd": 0.0, "breakdown": []}

    today_start = datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()

    session_usd = await _cost_ledger.cost_since(user_id=user_id, since=today_start)
    total_usd = await _cost_ledger.total_cost(user_id=user_id)
    breakdown = await _cost_ledger.cost_by_model(user_id=user_id, since=today_start)

    return {
        "session_usd": round(session_usd, 6),
        "total_usd": round(total_usd, 6),
        "breakdown": breakdown,
    }


@app.get("/missions/active")
async def get_active_mission(store: StoreDep):
    """Return the most recently active mission's task graph for the vine chart."""
    all_runs = await store.missions.list_all_runs()
    active_run = None
    for run in all_runs:
        if run.status in (RunStatus.active, RunStatus.paused):
            active_run = run
            break

    if active_run is None:
        return {"mission": None, "run": None, "tasks": []}

    mission = await store.missions.get(active_run.mission_id)
    tasks = await store.tasks.list_by_run(active_run.id)

    task_items = []
    for task in tasks:
        attempts = await store.tasks.list_attempts(task.id)
        worker_id = ""
        if attempts:
            worker_id = attempts[-1].worker_id

        elapsed = 0.0
        if attempts:
            latest = attempts[-1]
            if latest.ended_at is not None and latest.started_at is not None:
                elapsed = round(latest.ended_at - latest.started_at, 1)
            elif latest.started_at is not None:
                elapsed = round(time.time() - latest.started_at, 1)

        task_items.append({
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "parent_task_id": task.parent_task_id,
            "dependencies": [
                {"task_id": d.task_id, "type": d.type.value}
                for d in task.dependencies
            ],
            "worker_id": worker_id,
            "elapsed_seconds": elapsed,
        })

    return {
        "mission": {
            "id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
        } if mission else None,
        "run": {
            "id": active_run.id,
            "status": active_run.status.value,
        },
        "tasks": task_items,
    }


@app.get("/logs/recent")
async def logs_recent(store: StoreDep, user_id: str = "web-user", limit: int = 20):
    limit = min(limit, 50)
    entries = await store.chat_log.list_recent(user_id=user_id, limit=limit)
    return {
        "entries": [
            {
                "id": e.id,
                "user_message": e.user_message,
                "assistant_response": e.assistant_response,
                "worker_id": e.worker_id,
                "cost_usd": e.cost_usd,
                "created_at": e.created_at,
            }
            for e in entries
        ]
    }


@app.get("/settings")
async def get_settings():
    """Return current configuration (read-only). Sensitive values are masked."""

    def mask(val: str | None) -> str:
        if not val:
            return "(not set)"
        if len(val) <= 8:
            return "••••••••"
        return val[:4] + "••••" + val[-4:]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    brave_key = os.getenv("BRAVE_API_KEY")

    return {
        "executor_model": "claude-sonnet-4-6",
        "anthropic_api_key": mask(api_key),
        "telegram_token": mask(tg_token),
        "brave_api_key": mask(brave_key),
        "configured": bool(api_key),
    }


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
        raise HTTPException(status_code=501, detail=str(exc))
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
