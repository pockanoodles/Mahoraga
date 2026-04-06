# backend/orchestrator/workers/ollama.py
from __future__ import annotations
import json
import logging
from typing import AsyncGenerator

import httpx

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS: dict[str, str] = {
    "ollama:planner": (
        "You are a task-planning assistant. Decompose the given task into clear, ordered steps. "
        "Be concise and structured."
    ),
    "ollama:fast": "You are a quick-answer assistant. Answer directly and concisely.",
    "ollama:coder": (
        "You are an expert software engineer. Write clean, correct code. "
        "Explain your implementation briefly."
    ),
    "ollama:general": "You are a knowledgeable assistant. Provide clear, thorough answers.",
}


class OllamaWorker(WorkerAdapter):
    def __init__(
        self,
        model: str,
        worker_id: str,
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._base_url = base_url.rstrip("/")
        self._system_prompt = _SYSTEM_PROMPTS.get(worker_id, "You are a helpful assistant.")

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["general", "code_generation", "analysis"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        user_content = f"Task: {task.title}\n\nGoal: {task.goal}"
        if task.done_criteria:
            user_content += f"\n\nDone when: {task.done_criteria}"
        if task.context_refs:
            user_content += "\n\nContext:\n" + "\n".join(task.context_refs)
        if feedback:
            user_content += f"\n\nFeedback on previous attempt: {feedback}"

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
        ]

        full_response: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json={"model": self._model, "messages": messages, "stream": True},
                ) as response:
                    if response.status_code != 200:
                        yield WorkerEvent(
                            type="attempt.failed",
                            payload={
                                "error_code": "http_error",
                                "error": f"Ollama returned HTTP {response.status_code}",
                            },
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            full_response.append(content)
                        if chunk.get("done"):
                            break
        except httpx.ConnectError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "ollama_unreachable",
                    "error": f"Ollama is not running at {self._base_url}",
                },
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": f"[ERROR] {exc}"},
            )
            return

        summary = "".join(full_response)
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty response"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass  # Ollama HTTP streaming cannot be cancelled mid-flight; no-op

    async def health(self) -> WorkerHealth:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            if response.status_code != 200:
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail="Ollama returned non-200 on /api/tags",
                )
            model_names = [m["name"] for m in response.json().get("models", [])]
            model_base = self._model.split(":")[0]
            if not any(m.startswith(model_base) for m in model_names):
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail=f"Model {self._model!r} not pulled. Run: ollama pull {self._model}",
                )
            return WorkerHealth(worker_id=self._worker_id, healthy=True)
        except (httpx.ConnectError, httpx.TimeoutException):
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail=f"Ollama is not running at {self._base_url}",
            )
