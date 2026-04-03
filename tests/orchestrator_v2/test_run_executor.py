import pytest
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import (
    Mission, Plan, Run, RunMode, RunStatus,
    Task, TaskStatus, Dependency, DependencyType,
)
from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.service.run_executor import run_run
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult


def _pass_verifier() -> Verifier:
    result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v


_PASS_VERIFIER = _pass_verifier()


class MockWorker(WorkerAdapter):
    def __init__(self, worker_id: str, capabilities: list[str], events: list[WorkerEvent]):
        self._id = worker_id
        self._caps = capabilities
        self._events = events

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._caps

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        for ev in self._events:
            yield ev

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


async def _make_run(store: Store) -> tuple[Mission, Plan, Run]:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    return m, p, r


def _ok_worker() -> WorkerRegistry:
    reg = WorkerRegistry()
    reg.register(MockWorker("w", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ]))
    return reg


def _fail_worker() -> WorkerRegistry:
    reg = WorkerRegistry()
    reg.register(MockWorker("w", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "err", "error": "bad"}),
    ]))
    return reg


async def test_run_run_single_task_completed(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    final = await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    assert final == RunStatus.completed
    t = await store.tasks.get(task.id)
    assert t.status == TaskStatus.completed


async def test_run_run_updates_run_status_to_completed(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    updated_run = await store.missions.get_run(r.id)
    assert updated_run.status == RunStatus.completed


async def test_run_run_emits_run_started_event(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    events = await store.events.list_by_run(r.id)
    assert any(e.type == ev_types.RUN_STARTED for e in events)


async def test_run_run_emits_run_completed_event(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    events = await store.events.list_by_run(r.id)
    assert any(e.type == ev_types.RUN_COMPLETED for e in events)


async def test_run_run_linear_chain(store):
    """A → B: B starts only after A completes."""
    _, _, r = await _make_run(store)

    task_a = Task.new(run_id=r.id, title="A", goal="G", required_capabilities=["file_editing"])
    task_b = Task.new(
        run_id=r.id, title="B", goal="G",
        required_capabilities=["file_editing"],
        dependencies=[Dependency(task_id=task_a.id, type=DependencyType.completion)],
    )
    await store.tasks.save(task_a)
    await store.tasks.save(task_b)

    final = await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    assert final == RunStatus.completed
    a = await store.tasks.get(task_a.id)
    b = await store.tasks.get(task_b.id)
    assert a.status == TaskStatus.completed
    assert b.status == TaskStatus.completed


async def test_run_run_parallel_tasks(store):
    """Two independent tasks both complete."""
    _, _, r = await _make_run(store)

    task_a = Task.new(run_id=r.id, title="A", goal="G", required_capabilities=["file_editing"])
    task_b = Task.new(run_id=r.id, title="B", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task_a)
    await store.tasks.save(task_b)

    final = await run_run(r.id, store, _ok_worker(), _PASS_VERIFIER)

    assert final == RunStatus.completed
    a = await store.tasks.get(task_a.id)
    b = await store.tasks.get(task_b.id)
    assert a.status == TaskStatus.completed
    assert b.status == TaskStatus.completed


async def test_run_run_returns_failed_when_task_fails(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    final = await run_run(r.id, store, _fail_worker(), _PASS_VERIFIER)

    assert final == RunStatus.failed
    updated_run = await store.missions.get_run(r.id)
    assert updated_run.status == RunStatus.failed


async def test_run_run_no_run_completed_event_on_failure(store):
    _, _, r = await _make_run(store)
    task = Task.new(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    await store.tasks.save(task)

    await run_run(r.id, store, _fail_worker(), _PASS_VERIFIER)

    events = await store.events.list_by_run(r.id)
    assert not any(e.type == ev_types.RUN_COMPLETED for e in events)
    assert any(e.type == ev_types.RUN_FAILED for e in events)


async def test_run_run_raises_on_unknown_run_id(store):
    reg = WorkerRegistry()
    with pytest.raises(ValueError, match="not found"):
        await run_run("nonexistent", store, reg, _PASS_VERIFIER)


async def test_run_run_empty_task_list(store):
    """Run with no tasks completes immediately."""
    _, _, r = await _make_run(store)

    final = await run_run(r.id, store, WorkerRegistry(), _PASS_VERIFIER)

    assert final == RunStatus.completed
