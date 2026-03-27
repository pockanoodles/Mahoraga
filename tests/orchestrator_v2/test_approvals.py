import dataclasses
import pytest
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import Task, TaskStatus, Run, RunMode, Mission
from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.service.approvals import (
    request_approval,
    grant_approval,
    reject_approval,
)


async def _make_run(store: Store) -> tuple[Mission, Run]:
    m = Mission.new(title="M", goal="G")
    from backend.orchestrator.domain.models import Plan
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    return m, r


async def _blocked_task(store: Store, run_id: str) -> Task:
    task = Task.new(run_id=run_id, title="T", goal="G")
    task = dataclasses.replace(task, status=TaskStatus.blocked)
    await store.tasks.save(task)
    return task


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


async def test_request_approval_records_event(store):
    _, run = await _make_run(store)
    task = await _blocked_task(store, run.id)
    attempt_id = "attempt-1"
    await request_approval(run.id, task.id, attempt_id, store)
    events = await store.events.list_by_task(task.id)
    assert any(e.type == ev_types.APPROVAL_REQUESTED for e in events)


async def test_grant_approval_transitions_task_to_ready(store):
    _, run = await _make_run(store)
    task = await _blocked_task(store, run.id)
    await grant_approval(run.id, task.id, store)
    updated = await store.tasks.get(task.id)
    assert updated.status == TaskStatus.ready


async def test_grant_approval_records_granted_event(store):
    _, run = await _make_run(store)
    task = await _blocked_task(store, run.id)
    await grant_approval(run.id, task.id, store)
    events = await store.events.list_by_task(task.id)
    assert any(e.type == ev_types.APPROVAL_GRANTED for e in events)


async def test_reject_approval_transitions_task_to_failed(store):
    _, run = await _make_run(store)
    task = await _blocked_task(store, run.id)
    await reject_approval(run.id, task.id, store)
    updated = await store.tasks.get(task.id)
    assert updated.status == TaskStatus.failed


async def test_reject_approval_records_rejected_event(store):
    _, run = await _make_run(store)
    task = await _blocked_task(store, run.id)
    await reject_approval(run.id, task.id, store)
    events = await store.events.list_by_task(task.id)
    assert any(e.type == ev_types.APPROVAL_REJECTED for e in events)


async def test_grant_approval_task_not_found_raises(store):
    with pytest.raises(ValueError, match="not found"):
        await grant_approval("run-x", "nonexistent-task-id", store)


async def test_reject_approval_task_not_found_raises(store):
    with pytest.raises(ValueError, match="not found"):
        await reject_approval("run-x", "nonexistent-task-id", store)
