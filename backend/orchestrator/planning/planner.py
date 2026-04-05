from __future__ import annotations
import dataclasses

from ..domain.models import Dependency, DependencyType, Mission, Task
from .validator import ValidationError, validate_raw_tasks


class PlannerError(RuntimeError):
    """Raised when the planner produces unusable output."""


async def generate_tasks(
    mission: Mission,
    run_id: str,
) -> list[Task]:
    """Decompose a mission into Task objects using an LLM planner.

    NOTE: Ollama support has been removed. This function will be replaced
    with a Haiku-based implementation in Task 2.

    Raises:
        NotImplementedError: always, until Task 2 is implemented.
    """
    raise NotImplementedError(
        "generate_tasks: Ollama backend removed. Haiku planner coming in Task 2."
    )


def _build_tasks(raw_tasks: list[dict], run_id: str) -> list[Task]:
    """Convert validated raw task dicts into Task domain objects with resolved IDs."""
    # First pass: create tasks without dependencies to get IDs
    tasks_by_title: dict[str, Task] = {}
    for raw in raw_tasks:
        task = Task.new(
            run_id=run_id,
            title=raw["title"],
            goal=raw["goal"],
            done_criteria=raw.get("done_criteria", ""),
            context_refs=[],
        )
        tasks_by_title[raw["title"]] = task

    # Second pass: resolve dependency titles → IDs
    result: list[Task] = []
    for raw in raw_tasks:
        task = tasks_by_title[raw["title"]]
        deps = [
            Dependency(
                task_id=tasks_by_title[dep_title].id,
                type=DependencyType.completion,
            )
            for dep_title in raw.get("dependencies", [])
        ]
        result.append(dataclasses.replace(task, dependencies=deps))

    return result
