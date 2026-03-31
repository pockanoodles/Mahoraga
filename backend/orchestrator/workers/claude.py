from __future__ import annotations
import asyncio
from typing import AsyncGenerator

import anthropic

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt


class ClaudeWorker(WorkerAdapter):
    """Worker backed by the Anthropic API (claude-sonnet-4-6)."""

    _id = "claude"
    _capabilities = ["file_editing", "deep_reasoning", "review", "planning"]

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    async def execute(self, attempt: TaskAttempt, task: Task) -> AsyncGenerator[WorkerEvent, None]:
        prompt = _build_prompt(task)
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "api_error", "error": str(exc)},
            )
            return

        content = response.content[0].text if response.content else ""
        if content:
            yield WorkerEvent(
                type="attempt.completed",
                payload={"summary": content},
            )
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Claude returned empty content"},
            )

    async def cancel(self, attempt_id: str) -> None:
        pass  # Anthropic API does not support cancellation

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)
