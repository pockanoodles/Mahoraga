import pytest
import time
from orchestrator.core.models import Task, Event
from orchestrator.core.state import StateStore


@pytest.fixture
async def store():
    s = StateStore(":memory:")
    await s.init()
    return s


async def test_save_and_get_task(store):
    task = Task(id="t1", title="Fix bug", goal="Fix the login bug", task_type="debug")
    await store.save_task(task)

    fetched = await store.get_task("t1")
    assert fetched is not None
    assert fetched.id == "t1"
    assert fetched.goal == "Fix the login bug"
    assert fetched.task_type == "debug"
    assert fetched.status == "pending"
    assert fetched.escalation_count == 0


async def test_get_task_returns_none_for_unknown(store):
    result = await store.get_task("nonexistent")
    assert result is None


async def test_update_task_status(store):
    task = Task(id="t2", title="Add feature", goal="Add dark mode", task_type="code")
    await store.save_task(task)

    await store.update_task("t2", status="running", assigned_worker="extension")

    fetched = await store.get_task("t2")
    assert fetched.status == "running"
    assert fetched.assigned_worker == "extension"


async def test_update_task_escalation_count(store):
    task = Task(id="t3", title="Debug", goal="Debug crash", task_type="debug")
    await store.save_task(task)

    await store.update_task("t3", escalation_count=2, status="escalated")

    fetched = await store.get_task("t3")
    assert fetched.escalation_count == 2
    assert fetched.status == "escalated"


async def test_log_and_get_events(store):
    task = Task(id="t4", title="Plan", goal="Plan refactor", task_type="plan")
    await store.save_task(task)

    e1 = Event(event_type="task.created", task_id="t4", ts=1.0)
    e2 = Event(event_type="task.assigned", task_id="t4", worker_id="claude", ts=2.0, content={"reason": "planning task"})
    await store.log_event(e1)
    await store.log_event(e2)

    events = await store.get_events("t4")
    assert len(events) == 2
    assert events[0].event_type == "task.created"
    assert events[1].event_type == "task.assigned"
    assert events[1].worker_id == "claude"
    assert events[1].content["reason"] == "planning task"


async def test_get_events_returns_empty_for_unknown(store):
    events = await store.get_events("no-such-task")
    assert events == []


async def test_task_context_and_constraints_round_trip(store):
    task = Task(
        id="t5",
        title="Refactor",
        goal="Refactor auth",
        task_type="refactor",
        context={"workspace": "/tmp/proj", "branch": "main"},
        constraints=["no breaking changes", "keep public API"],
        validator_profile=["lint", "tests"],
    )
    await store.save_task(task)

    fetched = await store.get_task("t5")
    assert fetched.context == {"workspace": "/tmp/proj", "branch": "main"}
    assert fetched.constraints == ["no breaking changes", "keep public API"]
    assert fetched.validator_profile == ["lint", "tests"]
