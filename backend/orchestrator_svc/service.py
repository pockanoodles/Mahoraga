from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .models import Task, WorkerResult, Event
from .task_store import TaskStore
from .worker_registry import WorkerRegistry
from .event_bus import EventBus
from .routing import route, should_escalate

# Module-level singletons — replaced in tests via direct assignment
_store = TaskStore()
_registry = WorkerRegistry()
_bus = EventBus(_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _store.connect()
    yield
    await _store.close()


app = FastAPI(title="Orchestrator Service", lifespan=lifespan)


class SubmitRequest(BaseModel):
    title: str
    goal: str
    task_type: str
    priority: str = "normal"
    parent_id: str | None = None
    context: dict = {}
    constraints: list[str] = []


async def _process_result(task: Task, worker_id: str) -> None:
    """Background: consume stream_events, save result, handle escalation."""
    worker = _registry.get(worker_id)
    summary = ""

    async for event in worker.stream_events(task.id):
        await _bus.publish(event)
        if event.type == "task.completed":
            result = await worker.get_result(task.id)
            await _store.save_result(result)
            await _store.update_task_status(task.id, "completed")
            return
        elif event.type == "task.failed":
            summary = event.content.get("error", "worker reported failure")
            break

    current = await _store.get_task(task.id)
    if current and should_escalate(current):
        await _store.increment_escalation(task.id)
        await _store.update_task_status(task.id, "assigned", assigned_worker="claude")
        await _bus.publish(Event(type="task.escalated", task_id=task.id, worker_id="claude"))
        try:
            claude = _registry.get("claude")
            await claude.submit_task(current)
            await _store.update_task_status(task.id, "running")
            asyncio.create_task(_process_result(current, "claude"))
            return
        except KeyError:
            pass

    result = WorkerResult(
        task_id=task.id, worker_id=worker_id, status="failed", summary=summary
    )
    await _store.save_result(result)
    await _store.update_task_status(task.id, "failed")
    await _bus.publish(Event(type="task.failed", task_id=task.id, worker_id=worker_id))


@app.post("/tasks", status_code=201)
async def submit_task(req: SubmitRequest):
    task = Task.new(
        title=req.title, goal=req.goal, task_type=req.task_type,
        priority=req.priority, parent_id=req.parent_id,
        context=req.context, constraints=req.constraints,
    )
    await _store.save_task(task)
    await _bus.publish(Event(type="task.created", task_id=task.id, content={"title": task.title}))

    worker_id = route(task)
    await _store.update_task_status(task.id, "assigned", assigned_worker=worker_id)
    await _bus.publish(Event(type="task.assigned", task_id=task.id, worker_id=worker_id))

    try:
        worker = _registry.get(worker_id)
    except KeyError:
        raise HTTPException(status_code=503, detail=f"Worker '{worker_id}' not available")

    await worker.submit_task(task)
    await _store.update_task_status(task.id, "running")
    await _bus.publish(Event(type="task.started", task_id=task.id, worker_id=worker_id))

    asyncio.create_task(_process_result(task, worker_id))

    return {"task_id": task.id, "worker_id": worker_id, "status": "running"}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = await _store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/tasks/{task_id}/result")
async def get_result(task_id: str):
    result = await _store.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not yet available")
    return result


@app.get("/tasks/{task_id}/events")
async def list_events(task_id: str):
    return await _store.get_events(task_id)


@app.get("/tasks")
async def list_tasks(status: str | None = None):
    return await _store.list_tasks(status)


@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    task = await _store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_worker:
        try:
            await _registry.get(task.assigned_worker).cancel_task(task_id)
        except KeyError:
            pass
    await _store.update_task_status(task_id, "cancelled")
    await _bus.publish(Event(type="task.cancelled", task_id=task_id))
    return {"status": "cancelled"}


@app.get("/workers/health")
async def workers_health():
    return await _registry.health_all()
