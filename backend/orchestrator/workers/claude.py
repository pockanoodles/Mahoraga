# backend/orchestrator/workers/claude.py
from __future__ import annotations
import asyncio
from typing import AsyncGenerator

import anthropic

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt


class ClaudeWorker(WorkerAdapter):
    """Worker backed by the Anthropic API with stateful per-task conversation history."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude:sonnet",
        capabilities: list[str] | None = None,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._worker_id = worker_id
        self._capabilities = capabilities or ["general", "deep_reasoning"]
        # Per-task conversation history keyed by task_id
        self._history: dict[str, list[dict[str, str]]] = {}
        self._last_output: dict[str, str] = {}

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        task_id = task.id

        if task_id not in self._history or feedback is None:
            # First call for this task: build fresh history
            self._history[task_id] = [{"role": "user", "content": _build_prompt(task)}]
        else:
            # Retry: append prior assistant output + verifier feedback
            prior = self._last_output.get(task_id, "")
            self._history[task_id].append({"role": "assistant", "content": prior})
            self._history[task_id].append({"role": "user", "content": feedback})

        messages = self._history[task_id]
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=8192,
                messages=messages,
            )
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "api_error", "error": str(exc)},
            )
            return

        content = response.content[0].text if response.content else ""
        if content:
            self._last_output[task_id] = content
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Claude returned empty content"},
            )

    def clear_history(self, task_id: str) -> None:
        """Clear conversation history for a task after it reaches terminal state."""
        self._history.pop(task_id, None)
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)
