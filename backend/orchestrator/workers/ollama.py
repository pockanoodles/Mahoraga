from __future__ import annotations
from typing import AsyncGenerator

import httpx

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt


class OllamaWorker(WorkerAdapter):
    """Worker backed by a local Ollama instance via /api/chat.

    Maintains per-task conversation history to support feedback injection on retries.
    """

    def __init__(self, model: str = "qwen3:8b", base_url: str = "http://127.0.0.1:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._history: dict[str, list[dict[str, str]]] = {}
        self._last_output: dict[str, str] = {}

    @property
    def id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "general", "cheap_repetitive"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        task_id = task.id

        if task_id not in self._history or feedback is None:
            self._history[task_id] = [{"role": "user", "content": _build_prompt(task)}]
        else:
            prior = self._last_output.get(task_id, "")
            self._history[task_id].append({"role": "assistant", "content": prior})
            self._history[task_id].append({"role": "user", "content": feedback})

        payload = {
            "model": self._model,
            "messages": self._history[task_id],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=600.0) as client:
                resp = await client.post("/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.TimeoutException as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"Ollama inference timed out: {exc}"},
            )
            return
        except httpx.HTTPError as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "http_error", "error": str(exc)},
            )
            return

        content = resp.json().get("message", {}).get("content", "")
        if content:
            self._last_output[task_id] = content
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty content"},
            )

    def clear_history(self, task_id: str) -> None:
        self._history.pop(task_id, None)
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0) as client:
                resp = await client.get("/")
                resp.raise_for_status()
            return WorkerHealth(worker_id=self.id, healthy=True)
        except httpx.HTTPError as exc:
            return WorkerHealth(worker_id=self.id, healthy=False, detail=str(exc))
