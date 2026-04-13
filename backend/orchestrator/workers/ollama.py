# backend/orchestrator/workers/ollama.py
from __future__ import annotations
import json
import logging
import re
from typing import AsyncGenerator

import httpx

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from .postprocess import extract_code, strip_preamble
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS: dict[str, str] = {
    "ollama:planner": (
        "You are a planning assistant. Given a planning or analysis task, produce a concise structured plan. "
        "Use a numbered list. No preamble, no sign-off."
    ),
    "ollama:fast": (
        "You are a concise assistant. Answer directly and briefly. "
        "Do not include unnecessary preamble, examples, or sign-offs."
    ),
    "ollama:coder": (
        "You are a code generator. Follow these rules strictly:\n"
        "1. Output ONLY the code in a single code block.\n"
        "2. No explanations, no usage examples, no notes.\n"
        "3. Include brief inline comments only where logic is non-obvious.\n"
        "4. Use standard library solutions when available.\n"
        "5. Handle basic edge cases (empty input, null checks)."
    ),
    "ollama:general": (
        "You are a concise assistant. Answer directly and briefly. "
        "Do not include unnecessary preamble, examples, or sign-offs."
    ),
}

_OLLAMA_OPTIONS = {"num_ctx": 4096}


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
        _ollama_metrics: dict | None = None
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json={
                        "model": self._model,
                        "messages": messages,
                        "stream": True,
                        "think": False,
                        "options": _OLLAMA_OPTIONS,
                    },
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
                        msg = chunk.get("message", {})
                        content = msg.get("content", "")
                        # Collect content tokens; skip empty chunks (thinking-phase chunks
                        # have content="" with thinking in message.thinking or <think> tags).
                        # _THINK_TAG_RE strips any residual <think>...</think> blocks below.
                        if content:
                            full_response.append(content)
                        if chunk.get("done"):
                            # Extract exact token/timing metrics from the final Ollama chunk
                            eval_count = chunk.get("eval_count", 0)
                            eval_duration_ns = chunk.get("eval_duration", 0)
                            eval_duration_s = eval_duration_ns / 1e9 if eval_duration_ns else 0.0
                            tps = round(eval_count / eval_duration_s, 1) if eval_duration_s > 0 else 0.0
                            _ollama_metrics = {
                                "elapsed_s": round(eval_duration_s, 2),
                                "tokens": eval_count,
                                "throughput_tps": tps,
                            }
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

        summary = _THINK_TAG_RE.sub("", "".join(full_response)).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty response"},
            )
            return

        # Post-process: strip non-code content for coder, strip preamble for others
        if self._worker_id == "ollama:coder":
            summary = extract_code(summary)
        else:
            summary = strip_preamble(summary)

        logger.info("OLLAMA WORKER FINAL OUTPUT (first 200 chars): %s", summary[:200])
        if _ollama_metrics:
            yield WorkerEvent(type="metrics", payload=_ollama_metrics)
        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass  # Ollama HTTP streaming cannot be cancelled mid-flight; no-op

    async def warm(self) -> None:
        """Pre-load the model into Ollama's memory. Fire-and-forget at startup."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{self._base_url}/api/generate",
                    json={"model": self._model, "prompt": "", "keep_alive": "10m"},
                )
            logger.info("ollama: warmed %s (%s)", self._worker_id, self._model)
        except Exception as exc:
            logger.debug("ollama: warm skipped for %s: %s", self._worker_id, exc)

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
