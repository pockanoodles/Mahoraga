from __future__ import annotations
from .models import Event

# Mission lifecycle
MISSION_CREATED = "mission.created"

# Plan lifecycle
PLAN_CREATED = "plan.created"
PLAN_APPROVED = "plan.approved"

# Run lifecycle
RUN_STARTED = "run.started"
RUN_PAUSED = "run.paused"
RUN_CANCELLED = "run.cancelled"
RUN_COMPLETED = "run.completed"

# Task lifecycle
TASK_CREATED = "task.created"
TASK_READY = "task.ready"
TASK_ASSIGNED = "task.assigned"
TASK_BLOCKED = "task.blocked"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"

# Attempt lifecycle
ATTEMPT_ASSIGNED = "attempt.assigned"
ATTEMPT_STARTED = "attempt.started"
ATTEMPT_COMPLETED = "attempt.completed"
ATTEMPT_FAILED = "attempt.failed"
ATTEMPT_ESCALATED = "attempt.escalated"
ATTEMPT_CANCELLED = "attempt.cancelled"

# Human control
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_GRANTED = "approval.granted"
APPROVAL_REJECTED = "approval.rejected"

# Artifacts
ARTIFACT_CREATED = "artifact.created"

ALL_EVENT_TYPES = frozenset({
    MISSION_CREATED,
    PLAN_CREATED, PLAN_APPROVED,
    RUN_STARTED, RUN_PAUSED, RUN_CANCELLED, RUN_COMPLETED,
    TASK_CREATED, TASK_READY, TASK_ASSIGNED, TASK_BLOCKED,
    TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED,
    ATTEMPT_ASSIGNED, ATTEMPT_STARTED, ATTEMPT_COMPLETED,
    ATTEMPT_FAILED, ATTEMPT_ESCALATED, ATTEMPT_CANCELLED,
    APPROVAL_REQUESTED, APPROVAL_GRANTED, APPROVAL_REJECTED,
    ARTIFACT_CREATED,
})


def make_event(
    run_id: str,
    type: str,
    payload: dict | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> Event:
    """Validate event type and return a new Event."""
    if type not in ALL_EVENT_TYPES:
        raise ValueError(f"Unknown event type: {type!r}")
    return Event.new(
        run_id=run_id, type=type, payload=payload,
        task_id=task_id, attempt_id=attempt_id,
    )
