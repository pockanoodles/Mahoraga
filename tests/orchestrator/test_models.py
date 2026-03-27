# tests/orchestrator/test_models.py
import time
from backend.orchestrator_svc.models import Task, WorkerResult, Event


def test_task_new_generates_uuid():
    task = Task.new(title="Fix bug", goal="Fix the login bug", task_type="code")
    assert len(task.id) == 36
    assert task.status == "pending"
    assert task.escalation_count == 0
    assert task.priority == "normal"
    assert task.parent_id is None
    assert isinstance(task.created_at, float)


def test_task_new_accepts_optional_fields():
    task = Task.new(
        title="Plan refactor",
        goal="Plan auth module refactor",
        task_type="plan",
        priority="high",
        parent_id="parent-123",
    )
    assert task.priority == "high"
    assert task.parent_id == "parent-123"


def test_task_mutable_defaults_are_not_shared():
    t1 = Task.new(title="T1", goal="G1", task_type="code")
    t2 = Task.new(title="T2", goal="G2", task_type="code")
    t1.artifacts.append({"file": "app.py"})
    assert t2.artifacts == []


def test_worker_result_defaults():
    result = WorkerResult(
        task_id="t1", worker_id="extension", status="completed", summary="Fixed it"
    )
    assert result.artifacts == []
    assert result.validator_results == []
    assert isinstance(result.created_at, float)


def test_event_defaults():
    event = Event(type="task.created", task_id="t1")
    assert event.worker_id is None
    assert event.content == {}
    assert isinstance(event.ts, float)
