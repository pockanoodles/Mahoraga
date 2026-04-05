from __future__ import annotations
import dataclasses
import json

import anthropic

from ..domain.models import Dependency, DependencyType, Mission, Task
from .config import MAX_TASKS, PLANNER_MODEL
from .prompt import build_planner_prompt
from .validator import ValidationError, validate_raw_tasks


class PlannerError(RuntimeError):
    """Raised when the planner produces unusable output."""


async def generate_tasks(
    mission: Mission,
    run_id: str,
    user_profile: str | None = None,
) -> list[Task]:
    """Decompose a mission into Task objects using the Haiku planner.

    Args:
        mission: The mission to decompose.
        run_id: ID of the current orchestration run.
        user_profile: Optional user context forwarded to the system prompt.

    Returns:
        List of Task domain objects with resolved dependencies.

    Raises:
        PlannerError: If the API call fails, the response is not valid JSON,
                      or the task list fails validation.
    """
    system_prompt = build_planner_prompt(user_profile=user_profile)

    user_message = (
        f"Mission title: {mission.title}\n"
        f"Goal: {mission.goal}\n"
    )
    if mission.background:
        user_message += f"Background: {mission.background}\n"
    if mission.success_condition:
        user_message += f"Success condition: {mission.success_condition}\n"
    if mission.global_constraints:
        user_message += f"Constraints: {', '.join(mission.global_constraints)}\n"

    user_message += "\nDecompose this mission into tasks. Return only the JSON array."

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model=PLANNER_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise PlannerError(f"Haiku API error: {exc}") from exc

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        # Drop first and last lines (``` or ```json)
        raw_text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        raw_tasks: list[dict] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"Parse error — Haiku returned non-JSON: {exc}") from exc

    if not isinstance(raw_tasks, list):
        raise PlannerError(f"Parse error — expected a JSON array, got {type(raw_tasks).__name__}")

    # Cap at MAX_TASKS
    raw_tasks = raw_tasks[:MAX_TASKS]

    try:
        validate_raw_tasks(raw_tasks)
    except ValidationError as exc:
        raise PlannerError(f"Validation error: {exc}") from exc

    return _build_tasks(raw_tasks, run_id)


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
