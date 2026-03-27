import time
from orchestrator.core.models import Task, WorkerResult, Event


def test_task_defaults():
    t = Task(id="t1", title="Add test", goal="Add a test for foo()", task_type="code")
    assert t.status == "pending"
    assert t.priority == "normal"
    assert t.parent_id is None
    assert t.assigned_worker is None
    assert t.escalation_count == 0
    assert t.context == {}
    assert t.constraints == []
    assert t.artifacts == []
    assert t.validator_profile == []


def test_task_with_all_fields():
    t = Task(
        id="t2",
        title="Refactor auth",
        goal="Refactor the auth module",
        task_type="refactor",
        priority="high",
        status="running",
        parent_id="p1",
        assigned_worker="extension",
        context={"workspace": "/tmp/proj"},
        constraints=["no breaking changes"],
        artifacts=[{"path": "auth.py", "diff": "..."}],
        validator_profile=["lint", "tests"],
        escalation_count=1,
    )
    assert t.parent_id == "p1"
    assert t.priority == "high"
    assert t.context["workspace"] == "/tmp/proj"
    assert t.escalation_count == 1


def test_worker_result_defaults():
    r = WorkerResult(task_id="t1", worker_id="extension", status="completed", summary="Done")
    assert r.artifacts == []
    assert r.validator_results == []


def test_event_fields():
    ts = time.time()
    e = Event(event_type="task.created", task_id="t1", ts=ts)
    assert e.worker_id is None
    assert e.content == {}
    assert e.ts == ts


def test_event_with_worker():
    e = Event(
        event_type="task.assigned",
        task_id="t1",
        worker_id="extension",
        content={"reason": "bounded task"},
        ts=1.0,
    )
    assert e.worker_id == "extension"
    assert e.content["reason"] == "bounded task"
