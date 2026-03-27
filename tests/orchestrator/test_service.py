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
