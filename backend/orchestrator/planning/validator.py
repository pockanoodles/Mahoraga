from __future__ import annotations
import dataclasses
from backend.orchestrator.domain.dependencies import detect_cycles, CycleError
from backend.orchestrator.domain.models import Dependency, DependencyType, Task


class ValidationError(ValueError):
    pass


def validate_raw_tasks(tasks: list[dict]) -> None:
    """Validate a list of raw task dicts from the planner before saving.

    Checks:
    - Every task has non-empty 'title' and 'goal'
    - All dependency references name a title that exists in the batch
    - No cycles in the dependency graph

    Raises ValidationError describing the first failure found.
    Raises nothing if the list is empty (valid degenerate case).
    """
    titles = {t.get("title", "") for t in tasks}

    for i, task in enumerate(tasks):
        title = task.get("title", "")
        if not title or not title.strip():
            raise ValidationError(f"Task at index {i} is missing a non-empty 'title'")
        goal = task.get("goal", "")
        if not goal or not goal.strip():
            raise ValidationError(f"Task '{title}' is missing a non-empty 'goal'")
        for dep_title in task.get("dependencies", []):
            if dep_title not in titles:
                raise ValidationError(
                    f"Task '{title}' depends on '{dep_title}', which does not exist in this batch"
                )

    # Build stub Task objects solely to reuse detect_cycles
    title_to_id = {t["title"]: str(i) for i, t in enumerate(tasks)}
    stub_tasks: list[Task] = []
    for t in tasks:
        task_id = title_to_id[t["title"]]
        deps = [
            Dependency(task_id=title_to_id[dep_title], type=DependencyType.completion)
            for dep_title in t.get("dependencies", [])
        ]
        stub = Task.new(run_id="stub", title=t["title"], goal=t["goal"])
        stub = dataclasses.replace(stub, id=task_id, dependencies=deps)
        stub_tasks.append(stub)

    try:
        detect_cycles(stub_tasks)
    except CycleError as e:
        raise ValidationError(str(e)) from e
