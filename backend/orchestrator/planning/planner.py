from __future__ import annotations
import dataclasses
import json

import httpx

from ..config import ENABLED_BACKENDS
from ..domain.models import Dependency, DependencyType, Mission, Task
from .config import MAX_TASKS
from .prompt import build_planner_prompt
from .validator import ValidationError, validate_raw_tasks

_OLLAMA_BASE_URL = "http://localhost:11434"


class PlannerError(RuntimeError):
    """Raised when the planner produces unusable output."""


async def generate_tasks(
    mission: Mission,
    run_id: str,
    user_profile: str | None = None,
) -> list[Task]:
    """Decompose a mission into Task objects.

    Uses the Haiku planner when "claude" is in ENABLED_BACKENDS,
    otherwise falls back to the local Ollama planner.

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

    if "claude" in ENABLED_BACKENDS:
        raw_text = await _plan_with_claude(user_message, user_profile)
    else:
        raw_text = await _plan_with_ollama(user_message)

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        raw_tasks: list[dict] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"Parse error — planner returned non-JSON: {exc}") from exc

    if not isinstance(raw_tasks, list):
        raise PlannerError(f"Parse error — expected a JSON array, got {type(raw_tasks).__name__}")

    raw_tasks = raw_tasks[:MAX_TASKS]

    try:
        validate_raw_tasks(raw_tasks)
    except ValidationError as exc:
        raise PlannerError(f"Validation error: {exc}") from exc

    return _build_tasks(raw_tasks, run_id)


async def _plan_with_claude(user_message: str, user_profile: str | None) -> str:
    """Call the Haiku planner via Anthropic API. Used when 'claude' in ENABLED_BACKENDS."""
    import anthropic
    from .config import PLANNER_MODEL

    system_prompt = build_planner_prompt(user_profile=user_profile)
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
    return response.content[0].text.strip()


async def _plan_with_ollama(user_message: str) -> str:
    """Call the local Ollama planner. Used when 'claude' not in ENABLED_BACKENDS."""
    system_prompt = (
        "You are a task decomposer. Given a mission, break it into 2-5 concrete subtasks.\n"
        "Return ONLY a JSON array of objects with 'title', 'goal', and 'dependencies' fields.\n"
        "No explanation, no markdown, no commentary outside the JSON.\n"
        "Keep subtasks focused and actionable. Do NOT over-decompose simple tasks."
    )
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{_OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": "qwen3:4b-q4_K_M",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "stream": False,
                    "think": False,
                },
            )
            response.raise_for_status()
    except httpx.RequestError as exc:
        raise PlannerError(f"Ollama request error: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise PlannerError(f"Ollama HTTP error {exc.response.status_code}") from exc

    data = response.json()
    return data["message"]["content"].strip()


def _build_tasks(raw_tasks: list[dict], run_id: str) -> list[Task]:
    """Convert validated raw task dicts into Task domain objects with resolved IDs."""
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
