import time
from backend.orchestrator.domain.models import (
    Mission, MissionStatus,
    Plan, PlanStatus,
    Run, RunMode, RunStatus,
    Task, TaskStatus,
    TaskAttempt, AttemptStatus,
    Dependency, DependencyType,
    Artifact,
    Event,
)


def test_mission_new_sets_defaults():
    m = Mission.new(title="Fix auth", goal="Make login work")
    assert m.id
    assert m.title == "Fix auth"
    assert m.goal == "Make login work"
    assert m.status == MissionStatus.active
    assert m.context_refs == []
    assert m.global_constraints == []
    assert m.preferences == {}
    assert m.created_at > 0
    assert m.created_at == m.updated_at


def test_plan_new_sets_defaults():
    p = Plan.new(mission_id="m1")
    assert p.id
    assert p.mission_id == "m1"
    assert p.version == 1
    assert p.status == PlanStatus.draft
    assert p.task_graph_shape == "linear"


def test_run_new_defaults_to_paused():
    r = Run.new(mission_id="m1", plan_id="p1", mode=RunMode.plan_first)
    assert r.status == RunStatus.paused
    assert r.mode == RunMode.plan_first


def test_task_new_defaults():
    t = Task.new(run_id="r1", title="Write tests", goal="Cover the service layer")
    assert t.id
    assert t.run_id == "r1"
    assert t.status == TaskStatus.pending
    assert t.escalation_count == 0
    assert t.dependencies == []
    assert t.constraints == []
    assert t.scope == []


def test_task_attempt_new_defaults():
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    assert a.id
    assert a.task_id == "t1"
    assert a.worker_id == "extension"
    assert a.status == AttemptStatus.assigned
    assert a.error_code == ""
    assert a.blocking_reason == ""
    assert a.started_at is None
    assert a.ended_at is None
    assert a.artifact_refs == []


def test_dependency_roundtrips_to_dict():
    dep = Dependency(task_id="t1", type=DependencyType.completion)
    assert Dependency.from_dict(dep.to_dict()) == dep


def test_artifact_new():
    art = Artifact.new(run_id="r1", task_id="t1", attempt_id="a1",
                       type="file", location={"path": "/tmp/out.py"})
    assert art.id
    assert art.type == "file"
    assert art.location == {"path": "/tmp/out.py"}
    assert art.created_at > 0


def test_event_new():
    e = Event.new(run_id="r1", type="task.created", payload={"title": "x"}, task_id="t1")
    assert e.id
    assert e.type == "task.created"
    assert e.payload == {"title": "x"}
    assert e.task_id == "t1"
    assert e.attempt_id is None
