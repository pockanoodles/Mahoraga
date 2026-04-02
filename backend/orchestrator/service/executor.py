"""Lobster-style deterministic executor for driving tasks through their lifecycle."""
from __future__ import annotations
import dataclasses

from ..domain import events as ev_types
from ..domain import dependencies
from ..domain.models import Artifact, Task, TaskAttempt, TaskStatus, AttemptStatus
from ..domain.transitions import transition_task, verify_done_criteria
from ..store.base import Store
from ..workers.base import WorkerEvent
from ..workers.registry import WorkerRegistry
from ..routing.router import assign_worker, NoCapableWorker
from ..routing.escalation import should_escalate
from . import approvals

# Terminal WorkerEvent types the executor acts on
_TERMINAL = frozenset({"attempt.completed", "attempt.failed", "attempt.blocked"})


async def run_task(task_id: str, store: Store, registry: WorkerRegistry) -> None:
    """Drive one task from ready → terminal using a Lobster-style deterministic loop.

    Steps per attempt: assign → dispatch → stream → verify → escalate/complete/block
    All state decisions delegate to domain layer. No business logic here.
    """
    task = await store.tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id!r} not found")

    attempted: set[str] = set()

    while True:
        # ── ASSIGN ──────────────────────────────────────────────────────────
        try:
            worker_id = assign_worker(task, registry, exclude=attempted)
        except NoCapableWorker:
            # ready → in_progress → blocked (direct ready→blocked is not a legal transition)
            if task.status == TaskStatus.ready:
                task = transition_task(task, TaskStatus.in_progress)
                await store.tasks.update_status(task.id, task.status)
            task = transition_task(task, TaskStatus.blocked)
            await store.tasks.update_status(task.id, task.status)
            await store.events.append(
                ev_types.make_event(task.run_id, ev_types.TASK_BLOCKED, task_id=task.id)
            )
            await approvals.request_approval(task.run_id, task.id, "", store)
            return

        attempt = TaskAttempt.new(task_id=task.id, worker_id=worker_id)
        await store.tasks.save_attempt(attempt)
        attempted.add(worker_id)

        # Only transition to in_progress if not already there (e.g. after escalation loop)
        if task.status != TaskStatus.in_progress:
            task = transition_task(task, TaskStatus.in_progress)
            await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(
                task.run_id, ev_types.ATTEMPT_ASSIGNED,
                task_id=task.id, attempt_id=attempt.id,
                payload={"worker_id": worker_id},
            )
        )

        # ── DISPATCH ────────────────────────────────────────────────────────
        upstream = await _collect_upstream_outputs(task, store)
        dispatch_task = dataclasses.replace(task, context_refs=task.context_refs + upstream) if upstream else task

        worker = registry.get(worker_id)
        await store.tasks.update_attempt_status(attempt.id, AttemptStatus.running)
        await store.events.append(
            ev_types.make_event(
                task.run_id, ev_types.ATTEMPT_STARTED,
                task_id=task.id, attempt_id=attempt.id,
            )
        )

        # ── STREAM ──────────────────────────────────────────────────────────
        outcome: WorkerEvent | None = None
        async for w_ev in worker.execute(attempt, dispatch_task):
            if w_ev.type in _TERMINAL:
                outcome = w_ev
                break
            # Forward non-terminal events to log if they're valid event types
            if w_ev.type in ev_types.ALL_EVENT_TYPES:
                await store.events.append(
                    ev_types.make_event(
                        task.run_id, w_ev.type,
                        payload=w_ev.payload,
                        task_id=task.id, attempt_id=attempt.id,
                    )
                )

        if outcome is None:
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_ended", "error": "worker stream ended without terminal event"},
            )

        # ── VERIFY ──────────────────────────────────────────────────────────
        if outcome.type == "attempt.completed":
            summary = outcome.payload.get("summary", "")
            if verify_done_criteria(task, summary):
                await store.tasks.update_attempt_result(
                    attempt.id, AttemptStatus.completed, summary=summary,
                )
                task = transition_task(task, TaskStatus.completed)
                await store.tasks.update_status(task.id, task.status)
                await store.artifacts.save(Artifact.new(
                    run_id=task.run_id, task_id=task.id, attempt_id=attempt.id,
                    type="text_output", location={"content": summary},
                ))
                await store.events.append(
                    ev_types.make_event(task.run_id, ev_types.TASK_COMPLETED, task_id=task.id)
                )
                await _unlock_downstream(task, store)
                return
            # Done criteria not met → treat as failure
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "done_criteria_not_met",
                    "error": f"done_criteria not satisfied. summary={summary!r}",
                },
            )

        # ── ESCALATE or BLOCK ────────────────────────────────────────────────
        error_code = outcome.payload.get("error_code", "")
        blocking_reason = outcome.payload.get("error", outcome.payload.get("reason", ""))

        if outcome.type == "attempt.blocked":
            await store.tasks.update_attempt_result(
                attempt.id, AttemptStatus.blocked,
                summary="", error_code=error_code, blocking_reason=blocking_reason,
            )
            task = transition_task(task, TaskStatus.blocked)
            await store.tasks.update_status(task.id, task.status)
            await store.events.append(
                ev_types.make_event(
                    task.run_id, ev_types.TASK_BLOCKED,
                    task_id=task.id, attempt_id=attempt.id,
                )
            )
            await approvals.request_approval(task.run_id, task.id, attempt.id, store)
            return

        # attempt.failed — try escalation
        escalating = should_escalate(task, registry, attempted)
        final_attempt_status = AttemptStatus.escalated if escalating else AttemptStatus.failed
        await store.tasks.update_attempt_result(
            attempt.id, final_attempt_status,
            summary="", error_code=error_code, blocking_reason=blocking_reason,
        )

        if escalating:
            await store.tasks.increment_escalation(task.id)
            task = await store.tasks.get(task.id)  # reload escalation_count
            await store.events.append(
                ev_types.make_event(
                    task.run_id, ev_types.ATTEMPT_ESCALATED,
                    task_id=task.id, attempt_id=attempt.id,
                )
            )
            # task remains in_progress; loop back to assign next worker
            continue

        # No escalation path → block and request approval
        task = transition_task(task, TaskStatus.blocked)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(task.run_id, ev_types.TASK_BLOCKED, task_id=task.id)
        )
        await approvals.request_approval(task.run_id, task.id, attempt.id, store)
        return


async def _collect_upstream_outputs(task: Task, store: Store) -> list[str]:
    """Return text summaries from all completed dependency tasks."""
    results = []
    for dep in task.dependencies:
        for artifact in await store.artifacts.list_by_task(dep.task_id):
            if artifact.type == "text_output":
                content = artifact.location.get("content", "")
                if content:
                    results.append(content)
    return results


async def _unlock_downstream(completed_task: Task, store: Store) -> None:
    """Transition pending tasks to ready when their dependencies are now satisfied."""
    all_tasks = await store.tasks.list_by_run(completed_task.run_id)
    artifacts = await store.artifacts.list_by_run(completed_task.run_id)
    artifact_task_ids = {a.task_id for a in artifacts}
    approval_events = await store.events.list_by_type(completed_task.run_id, ev_types.APPROVAL_GRANTED)
    approval_task_ids = {e.task_id for e in approval_events if e.task_id}

    newly_ready = dependencies.check_ready(all_tasks, artifact_task_ids, approval_task_ids)
    for task in newly_ready:
        task = transition_task(task, TaskStatus.ready)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(task.run_id, ev_types.TASK_READY, task_id=task.id)
        )
