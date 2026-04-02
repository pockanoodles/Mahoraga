from __future__ import annotations
import dataclasses
import json

import httpx

from ..domain.models import Dependency, DependencyType, Mission, Task
from .prompt import SYSTEM_PROMPT, build_user_message
from .validator import ValidationError, validate_raw_tasks


class OllamaUnavailable(RuntimeError):
    """Raised when the Ollama server cannot be reached."""


class PlannerError(RuntimeError):
    """Raised when the planner produces unusable output."""


async def generate_tasks(
    mission: Mission,
    run_id: str,
    base_url: str = "http://localhost:11434",
    model: str = "qwen3:8b",
) -> list[Task]:
    """Call Ollama to decompose a mission into Task objects.

    Returns a list of Task objects with dependencies resolved to IDs,
    ready to be saved to the store.

    Raises:
        OllamaUnavailable: if the Ollama server is unreachable.
        PlannerError: if the model output cannot be parsed or fails validation.
    """
    user_msg = build_user_message(
        title=mission.title,
        goal=mission.goal,
        success_condition=mission.success_condition,
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120.0) as client:
            resp = await client.post("/api/chat", json=payload)
            resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise OllamaUnavailable(f"Ollama unreachable at {base_url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PlannerError(f"Ollama HTTP error: {exc}") from exc

    raw_content = resp.json().get("message", {}).get("content", "")
    try:
        data = json.loads(raw_content)
        raw_tasks: list[dict] = data["tasks"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlannerError(
            f"Parse error — model output was not valid JSON with a 'tasks' key. "
            f"Raw output: {raw_content!r}"
        ) from exc

    try:
        validate_raw_tasks(raw_tasks)
    except ValidationError as exc:
        raise PlannerError(f"Validation failed: {exc}") from exc

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
