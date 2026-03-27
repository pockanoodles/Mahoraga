import dataclasses
import pytest
from backend.orchestrator.domain.models import (
    Task, TaskStatus, TaskAttempt, AttemptStatus,
    Dependency, DependencyType,
)


async def test_save_and_get_task(store):
    t = Task.new(run_id="r1", title="Write service layer", goal="Implement TaskStore")
    await store.tasks.save(t)
    fetched = await store.tasks.get(t.id)
    assert fetched is not None
    assert fetched.id == t.id
    assert fetched.title == "Write service layer"
    assert fetched.status == TaskStatus.pending
    assert fetched.escalation_count == 0
    assert fetched.dependencies == []
    assert fetched.scope == []


async def test_get_missing_task_returns_none(store):
    assert await store.tasks.get("no-such-id") is None


async def test_save_task_with_dependencies(store):
    dep = Dependency(task_id="upstream-1", type=DependencyType.completion)
    t = Task.new(run_id="r1", title="T", goal="G", dependencies=[dep])
    await store.tasks.save(t)
    fetched = await store.tasks.get(t.id)
    assert len(fetched.dependencies) == 1
    assert fetched.dependencies[0].task_id == "upstream-1"
    assert fetched.dependencies[0].type == DependencyType.completion


async def test_save_task_with_all_fields(store):
    t = Task.new(
        run_id="r1", title="Impl", goal="Build it",
        scope=["src/auth/"],
        context_refs=["docs/spec.md"],
        done_criteria="All tests pass",
        constraints=["planning_only"],
        preferred_worker_type="claude",
        required_capabilities=["deep_reasoning"],
    )
    await store.tasks.save(t)
    fetched = await store.tasks.get(t.id)
    assert fetched.scope == ["src/auth/"]
    assert fetched.context_refs == ["docs/spec.md"]
    assert fetched.done_criteria == "All tests pass"
    assert fetched.constraints == ["planning_only"]
    assert fetched.preferred_worker_type == "claude"
    assert fetched.required_capabilities == ["deep_reasoning"]


async def test_update_task_status(store):
    t = Task.new(run_id="r1", title="T", goal="G")
    await store.tasks.save(t)
    await store.tasks.update_status(t.id, TaskStatus.ready)
    fetched = await store.tasks.get(t.id)
    assert fetched.status == TaskStatus.ready


async def test_increment_escalation(store):
    t = Task.new(run_id="r1", title="T", goal="G")
    await store.tasks.save(t)
    await store.tasks.increment_escalation(t.id)
    fetched = await store.tasks.get(t.id)
    assert fetched.escalation_count == 1
    await store.tasks.increment_escalation(t.id)
    fetched = await store.tasks.get(t.id)
    assert fetched.escalation_count == 2


async def test_list_tasks_by_run(store):
    t1 = Task.new(run_id="r1", title="A", goal="G")
    t2 = Task.new(run_id="r1", title="B", goal="G")
    t3 = Task.new(run_id="r2", title="C", goal="G")
    for t in [t1, t2, t3]:
        await store.tasks.save(t)
    results = await store.tasks.list_by_run("r1")
    ids = {r.id for r in results}
    assert t1.id in ids
    assert t2.id in ids
    assert t3.id not in ids


async def test_list_tasks_by_status(store):
    t1 = Task.new(run_id="r1", title="A", goal="G")
    t2 = dataclasses.replace(Task.new(run_id="r1", title="B", goal="G"),
                              status=TaskStatus.ready)
    await store.tasks.save(t1)
    await store.tasks.save(t2)
    pending = await store.tasks.list_by_status(TaskStatus.pending)
    assert any(r.id == t1.id for r in pending)
    assert all(r.id != t2.id for r in pending)


async def test_save_and_get_attempt(store):
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    await store.tasks.save_attempt(a)
    fetched = await store.tasks.get_attempt(a.id)
    assert fetched is not None
    assert fetched.id == a.id
    assert fetched.worker_id == "extension"
    assert fetched.status == AttemptStatus.assigned
    assert fetched.started_at is None
    assert fetched.ended_at is None


async def test_update_attempt_status(store):
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    await store.tasks.save_attempt(a)
    await store.tasks.update_attempt_status(a.id, AttemptStatus.running)
    fetched = await store.tasks.get_attempt(a.id)
    assert fetched.status == AttemptStatus.running


async def test_update_attempt_result(store):
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    await store.tasks.save_attempt(a)
    await store.tasks.update_attempt_result(
        a.id,
        status=AttemptStatus.completed,
        summary="All 12 tests passed",
        artifact_refs=["art-1"],
    )
    fetched = await store.tasks.get_attempt(a.id)
    assert fetched.status == AttemptStatus.completed
    assert fetched.summary == "All 12 tests passed"
    assert fetched.artifact_refs == ["art-1"]
    assert fetched.ended_at is not None


async def test_update_attempt_failure(store):
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    await store.tasks.save_attempt(a)
    await store.tasks.update_attempt_result(
        a.id,
        status=AttemptStatus.failed,
        summary="",
        error_code="timeout",
        blocking_reason="Worker did not respond within 120s",
    )
    fetched = await store.tasks.get_attempt(a.id)
    assert fetched.error_code == "timeout"
    assert fetched.blocking_reason == "Worker did not respond within 120s"


async def test_list_attempts_for_task(store):
    a1 = TaskAttempt.new(task_id="t1", worker_id="extension")
    a2 = TaskAttempt.new(task_id="t1", worker_id="claude")
    a3 = TaskAttempt.new(task_id="t2", worker_id="extension")
    for a in [a1, a2, a3]:
        await store.tasks.save_attempt(a)
    results = await store.tasks.list_attempts("t1")
    ids = {r.id for r in results}
    assert a1.id in ids
    assert a2.id in ids
    assert a3.id not in ids
