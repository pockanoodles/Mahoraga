import asyncio
import pytest
from backend.orchestrator_svc.models import Task, WorkerResult, Event
from backend.orchestrator_svc.worker_registry import WorkerRegistry
from backend.orchestrator_svc.event_bus import EventBus
from backend.orchestrator_svc.task_store import TaskStore


class MockWorker:
    worker_id = "mock"
    display_name = "Mock Worker"

    async def submit_task(self, task: Task) -> str:
        return task.id

    async def stream_events(self, task_id: str):
        yield Event(type="task.completed", task_id=task_id, worker_id="mock")

    async def get_result(self, task_id: str) -> WorkerResult:
        return WorkerResult(task_id=task_id, worker_id="mock", status="completed", summary="done")

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def health(self) -> dict:
        return {"status": "ok"}


def test_registry_register_and_get():
    registry = WorkerRegistry()
    registry.register(MockWorker())
    assert registry.get("mock").worker_id == "mock"
    assert "mock" in registry.list_workers()


def test_registry_get_unknown_raises():
    registry = WorkerRegistry()
    with pytest.raises(KeyError, match="Worker 'nope' not registered"):
        registry.get("nope")


async def test_event_bus_publish_notifies_subscriber(tmp_path):
    store = TaskStore(db_path=tmp_path / "test.db")
    await store.connect()
    bus = EventBus(store)

    task = Task.new(title="T", goal="G", task_type="code")
    await store.save_task(task)

    queue = await bus.subscribe(task.id)
    event = Event(type="task.created", task_id=task.id)
    await bus.publish(event)

    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.type == "task.created"

    events = await store.get_events(task.id)
    assert len(events) == 1
    await store.close()


# --- Service endpoint tests ---

import backend.orchestrator_svc.service as svc_module
from httpx import AsyncClient, ASGITransport


class _TrackingWorker:
    """Extension-like worker that records submitted and cancelled tasks."""
    worker_id = "extension"
    display_name = "Tracking Worker"

    def __init__(self):
        self.submitted: list[Task] = []
        self.cancelled: list[str] = []

    async def submit_task(self, task: Task) -> str:
        self.submitted.append(task)
        return task.id

    async def stream_events(self, task_id: str):
        yield Event(type="task.completed", task_id=task_id, worker_id="extension",
                    content={"summary": "done"})

    async def get_result(self, task_id: str) -> WorkerResult:
        return WorkerResult(task_id=task_id, worker_id="extension",
                            status="completed", summary="done")

    async def cancel_task(self, task_id: str) -> None:
        self.cancelled.append(task_id)

    async def health(self) -> dict:
        return {"status": "ok"}


@pytest.fixture
async def svc_client(tmp_path):
    store = TaskStore(db_path=tmp_path / "test.db")
    await store.connect()
    bus = EventBus(store)
    registry = WorkerRegistry()
    worker = _TrackingWorker()
    registry.register(worker)

    svc_module._store = store
    svc_module._registry = registry
    svc_module._bus = bus

    async with AsyncClient(
        transport=ASGITransport(app=svc_module.app), base_url="http://test"
    ) as c:
        yield c, worker

    await store.close()


async def test_submit_routes_code_to_extension(svc_client):
    c, worker = svc_client
    resp = await c.post("/tasks", json={
        "title": "Fix login",
        "goal": "Add test for the login function",
        "task_type": "code",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["worker_id"] == "extension"
    assert data["status"] == "running"
    assert len(worker.submitted) == 1


async def test_submit_routes_plan_to_claude(svc_client):
    c, worker = svc_client
    # No claude worker registered — expect 503
    resp = await c.post("/tasks", json={
        "title": "Plan refactor",
        "goal": "Plan the auth module refactor",
        "task_type": "plan",
    })
    assert resp.status_code == 503


async def test_get_task_returns_stored(svc_client):
    c, _ = svc_client
    resp = await c.post("/tasks", json={
        "title": "Fix login", "goal": "Fix import for utils", "task_type": "code"
    })
    task_id = resp.json()["task_id"]
    resp2 = await c.get(f"/tasks/{task_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == task_id
    assert resp2.json()["status"] == "running"


async def test_get_unknown_task_returns_404(svc_client):
    c, _ = svc_client
    assert (await c.get("/tasks/nonexistent")).status_code == 404


async def test_cancel_task(svc_client):
    c, worker = svc_client
    resp = await c.post("/tasks", json={"title": "T", "goal": "Add test for X", "task_type": "code"})
    task_id = resp.json()["task_id"]
    resp2 = await c.delete(f"/tasks/{task_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "cancelled"
    assert task_id in worker.cancelled


async def test_events_logged_on_submit(svc_client):
    c, _ = svc_client
    resp = await c.post("/tasks", json={"title": "T", "goal": "Rename the util function", "task_type": "code"})
    task_id = resp.json()["task_id"]
    events_resp = await c.get(f"/tasks/{task_id}/events")
    types = [e["type"] for e in events_resp.json()]
    assert "task.created" in types
    assert "task.assigned" in types
    assert "task.started" in types


async def test_list_tasks(svc_client):
    c, _ = svc_client
    await c.post("/tasks", json={"title": "T1", "goal": "Add test for A", "task_type": "code"})
    await c.post("/tasks", json={"title": "T2", "goal": "Add test for B", "task_type": "code"})
    resp = await c.get("/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2
