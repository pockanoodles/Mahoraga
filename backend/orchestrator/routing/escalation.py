from __future__ import annotations
from ..domain.models import Task
from ..domain.transitions import can_escalate
from ..workers.registry import WorkerRegistry
from .router import assign_worker, NoCapableWorker


def should_escalate(
    task: Task,
    registry: WorkerRegistry,
    attempted: set[str],
) -> bool:
    """Return True if the task should be escalated to a different worker.

    Conditions (all must hold):
    - task.escalation_count < ESCALATION_LIMIT (domain rule)
    - at least one worker exists with required capabilities that hasn't already attempted
    """
    if not can_escalate(task):
        return False
    try:
        assign_worker(task, registry, exclude=attempted)
        return True
    except NoCapableWorker:
        return False
