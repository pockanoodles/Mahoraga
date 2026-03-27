from __future__ import annotations
from ..domain import events as ev_types
from ..domain.models import TaskStatus
from ..domain.transitions import transition_task
from ..store.base import Store


async def request_approval(
    run_id: str, task_id: str, attempt_id: str, store: Store
) -> None:
    """Record approval.requested event. Task must already be blocked."""
    event = ev_types.make_event(
        run_id, ev_types.APPROVAL_REQUESTED,
        task_id=task_id, attempt_id=attempt_id,
    )
    await store.events.append(event)


async def grant_approval(run_id: str, task_id: str, store: Store) -> None:
    """Record approval.granted and transition task blocked → ready."""
    task = await store.tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id!r} not found")
    task = transition_task(task, TaskStatus.ready)
    await store.tasks.update_status(task.id, task.status)
    event = ev_types.make_event(run_id, ev_types.APPROVAL_GRANTED, task_id=task_id)
    await store.events.append(event)


async def reject_approval(run_id: str, task_id: str, store: Store) -> None:
    """Record approval.rejected and transition task blocked → failed."""
    task = await store.tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id!r} not found")
    task = transition_task(task, TaskStatus.failed)
    await store.tasks.update_status(task.id, task.status)
    event = ev_types.make_event(run_id, ev_types.APPROVAL_REJECTED, task_id=task_id)
    await store.events.append(event)
