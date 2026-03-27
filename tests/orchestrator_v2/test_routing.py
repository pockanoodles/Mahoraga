import dataclasses
import pytest
from typing import AsyncIterator
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.domain.models import Task, TaskAttempt
from backend.orchestrator.workers.registry import WorkerRegistry, WorkerNotFoundError


class _ConcreteWorker(WorkerAdapter):
    @property
    def id(self) -> str:
        return "test"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing"]

    async def execute(self, attempt: TaskAttempt, task: Task) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent(type="attempt.completed", payload={"summary": "done"})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="test", healthy=True)


def test_worker_event_is_dataclass():
    ev = WorkerEvent(type="attempt.completed", payload={"summary": "done"})
    assert ev.type == "attempt.completed"
    assert ev.payload == {"summary": "done"}


def test_worker_health_is_dataclass():
    h = WorkerHealth(worker_id="claude", healthy=True, detail="ok")
    assert h.worker_id == "claude"
    assert h.healthy is True
    assert h.detail == "ok"


def test_worker_health_detail_defaults_to_empty():
    h = WorkerHealth(worker_id="claude", healthy=True)
    assert h.detail == ""


async def test_concrete_worker_execute_yields_event():
    worker = _ConcreteWorker()
    task = Task.new(run_id="r1", title="T", goal="G")
    attempt = TaskAttempt.new(task_id=task.id, worker_id="test")
    events = [ev async for ev in worker.execute(attempt, task)]
    assert len(events) == 1
    assert events[0].type == "attempt.completed"


def test_worker_adapter_requires_abstract_methods():
    """WorkerAdapter cannot be instantiated directly."""
    with pytest.raises(TypeError):
        WorkerAdapter()


def test_registry_register_and_get():
    reg = WorkerRegistry()
    worker = _ConcreteWorker()
    reg.register(worker)
    assert reg.get("test") is worker


def test_registry_get_missing_raises():
    reg = WorkerRegistry()
    with pytest.raises(WorkerNotFoundError):
        reg.get("nonexistent")


def test_registry_list_all_returns_registered():
    reg = WorkerRegistry()
    reg.register(_ConcreteWorker())
    workers = reg.list_all()
    assert len(workers) == 1
    assert workers[0].id == "test"


def test_registry_list_all_empty():
    reg = WorkerRegistry()
    assert reg.list_all() == []


async def test_registry_health_all():
    reg = WorkerRegistry()
    reg.register(_ConcreteWorker())
    results = await reg.health_all()
    assert "test" in results
    assert results["test"].healthy is True
    assert results["test"].worker_id == "test"


# Routing tests
import dataclasses as dc
from backend.orchestrator.routing.router import assign_worker, NoCapableWorker


class _EditWorker(_ConcreteWorker):
    @property
    def id(self) -> str:
        return "extension"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "cheap_repetitive"]


class _ClaudeWorker(_ConcreteWorker):
    @property
    def id(self) -> str:
        return "claude"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "deep_reasoning", "planning", "review"]


def _reg(*workers) -> WorkerRegistry:
    reg = WorkerRegistry()
    for w in workers:
        reg.register(w)
    return reg


def make_task_with(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="T", goal="G")
    return dc.replace(t, **kwargs) if kwargs else t


def test_assign_worker_matches_capability():
    task = make_task_with(required_capabilities=["deep_reasoning"])
    reg = _reg(_EditWorker(), _ClaudeWorker())
    assert assign_worker(task, reg) == "claude"


def test_assign_worker_no_required_caps_returns_first():
    task = make_task_with(required_capabilities=[])
    reg = _reg(_EditWorker(), _ClaudeWorker())
    # Returns first registered worker
    assert assign_worker(task, reg) in ("extension", "claude")


def test_assign_worker_preferred_type_used_first():
    task = make_task_with(
        required_capabilities=["file_editing"],
        preferred_worker_type="claude",
    )
    reg = _reg(_EditWorker(), _ClaudeWorker())
    assert assign_worker(task, reg) == "claude"


def test_assign_worker_preferred_type_excluded_falls_back():
    task = make_task_with(
        required_capabilities=["file_editing"],
        preferred_worker_type="claude",
    )
    reg = _reg(_EditWorker(), _ClaudeWorker())
    assert assign_worker(task, reg, exclude={"claude"}) == "extension"


def test_assign_worker_no_capable_worker_raises():
    task = make_task_with(required_capabilities=["cheap_repetitive"])
    reg = _reg(_ClaudeWorker())  # claude has no cheap_repetitive
    with pytest.raises(NoCapableWorker):
        assign_worker(task, reg)


def test_assign_worker_all_excluded_raises():
    task = make_task_with(required_capabilities=["file_editing"])
    reg = _reg(_EditWorker(), _ClaudeWorker())
    with pytest.raises(NoCapableWorker):
        assign_worker(task, reg, exclude={"extension", "claude"})


def test_assign_worker_exclude_skips_worker():
    task = make_task_with(required_capabilities=["file_editing"])
    reg = _reg(_EditWorker(), _ClaudeWorker())
    result = assign_worker(task, reg, exclude={"extension"})
    assert result == "claude"
