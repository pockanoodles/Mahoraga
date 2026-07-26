# backend/orchestrator/workers/ollama.py
from __future__ import annotations
import asyncio
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

# Role → system prompt. New worker_ids may look like "ollama:gemma4-e4b:coder";
# we extract the trailing role segment to resolve a default prompt.
ROLE_PROMPTS: dict[str, str] = {
    "planner": (
        "You are a planning assistant. Given a planning or analysis task, produce a concise structured plan. "
        "Use a numbered list. No preamble, no sign-off."
    ),
    "fast": (
        "You are a concise assistant. Answer directly and briefly. "
        "Do not include unnecessary preamble, examples, or sign-offs."
    ),
    "coder": (
        "You are a code generator. Follow these rules strictly:\n"
        "1. Output ONLY the code in a single code block.\n"
        "2. No explanations, no usage examples, no notes.\n"
        "3. Include brief inline comments only where logic is non-obvious.\n"
        "4. Use standard library solutions when available.\n"
        "5. Handle basic edge cases (empty input, null checks)."
    ),
    "general": (
        "You are a concise assistant. Answer directly and briefly. "
        "Do not include unnecessary preamble, examples, or sign-offs."
    ),
}

# Back-compat: legacy single-model worker_ids (ollama:planner etc).
_SYSTEM_PROMPTS: dict[str, str] = {f"ollama:{role}": prompt for role, prompt in ROLE_PROMPTS.items()}

_DEFAULT_OPTIONS = {"num_ctx": 4096}

# Cold-load resilience: an Ollama model that is still loading (or being swapped
# in on a memory-tight machine) can return HTTP 5xx or drop the stream with a
# ReadError before emitting any tokens. These are transient — the request is
# fully buffered and nothing is yielded until the stream completes, so it's safe
# to retry the whole call with backoff. Observed in Phase 4 (2026-07-26): 6/50
# qwen3.5 tasks failed exactly this way, all clustered at the first task after a
# model swap. 4xx and "Ollama not running" are NOT transient and fail fast.
_MAX_TRANSIENT_RETRIES = 2   # 3 attempts total
_RETRY_BASE_DELAY_S = 2.0    # 2s → 4s exponential backoff, covers a cold load


def _role_of(worker_id: str) -> str:
    """Extract trailing role segment from a worker_id like 'ollama:gemma4-e4b:coder'."""
    tail = worker_id.rsplit(":", 1)[-1]
    return tail if tail in ROLE_PROMPTS else "general"


class OllamaWorker(WorkerAdapter):
    """Streams responses from an Ollama model via /api/chat.

    Each worker instance binds a single (model, system_prompt) pair. Instances
    with the same model but different prompts are cheap — no shared state.
    Model-specific knobs (generation params, think toggle, context cap) are
    passed via `options` and `extra_payload` at construction time.
    """

    def __init__(
        self,
        model: str,
        worker_id: str,
        base_url: str = "http://localhost:11434",
        system_prompt: str | None = None,
        options: dict | None = None,
        extra_payload: dict | None = None,
        max_ctx: int | None = None,
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._base_url = base_url.rstrip("/")
        # Priority: explicit arg → legacy lookup by full id → role-derived → fallback.
        if system_prompt is not None:
            self._system_prompt = system_prompt
        elif worker_id in _SYSTEM_PROMPTS:
            self._system_prompt = _SYSTEM_PROMPTS[worker_id]
        else:
            self._system_prompt = ROLE_PROMPTS.get(_role_of(worker_id), "You are a helpful assistant.")
        self._options = {**_DEFAULT_OPTIONS, **(options or {})}
        self._extra_payload = extra_payload if extra_payload is not None else {"think": False}
        self._max_ctx = max_ctx
        self._role = _role_of(worker_id)

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["general", "code", "research"]

    @property
    def max_ctx(self) -> int | None:
        return self._max_ctx

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

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "options": self._options,
            **self._extra_payload,
        }

        full_response: list[str] = []
        _ollama_metrics: dict | None = None
        fail_event: WorkerEvent | None = None
        for attempt_no in range(_MAX_TRANSIENT_RETRIES + 1):
            full_response = []
            _ollama_metrics = None
            fail_event = None
            transient = False
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST", f"{self._base_url}/api/chat", json=payload,
                    ) as response:
                        if response.status_code != 200:
                            fail_event = WorkerEvent(
                                type="attempt.failed",
                                payload={
                                    "error_code": "http_error",
                                    "error": f"Ollama returned HTTP {response.status_code} for {self._model}",
                                },
                            )
                            transient = response.status_code >= 500  # 5xx = still loading
                        else:
                            async for line in response.aiter_lines():
                                if not line:
                                    continue
                                try:
                                    chunk = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                msg = chunk.get("message", {})
                                content = msg.get("content", "")
                                if content:
                                    full_response.append(content)
                                if chunk.get("done"):
                                    eval_count = chunk.get("eval_count", 0)
                                    eval_duration_ns = chunk.get("eval_duration", 0)
                                    eval_duration_s = eval_duration_ns / 1e9 if eval_duration_ns else 0.0
                                    tps = round(eval_count / eval_duration_s, 1) if eval_duration_s > 0 else 0.0
                                    _ollama_metrics = {
                                        "elapsed_s": round(eval_duration_s, 2),
                                        "tokens": eval_count,
                                        "throughput_tps": tps,
                                    }
                                    # Input side of the cost counterfactual: the done
                                    # chunk also carries prompt_eval_count/_duration.
                                    prompt_eval_count = chunk.get("prompt_eval_count", 0)
                                    if prompt_eval_count:
                                        prompt_eval_ns = chunk.get("prompt_eval_duration", 0)
                                        prompt_eval_s = prompt_eval_ns / 1e9 if prompt_eval_ns else 0.0
                                        _ollama_metrics["prompt_tokens"] = prompt_eval_count
                                        _ollama_metrics["prompt_eval_rate"] = (
                                            round(prompt_eval_count / prompt_eval_s, 1)
                                            if prompt_eval_s > 0 else 0.0
                                        )
                                    break
            except httpx.ConnectError:
                # Server not running — retrying won't help quickly; fail fast so
                # the "Ollama is down" signal isn't masked by backoff.
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={
                        "error_code": "ollama_unreachable",
                        "error": f"Ollama is not running at {self._base_url}",
                    },
                )
                return
            except (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                # Stream dropped mid-flight — classic cold-load / model-swap flake.
                fail_event = WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "stream_error", "error": f"[ERROR] {exc}"},
                )
                transient = True
            except Exception as exc:
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "stream_error", "error": f"[ERROR] {exc}"},
                )
                return

            if fail_event is None:
                break  # success — response fully buffered
            if transient and attempt_no < _MAX_TRANSIENT_RETRIES:
                delay = _RETRY_BASE_DELAY_S * (2 ** attempt_no)
                logger.warning(
                    "ollama %s transient failure (%s) — retry %d/%d in %.1fs",
                    self._model, fail_event.payload.get("error"),
                    attempt_no + 1, _MAX_TRANSIENT_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue
            # non-transient (4xx), or retries exhausted
            yield fail_event
            return

        # Strip any <think>...</think> reasoning chain. Applies regardless of
        # model — DeepSeek-R1 emits them unconditionally; others don't.
        summary = _THINK_TAG_RE.sub("", "".join(full_response)).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty response"},
            )
            return

        # Role-based postprocess — same semantics as before, just keyed on the
        # trailing role segment so multi-model worker_ids work.
        if self._role == "coder":
            summary = extract_code(summary)
        else:
            summary = strip_preamble(summary)

        logger.info("OLLAMA WORKER FINAL OUTPUT (first 200 chars): %s", summary[:200])
        if _ollama_metrics:
            yield WorkerEvent(type="metrics", payload=_ollama_metrics)
        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def warm(self) -> None:
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
