# backend/orchestrator/service/executor.py
"""Lobster-style deterministic executor for driving tasks through their lifecycle."""
from __future__ import annotations
import dataclasses
import logging

from ..domain import events as ev_types
from ..domain import dependencies
from ..domain.models import Artifact, Task, TaskAttempt, TaskStatus, AttemptStatus
from ..domain.transitions import transition_task
from ..store.base import Store
from ..verifier.verifier import Verifier, VerifierError
from ..verifier.config import MAX_SOFT_RETRIES
from ..workers.validator import validate_code_output, validate_general_output
from ..workers.base import WorkerEvent
from ..workers.registry import WorkerRegistry
from ..routing.router import assign_worker, NoCapableWorker
from ..routing.escalation import should_escalate
from . import approvals

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"attempt.completed", "attempt.failed", "attempt.blocked"})


async def run_task(
    task_id: str,
    store: Store,
    registry: WorkerRegistry,
    verifier: Verifier,
) -> None:
    """Drive one task from ready → terminal using a Lobster-style deterministic loop.

    Steps per attempt: assign → dispatch → stream → verify → soft-retry/escalate/complete/block
    """
    task = await store.tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id!r} not found")

    attempted: set[str] = set()
    soft_retry_count: dict[str, int] = {}
    _retry_worker_id: str | None = None   # set on soft retry to force same worker
    _retry_feedback: str | None = None    # verifier feedback to inject on retry

    while True:
        # ── ASSIGN ──────────────────────────────────────────────────────────
        if _retry_worker_id:
            worker_id = _retry_worker_id
        else:
            try:
                worker_id = assign_worker(task, registry, exclude=attempted)
            except NoCapableWorker:
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
        logger.info("task %s assigned to worker %s", task.id, worker_id)

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
        logger.info("attempt %s started", attempt.id)

        # ── STREAM ──────────────────────────────────────────────────────────
        outcome: WorkerEvent | None = None
        async for w_ev in worker.execute(attempt, dispatch_task, feedback=_retry_feedback):
            if w_ev.type in _TERMINAL:
                outcome = w_ev
                break
            if w_ev.type in ev_types.ALL_EVENT_TYPES:
                await store.events.append(
                    ev_types.make_event(
                        task.run_id, w_ev.type,
                        payload=w_ev.payload,
                        task_id=task.id, attempt_id=attempt.id,
                    )
                )

        # Reset retry state after consuming it
        _retry_feedback = None
        _retry_worker_id = None

        if outcome is None:
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_ended", "error": "worker stream ended without terminal event"},
            )

        # ── VERIFY ──────────────────────────────────────────────────────────
        if outcome.type == "attempt.completed":
            summary = outcome.payload.get("summary", "")

            if worker_id.startswith("ollama:"):
                # Fast Python heuristic — no LLM API call
                if worker_id == "ollama:coder":
                    is_valid, reason = validate_code_output(summary)
                else:
                    is_valid, reason = validate_general_output(summary)
                result_action = "pass" if is_valid else "retry"
                result_feedback = "" if is_valid else f"Output validation failed: {reason}"
            else:
                try:
                    result = await verifier.verify(task, summary)
                    result_action = result.action
                    result_feedback = result.feedback
                except VerifierError:
                    result_action = "escalate"
                    result_feedback = "verifier error — escalating to next worker"

            logger.info("verifier action=%s task=%s attempt=%s", result_action, task.id, attempt.id)

            if result_action == "pass":
                await store.tasks.update_attempt_result(
                    attempt.id, AttemptStatus.completed, summary=summary, output=summary,
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
                logger.info("task %s completed", task.id)
                worker.clear_history(task.id)
                await _unlock_downstream(task, store)
                return

            if result_action == "retry" and soft_retry_count.get(worker_id, 0) < MAX_SOFT_RETRIES:
                await store.tasks.update_attempt_result(
                    attempt.id, AttemptStatus.failed,
                    summary="", error_code="verification_retry",
                    blocking_reason=result_feedback,
                )
                soft_retry_count[worker_id] = soft_retry_count.get(worker_id, 0) + 1
                _retry_worker_id = worker_id
                _retry_feedback = result_feedback
                logger.warning(
                    "soft retry %d/%d for task %s: %s",
                    soft_retry_count[worker_id], MAX_SOFT_RETRIES,
                    task.id, result_feedback[:100],
                )
                continue  # loop back — same worker, feedback injected via history

            # Verification failed (score 0-3 or retries exhausted) → treat as attempt.failed
            worker.clear_history(task.id)
            soft_retry_count = {}
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "verification_failed", "error": result_feedback},
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

        escalating = should_escalate(task, registry, attempted)
        final_attempt_status = AttemptStatus.escalated if escalating else AttemptStatus.failed
        await store.tasks.update_attempt_result(
            attempt.id, final_attempt_status,
            summary="", error_code=error_code, blocking_reason=blocking_reason,
        )

        if escalating:
            attempted.add(worker_id)
            await store.tasks.increment_escalation(task.id)
            task = await store.tasks.get(task.id)
            await store.events.append(
                ev_types.make_event(
                    task.run_id, ev_types.ATTEMPT_ESCALATED,
                    task_id=task.id, attempt_id=attempt.id,
                )
            )
            logger.warning("escalating task %s from %s", task.id, worker_id)
            continue

        task = transition_task(task, TaskStatus.blocked)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(task.run_id, ev_types.TASK_BLOCKED, task_id=task.id)
        )
        await approvals.request_approval(task.run_id, task.id, attempt.id, store)
        logger.error("task %s blocked/failed: %s", task.id, blocking_reason[:100])
        return


async def _collect_upstream_outputs(task: Task, store: Store) -> list[str]:
    results = []
    for dep in task.dependencies:
        for artifact in await store.artifacts.list_by_task(dep.task_id):
            if artifact.type == "text_output":
                content = artifact.location.get("content", "")
                if content:
                    results.append(content)
    return results


async def _unlock_downstream(completed_task: Task, store: Store) -> None:
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
