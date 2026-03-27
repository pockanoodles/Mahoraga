import dataclasses
import pytest
from backend.orchestrator.domain.models import (
    Task, TaskStatus, TaskAttempt, AttemptStatus,
)
from backend.orchestrator.domain.transitions import (
    transition_task, transition_attempt,
    can_escalate, verify_done_criteria,
    IllegalTransition,
)


# ── helpers ────────────────────────────────────────────────────────────────

def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="T", goal="G")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(**kwargs) -> TaskAttempt:
    a = TaskAttempt.new(task_id="t1", worker_id="extension")
    return dataclasses.replace(a, **kwargs) if kwargs else a


# ── task transitions ────────────────────────────────────────────────────────

def test_pending_to_ready_is_legal():
    t = make_task(status=TaskStatus.pending)
    result = transition_task(t, TaskStatus.ready)
    assert result.status == TaskStatus.ready
    assert result.updated_at >= t.updated_at


def test_ready_to_in_progress_is_legal():
    t = make_task(status=TaskStatus.ready)
    assert transition_task(t, TaskStatus.in_progress).status == TaskStatus.in_progress


def test_in_progress_to_completed_is_legal():
    t = make_task(status=TaskStatus.in_progress)
    assert transition_task(t, TaskStatus.completed).status == TaskStatus.completed


def test_in_progress_to_blocked_is_legal():
    t = make_task(status=TaskStatus.in_progress)
    assert transition_task(t, TaskStatus.blocked).status == TaskStatus.blocked


def test_in_progress_to_failed_is_legal():
    t = make_task(status=TaskStatus.in_progress)
    assert transition_task(t, TaskStatus.failed).status == TaskStatus.failed


def test_in_progress_to_cancelled_is_legal():
    t = make_task(status=TaskStatus.in_progress)
    assert transition_task(t, TaskStatus.cancelled).status == TaskStatus.cancelled


def test_blocked_to_ready_is_legal():
    t = make_task(status=TaskStatus.blocked)
    assert transition_task(t, TaskStatus.ready).status == TaskStatus.ready


def test_failed_to_ready_is_legal():
    t = make_task(status=TaskStatus.failed)
    assert transition_task(t, TaskStatus.ready).status == TaskStatus.ready


def test_pending_to_completed_raises():
    t = make_task(status=TaskStatus.pending)
    with pytest.raises(IllegalTransition, match="pending"):
        transition_task(t, TaskStatus.completed)


def test_ready_to_completed_raises():
    t = make_task(status=TaskStatus.ready)
    with pytest.raises(IllegalTransition):
        transition_task(t, TaskStatus.completed)


def test_completed_is_terminal():
    t = make_task(status=TaskStatus.completed)
    with pytest.raises(IllegalTransition):
        transition_task(t, TaskStatus.ready)


def test_cancelled_is_terminal():
    t = make_task(status=TaskStatus.cancelled)
    with pytest.raises(IllegalTransition):
        transition_task(t, TaskStatus.pending)


def test_pending_to_in_progress_raises():
    t = make_task(status=TaskStatus.pending)
    with pytest.raises(IllegalTransition):
        transition_task(t, TaskStatus.in_progress)


# ── attempt transitions ────────────────────────────────────────────────────

def test_assigned_to_running_is_legal():
    a = make_attempt(status=AttemptStatus.assigned)
    assert transition_attempt(a, AttemptStatus.running).status == AttemptStatus.running


def test_running_to_completed_sets_ended_at():
    a = make_attempt(status=AttemptStatus.running)
    result = transition_attempt(a, AttemptStatus.completed)
    assert result.status == AttemptStatus.completed
    assert result.ended_at is not None


def test_running_to_failed_sets_ended_at():
    a = make_attempt(status=AttemptStatus.running)
    result = transition_attempt(a, AttemptStatus.failed)
    assert result.ended_at is not None


def test_running_to_escalated_sets_ended_at():
    a = make_attempt(status=AttemptStatus.running)
    result = transition_attempt(a, AttemptStatus.escalated)
    assert result.ended_at is not None


def test_completed_attempt_is_terminal():
    a = make_attempt(status=AttemptStatus.completed)
    with pytest.raises(IllegalTransition):
        transition_attempt(a, AttemptStatus.running)


def test_assigned_to_failed_raises():
    a = make_attempt(status=AttemptStatus.assigned)
    with pytest.raises(IllegalTransition):
        transition_attempt(a, AttemptStatus.failed)


# ── escalation domain rule ─────────────────────────────────────────────────

def test_can_escalate_when_count_zero():
    t = make_task(escalation_count=0)
    assert can_escalate(t) is True


def test_can_escalate_when_count_one():
    t = make_task(escalation_count=1)
    assert can_escalate(t) is True


def test_cannot_escalate_when_count_two():
    t = make_task(escalation_count=2)
    assert can_escalate(t) is False


def test_cannot_escalate_when_count_exceeds_limit():
    t = make_task(escalation_count=5)
    assert can_escalate(t) is False


# ── done criteria ──────────────────────────────────────────────────────────

def test_verify_done_criteria_passes_with_summary():
    t = make_task(done_criteria="Tests pass")
    assert verify_done_criteria(t, summary="All 12 tests passed") is True


def test_verify_done_criteria_fails_with_empty_summary():
    t = make_task(done_criteria="Tests pass")
    assert verify_done_criteria(t, summary="") is False


def test_verify_done_criteria_no_criteria_passes_with_summary():
    t = make_task(done_criteria="")
    assert verify_done_criteria(t, summary="Done") is True


def test_verify_done_criteria_no_criteria_no_summary_fails():
    t = make_task(done_criteria="")
    assert verify_done_criteria(t, summary="") is False
