"""Wave-based run executor — drives an entire Run to completion."""
from __future__ import annotations
import asyncio

from ..domain import dependencies, events as ev
from ..domain.models import RunStatus, TaskStatus
from ..domain.transitions import transition_task
from ..store.base import Store
from ..workers.registry import WorkerRegistry
from .executor import run_task

_TERMINAL_TASK = frozenset({
    TaskStatus.completed, TaskStatus.failed,
    TaskStatus.blocked, TaskStatus.cancelled,
})


async def run_run(run_id: str, store: Store, registry: WorkerRegistry) -> RunStatus:
    """Drive an entire Run from paused → terminal using wave execution.

    Seeds all pending tasks with no unmet dependencies to ready, then
    dispatches waves of ready tasks concurrently via asyncio.gather.
    Loops until all tasks are terminal or no progress is possible.
    """
    run = await store.missions.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id!r} not found")

    await store.missions.update_run_status(run_id, RunStatus.active)
    await store.events.append(ev.make_event(run_id, ev.RUN_STARTED))

    await _seed_ready(run_id, store)

    dispatched: set[str] = set()

    while True:
        tasks = await store.tasks.list_by_run(run_id)

        if not tasks or all(t.status in _TERMINAL_TASK for t in tasks):
            break

        ready = [t for t in tasks if t.status == TaskStatus.ready and t.id not in dispatched]

        if ready:
            for t in ready:
                dispatched.add(t.id)
            await asyncio.gather(*[run_task(t.id, store, registry) for t in ready])
        elif not any(t.status == TaskStatus.in_progress for t in tasks):
            # No ready tasks, nothing running — stuck on blocked/pending with unresolvable deps
            break
        else:
            await asyncio.sleep(0.1)

    tasks = await store.tasks.list_by_run(run_id)
    all_completed = not tasks or all(t.status == TaskStatus.completed for t in tasks)
    final = RunStatus.completed if all_completed else RunStatus.failed

    await store.missions.update_run_status(run_id, final)
    if final == RunStatus.completed:
        await store.events.append(ev.make_event(run_id, ev.RUN_COMPLETED))

    return final


async def _seed_ready(run_id: str, store: Store) -> None:
    """Transition all pending tasks with satisfied dependencies to ready."""
    tasks = await store.tasks.list_by_run(run_id)
    artifacts = await store.artifacts.list_by_run(run_id)
    artifact_task_ids = {a.task_id for a in artifacts}
    approval_events = await store.events.list_by_type(run_id, ev.APPROVAL_GRANTED)
    approval_task_ids = {e.task_id for e in approval_events if e.task_id}

    newly_ready = dependencies.check_ready(tasks, artifact_task_ids, approval_task_ids)
    for task in newly_ready:
        task = transition_task(task, TaskStatus.ready)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(ev.make_event(run_id, ev.TASK_READY, task_id=task.id))
