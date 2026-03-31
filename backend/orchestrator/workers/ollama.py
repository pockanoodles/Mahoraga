from __future__ import annotations
from typing import AsyncGenerator

import httpx

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth


class OllamaWorker(WorkerAdapter):
    """Worker backed by a local Ollama instance via /api/chat."""

    def __init__(self, model: str = "qwen3:8b", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")

    @property
    def id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "general", "cheap_repetitive"]

    async def execute(self, attempt: TaskAttempt, task: Task) -> AsyncGenerator[WorkerEvent, None]:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": _build_prompt(task)}],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=300.0) as client:
                resp = await client.post("/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "http_error", "error": str(exc)},
            )
            return

        content = resp.json().get("message", {}).get("content", "")
        if content:
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty content"},
            )

    async def cancel(self, attempt_id: str) -> None:
        pass  # Ollama HTTP API does not support cancellation

    async def health(self) -> WorkerHealth:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0) as client:
                resp = await client.get("/")
                resp.raise_for_status()
            return WorkerHealth(worker_id=self.id, healthy=True)
        except httpx.HTTPError as exc:
            return WorkerHealth(worker_id=self.id, healthy=False, detail=str(exc))


def _build_prompt(task: Task) -> str:
    lines = [f"# Task: {task.title}", f"\n## Goal\n{task.goal}"]
    if task.context_refs:
        lines.append("\n## Context\n" + "\n".join(f"- {ref}" for ref in task.context_refs))
    if task.constraints:
        lines.append("\n## Constraints\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.done_criteria:
        lines.append(f"\n## Done Criteria\n{task.done_criteria}")
    return "\n".join(lines)
