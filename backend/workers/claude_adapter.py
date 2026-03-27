from __future__ import annotations
import asyncio
import time
from typing import AsyncIterator

import anthropic

from backend.orchestrator_svc.models import Task, WorkerResult, Event

CLAUDE_MODEL = "claude-sonnet-4-6"

_PLANNING_SYSTEM = """\
You are a senior software architect. Given a task, produce:
1. A step-by-step implementation plan
2. Key risks or edge cases
3. Recommended starting point

Be concise. Plain text with clear sections. No preamble."""

_REVIEW_SYSTEM = """\
You are a senior code reviewer. Given a task goal and context, produce:
1. APPROVE or REQUEST_CHANGES verdict
2. Specific issues found (if any)
3. Suggested fixes

Be concise. Plain text."""

_DEFAULT_SYSTEM = """\
You are a senior software engineer. Complete the task concisely and correctly.
If you are given code context, reason carefully before answering."""


def _system_for(task_type: str) -> str:
    if task_type in ("plan", "explain"):
        return _PLANNING_SYSTEM
    if task_type == "review":
        return _REVIEW_SYSTEM
    return _DEFAULT_SYSTEM


class ClaudeAdapter:
    worker_id = "claude"
    display_name = "Claude Code + Superpowers"

    def __init__(self, model: str = CLAUDE_MODEL) -> None:
        self._model = model
        self._client = anthropic.AsyncAnthropic()
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, WorkerResult] = {}

    async def submit_task(self, task: Task) -> str:
        bg = asyncio.create_task(self._run_task(task))
        self._active_tasks[task.id] = bg
        return task.id

    async def _run_task(self, task: Task) -> None:
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_system_for(task.task_type),
                messages=[{"role": "user", "content": task.goal}],
            )
            summary = message.content[0].text if message.content else ""
            self._results[task.id] = WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                status="completed",
                summary=summary,
                created_at=time.time(),
            )
        except Exception as exc:
            self._results[task.id] = WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                status="failed",
                summary=str(exc),
                created_at=time.time(),
            )

    async def stream_events(self, task_id: str) -> AsyncIterator[Event]:
        for _ in range(120):
            if task_id in self._results:
                result = self._results[task_id]
                event_type = "task.completed" if result.status == "completed" else "task.failed"
                yield Event(
                    type=event_type,
                    task_id=task_id,
                    worker_id=self.worker_id,
                    content={"summary": result.summary},
                )
                return
            await asyncio.sleep(1)
        yield Event(
            type="task.failed",
            task_id=task_id,
            worker_id=self.worker_id,
            content={"error": "timeout after 120s"},
        )

    async def get_result(self, task_id: str) -> WorkerResult:
        if task_id not in self._results:
            raise RuntimeError(f"Result for task '{task_id}' not ready")
        return self._results[task_id]

    async def cancel_task(self, task_id: str) -> None:
        bg = self._active_tasks.get(task_id)
        if bg and not bg.done():
            bg.cancel()

    async def health(self) -> dict:
        try:
            await self._client.models.list()
            return {"status": "ok", "worker_id": self.worker_id, "model": self._model}
        except anthropic.AuthenticationError:
            return {"status": "down", "worker_id": self.worker_id, "error": "invalid API key"}
        except Exception as exc:
            return {"status": "down", "worker_id": self.worker_id, "error": str(exc)}
