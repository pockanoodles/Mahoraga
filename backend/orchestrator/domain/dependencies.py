from __future__ import annotations
from .models import Task, TaskStatus, DependencyType


class CycleError(ValueError):
    pass


def check_ready(
    tasks: list[Task],
    artifact_task_ids: set[str],
    approval_task_ids: set[str],
) -> list[Task]:
    """Return pending tasks whose dependencies are all currently satisfied.

    Args:
        tasks: All tasks in the run (must be scoped to a single run_id).
        artifact_task_ids: task_ids that have produced at least one artifact.
        approval_task_ids: task_ids for which an approval.granted event exists.
    """
    completed_ids = {t.id for t in tasks if t.status == TaskStatus.completed}
    return [
        t for t in tasks
        if t.status == TaskStatus.pending
        and _all_deps_satisfied(t, completed_ids, artifact_task_ids, approval_task_ids)
    ]


def _all_deps_satisfied(
    task: Task,
    completed_ids: set[str],
    artifact_task_ids: set[str],
    approval_task_ids: set[str],
) -> bool:
    for dep in task.dependencies:
        if dep.type == DependencyType.completion:
            if dep.task_id not in completed_ids:
                return False
        elif dep.type == DependencyType.artifact:
            if dep.task_id not in artifact_task_ids:
                return False
        elif dep.type == DependencyType.approval:
            if dep.task_id not in approval_task_ids:
                return False
        else:
            return False  # Unknown dependency type — fail closed
    return True


def detect_cycles(tasks: list[Task]) -> None:
    """Raise CycleError if the task graph contains a cycle.

    Uses iterative DFS using an explicit stack with three-color marking:
    0=unvisited, 1=visiting, 2=done. Only considers dependency edges within
    the provided task list; external references (deps pointing to tasks not
    in the list) are silently ignored.
    """
    task_ids = {t.id for t in tasks}
    # Build adjacency: task_id → list of upstream task_ids it depends on
    adj: dict[str, list[str]] = {}
    for task in tasks:
        for dep in task.dependencies:
            if dep.task_id in task_ids:
                adj.setdefault(task.id, []).append(dep.task_id)

    color: dict[str, int] = {}

    for start in tasks:
        if color.get(start.id, 0) != 0:
            continue
        color[start.id] = 1
        stack = [(start.id, iter(adj.get(start.id, [])))]
        while stack:
            node, neighbors = stack[-1]
            try:
                neighbor = next(neighbors)
                state = color.get(neighbor, 0)
                if state == 1:
                    raise CycleError(
                        f"Cycle detected: {node} → {neighbor} closes a loop"
                    )
                if state == 0:
                    color[neighbor] = 1
                    stack.append((neighbor, iter(adj.get(neighbor, []))))
            except StopIteration:
                color[node] = 2
                stack.pop()
