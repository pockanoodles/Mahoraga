# backend/orchestrator/workers/claude.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import AsyncGenerator

import anthropic

from ..domain.models import Task, TaskAttempt
from ..tracking.pricing import calculate_cost
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt

logger = logging.getLogger(__name__)


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
        t0 = time.monotonic()
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
        elapsed_s = round(time.monotonic() - t0, 2)

        content = response.content[0].text if response.content else ""
        if content:
            self._last_output[task_id] = content
            metrics = self._telemetry(response, elapsed_s)
            if metrics is not None:
                yield WorkerEvent(type="metrics", payload=metrics)
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Claude returned empty content"},
            )

    def _telemetry(self, response, elapsed_s: float) -> dict | None:
        """Build a `metrics` payload (same shape as ClaudeCliWorker's).

        Without this the SDK arm records $0 while claude-cli records real
        cost, and the bandit's cost penalty would prefer this arm for
        accounting reasons rather than real ones. Returns None (skips the
        event) if the response carries no usable usage data.
        """
        try:
            usage = getattr(response, "usage", None)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            cache_read_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        except Exception as exc:
            logger.warning("claude: telemetry extraction failed (%s) — skipping metrics", exc)
            return None
        tps = round(output_tokens / elapsed_s, 1) if elapsed_s > 0 and output_tokens else 0.0
        return {
            "elapsed_s": elapsed_s,
            "tokens": output_tokens,
            "throughput_tps": tps,
            "prompt_tokens": input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cost_usd": calculate_cost(
                self._model, input_tokens, output_tokens, cache_read_tokens
            ),
            "model": self._model,
        }

    def clear_history(self, task_id: str) -> None:
        """Clear conversation history for a task after it reaches terminal state."""
        self._history.pop(task_id, None)
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)
