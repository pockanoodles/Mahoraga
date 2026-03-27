from __future__ import annotations
import dataclasses
import time
from .models import Task, TaskAttempt, TaskStatus, AttemptStatus

_LEGAL_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.pending:     frozenset({TaskStatus.ready}),
    TaskStatus.ready:       frozenset({TaskStatus.in_progress, TaskStatus.cancelled}),
    TaskStatus.in_progress: frozenset({
        TaskStatus.completed, TaskStatus.blocked,
        TaskStatus.failed, TaskStatus.cancelled,
    }),
    TaskStatus.blocked:     frozenset({TaskStatus.ready, TaskStatus.failed, TaskStatus.cancelled}),
    TaskStatus.failed:      frozenset({TaskStatus.ready, TaskStatus.cancelled}),
    TaskStatus.completed:   frozenset(),
    TaskStatus.cancelled:   frozenset(),
}

_LEGAL_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.assigned: frozenset({AttemptStatus.running, AttemptStatus.cancelled}),
    AttemptStatus.running:  frozenset({
        AttemptStatus.completed, AttemptStatus.failed,
        AttemptStatus.blocked, AttemptStatus.escalated, AttemptStatus.cancelled,
    }),
    AttemptStatus.completed:  frozenset(),
    AttemptStatus.failed:     frozenset(),
    AttemptStatus.blocked:    frozenset(),
    AttemptStatus.escalated:  frozenset(),
    AttemptStatus.cancelled:  frozenset(),
}

ESCALATION_LIMIT = 2

_ATTEMPT_TERMINAL = frozenset(
    s for s, targets in _LEGAL_ATTEMPT_TRANSITIONS.items() if not targets
)


class IllegalTransition(ValueError):
    pass


def transition_task(task: Task, new_status: TaskStatus) -> Task:
    """Return a new Task with updated status. Raises IllegalTransition if not legal."""
    allowed = _LEGAL_TASK_TRANSITIONS.get(task.status, frozenset())
    if new_status not in allowed:
        raise IllegalTransition(
            f"Task {task.id}: {task.status.value} → {new_status.value} is not a legal transition"
        )
    return dataclasses.replace(task, status=new_status, updated_at=time.time())


def transition_attempt(attempt: TaskAttempt, new_status: AttemptStatus) -> TaskAttempt:
    """Return a new TaskAttempt with updated status. Raises IllegalTransition if not legal."""
    allowed = _LEGAL_ATTEMPT_TRANSITIONS.get(attempt.status, frozenset())
    if new_status not in allowed:
        raise IllegalTransition(
            f"Attempt {attempt.id}: {attempt.status.value} → {new_status.value} is not a legal transition"
        )
    ended_at = time.time() if new_status in _ATTEMPT_TERMINAL else attempt.ended_at
    return dataclasses.replace(attempt, status=new_status, ended_at=ended_at)


def can_escalate(task: Task) -> bool:
    """Domain rule: can this task be escalated to a different worker?"""
    return task.escalation_count < ESCALATION_LIMIT


def verify_done_criteria(task: Task, summary: str) -> bool:
    """v1: done_criteria is satisfied when the worker provides a non-empty summary.

    v1.5 will introduce structured done_criteria with explicit verification rules.
    """
    return bool(summary)
