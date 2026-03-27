import pytest
from backend.orchestrator_svc.task_store import TaskStore
from backend.orchestrator_svc.models import Task, WorkerResult, Event


@pytest.fixture
async def store(tmp_path):
    s = TaskStore(db_path=tmp_path / "test.db")
    await s.connect()
    yield s
    await s.close()


async def test_save_and_get_task(store):
    task = Task.new(title="Fix login", goal="Fix the login bug", task_type="code")
    await store.save_task(task)
    fetched = await store.get_task(task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.title == "Fix login"
    assert fetched.status == "pending"
    assert fetched.context == {}
    assert fetched.artifacts == []


async def test_get_nonexistent_task_returns_none(store):
    assert await store.get_task("nonexistent") is None


async def test_update_task_status(store):
    task = Task.new(title="Fix login", goal="Fix the login bug", task_type="code")
    await store.save_task(task)
    await store.update_task_status(task.id, "running", assigned_worker="extension")
    fetched = await store.get_task(task.id)
    assert fetched.status == "running"
    assert fetched.assigned_worker == "extension"


async def test_increment_escalation(store):
    task = Task.new(title="Fix bug", goal="Fix the bug", task_type="code")
    await store.save_task(task)
    await store.increment_escalation(task.id)
    await store.increment_escalation(task.id)
    fetched = await store.get_task(task.id)
    assert fetched.escalation_count == 2


async def test_log_and_get_events(store):
    task = Task.new(title="Fix login", goal="Fix the login bug", task_type="code")
    await store.save_task(task)
    await store.log_event(Event(type="task.created", task_id=task.id, content={"title": task.title}))
    await store.log_event(Event(type="task.assigned", task_id=task.id, worker_id="extension"))
    events = await store.get_events(task.id)
    assert len(events) == 2
    assert events[0].type == "task.created"
    assert events[1].type == "task.assigned"
    assert events[1].worker_id == "extension"


async def test_save_and_get_result(store):
    task = Task.new(title="Fix login", goal="Fix the login bug", task_type="code")
    await store.save_task(task)
    result = WorkerResult(task_id=task.id, worker_id="extension", status="completed", summary="Fixed it")
    await store.save_result(result)
    fetched = await store.get_result(task.id)
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.summary == "Fixed it"
    assert fetched.artifacts == []


async def test_list_tasks_filtered_by_status(store):
    t1 = Task.new(title="T1", goal="Add test for login", task_type="code")
    t2 = Task.new(title="T2", goal="Plan the refactor", task_type="plan")
    await store.save_task(t1)
    await store.save_task(t2)
    await store.update_task_status(t2.id, "running")
    pending = await store.list_tasks(status="pending")
    assert any(t.id == t1.id for t in pending)
    assert not any(t.id == t2.id for t in pending)


async def test_state_survives_reconnect(tmp_path):
    s1 = TaskStore(db_path=tmp_path / "persist.db")
    await s1.connect()
    task = Task.new(title="Fix login", goal="Fix the login bug", task_type="code")
    await s1.save_task(task)
    await s1.close()

    s2 = TaskStore(db_path=tmp_path / "persist.db")
    await s2.connect()
    fetched = await s2.get_task(task.id)
    await s2.close()
    assert fetched is not None
    assert fetched.title == "Fix login"
