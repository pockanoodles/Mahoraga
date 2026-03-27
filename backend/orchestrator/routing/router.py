from __future__ import annotations
from ..domain.models import Task
from ..workers.base import WorkerAdapter
from ..workers.registry import WorkerRegistry, WorkerNotFoundError


class NoCapableWorker(Exception):
    pass


def assign_worker(
    task: Task,
    registry: WorkerRegistry,
    exclude: set[str] | None = None,
) -> str:
    """Return worker_id for task using preferred_worker_type first, then capability matching.

    Raises NoCapableWorker if no registered worker satisfies the requirements.
    """
    exclude = exclude or set()

    # Try preferred worker first (if it has the required capabilities and is not excluded)
    if task.preferred_worker_type and task.preferred_worker_type not in exclude:
        try:
            worker = registry.get(task.preferred_worker_type)
            if _capable(worker, task.required_capabilities):
                return worker.id
        except WorkerNotFoundError:
            pass

    # Fall back to capability matching across all registered workers
    candidates = [
        w for w in registry.list_all()
        if w.id not in exclude and _capable(w, task.required_capabilities)
    ]
    if not candidates:
        raise NoCapableWorker(
            f"No worker with capabilities {task.required_capabilities} "
            f"(excluding {exclude})"
        )
    return candidates[0].id


def _capable(worker: WorkerAdapter, required: list[str]) -> bool:
    return all(cap in worker.capabilities for cap in required)
