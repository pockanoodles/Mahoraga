import dataclasses
import pytest
from backend.orchestrator.domain.models import (
    Task, TaskStatus, Dependency, DependencyType,
)
from backend.orchestrator.domain.dependencies import (
    check_ready, detect_cycles, CycleError,
)


def make_task(id: str, status: TaskStatus = TaskStatus.pending,
              deps: list[Dependency] | None = None) -> Task:
    t = Task.new(run_id="r1", title=id, goal="G")
    return dataclasses.replace(t, id=id, status=status, dependencies=deps or [])


# ── check_ready ────────────────────────────────────────────────────────────

def test_task_with_no_deps_is_ready():
    t = make_task("t1", status=TaskStatus.pending)
    ready = check_ready([t], artifact_task_ids=set(), approval_task_ids=set())
    assert len(ready) == 1
    assert ready[0].id == "t1"


def test_task_with_unsatisfied_completion_dep_is_not_ready():
    dep = Dependency(task_id="t0", type=DependencyType.completion)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.in_progress)
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids=set())
    assert ready == []


def test_task_with_satisfied_completion_dep_is_ready():
    dep = Dependency(task_id="t0", type=DependencyType.completion)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.completed)
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids=set())
    assert len(ready) == 1
    assert ready[0].id == "t1"


def test_task_with_artifact_dep_satisfied():
    dep = Dependency(task_id="t0", type=DependencyType.artifact)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.completed)
    ready = check_ready([t0, t1], artifact_task_ids={"t0"}, approval_task_ids=set())
    assert len(ready) == 1


def test_task_with_artifact_dep_not_satisfied_even_if_upstream_completed():
    dep = Dependency(task_id="t0", type=DependencyType.artifact)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.completed)
    # t0 completed but produced no artifact
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids=set())
    assert ready == []


def test_task_with_approval_dep_satisfied():
    dep = Dependency(task_id="t0", type=DependencyType.approval)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.completed)
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids={"t0"})
    assert len(ready) == 1


def test_task_with_mixed_deps_all_must_be_satisfied():
    dep_c = Dependency(task_id="t0", type=DependencyType.completion)
    dep_a = Dependency(task_id="t0", type=DependencyType.artifact)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep_c, dep_a])
    t0 = make_task("t0", status=TaskStatus.completed)
    # completion satisfied but artifact missing
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids=set())
    assert ready == []
    # both satisfied
    ready = check_ready([t0, t1], artifact_task_ids={"t0"}, approval_task_ids=set())
    assert len(ready) == 1


def test_only_pending_tasks_returned():
    t1 = make_task("t1", status=TaskStatus.ready)
    t2 = make_task("t2", status=TaskStatus.in_progress)
    t3 = make_task("t3", status=TaskStatus.pending)
    ready = check_ready([t1, t2, t3], artifact_task_ids=set(), approval_task_ids=set())
    assert len(ready) == 1
    assert ready[0].id == "t3"


def test_upstream_retry_does_not_unlock_downstream():
    """A downstream pending task with completion dep must not be returned as ready
    when the upstream task re-enters pending (e.g. human retry) after having been
    completed before. This test documents that check_ready is purely state-based:
    it only looks at current status == completed."""
    dep = Dependency(task_id="t0", type=DependencyType.completion)
    t1 = make_task("t1", status=TaskStatus.pending, deps=[dep])
    t0 = make_task("t0", status=TaskStatus.pending)  # re-opened upstream
    ready = check_ready([t0, t1], artifact_task_ids=set(), approval_task_ids=set())
    # t0 is pending again, t1 must not be unlocked
    assert "t1" not in [r.id for r in ready]


# ── detect_cycles ──────────────────────────────────────────────────────────

def test_no_cycle_linear_chain():
    dep = Dependency(task_id="t1", type=DependencyType.completion)
    t1 = make_task("t1")
    t2 = make_task("t2", deps=[dep])
    detect_cycles([t1, t2])  # must not raise


def test_no_cycle_independent_tasks():
    t1 = make_task("t1")
    t2 = make_task("t2")
    detect_cycles([t1, t2])  # must not raise


def test_direct_cycle_raises():
    # t1 → t2 and t2 → t1
    t1 = make_task("t1", deps=[Dependency(task_id="t2", type=DependencyType.completion)])
    t2 = make_task("t2", deps=[Dependency(task_id="t1", type=DependencyType.completion)])
    with pytest.raises(CycleError):
        detect_cycles([t1, t2])


def test_indirect_cycle_raises():
    # t1 → t2 → t3 → t1
    t1 = make_task("t1", deps=[Dependency(task_id="t2", type=DependencyType.completion)])
    t2 = make_task("t2", deps=[Dependency(task_id="t3", type=DependencyType.completion)])
    t3 = make_task("t3", deps=[Dependency(task_id="t1", type=DependencyType.completion)])
    with pytest.raises(CycleError):
        detect_cycles([t1, t2, t3])


def test_self_loop_raises():
    t1 = make_task("t1", deps=[Dependency(task_id="t1", type=DependencyType.completion)])
    with pytest.raises(CycleError):
        detect_cycles([t1])


def test_deps_pointing_outside_graph_are_ignored():
    # External dep ref (not in task list) should not cause crash
    dep = Dependency(task_id="external-id", type=DependencyType.completion)
    t1 = make_task("t1", deps=[dep])
    detect_cycles([t1])  # must not raise
