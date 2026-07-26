"""ClaudeCliWorker — subprocess-based WorkerAdapter for the Claude Code CLI.

Runs `claude -p <prompt> --output-format json --model <model>` in
non-interactive print mode. Auth comes from the local Claude subscription
login (`claude login`) — no ANTHROPIC_API_KEY needed. The variable is
stripped from the subprocess env so a stale/dummy key from .env can never
shadow subscription auth.

Retry/feedback: the CLI's `--resume <session_id>` was considered but rejected —
it couples retries to CLI session state on disk, which can be cleared between
attempts. Instead retries rebuild the prompt inline with the prior output and
verifier feedback (same information, stateless, robust).

Telemetry: on success a `metrics` WorkerEvent is emitted with the same payload
field names OllamaWorker uses (`elapsed_s`, `tokens`, `throughput_tps`) plus
`prompt_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_usd`, and
`model` — the executor side-channel carries these to the recording path in
app.py/gateway unchanged. Cost prefers the CLI's authoritative
`total_cost_usd`; falls back to calculate_cost() from token counts, then 0.0.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt
from ..domain.models import Task, TaskAttempt
from ..tracking.pricing import calculate_cost

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300  # seconds — cloud call incl. cold session-cache write


class ClaudeCliWorker(WorkerAdapter):
    """Cloud arm via the Claude Code CLI (Max subscription, no API key)."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude-cli:sonnet",
        binary_path: str = "claude",
        timeout: float = _DEFAULT_TIMEOUT,
        cwd: str | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._binary = binary_path
        self._timeout = timeout
        self._cwd = cwd
        self._capabilities = capabilities or ["general", "code", "plan", "deep_reasoning"]
        # Per-task last output so retries can inline prior attempt + feedback
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
        binary = shutil.which(self._binary) or self._binary
        prompt = _build_prompt(task)
        if feedback:
            prior = self._last_output.get(task.id, "")
            if prior:
                prompt += f"\n\n## Previous Attempt Output\n{prior}"
            prompt += f"\n\n## Feedback on Previous Attempt\n{feedback}"

        cmd = [binary, "-p", prompt, "--output-format", "json", "--model", self._model]
        # Subscription auth only — never let an env API key shadow it.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=env,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": f"claude binary not found at {self._binary!r}. Install: npm install -g @anthropic-ai/claude-code"},
            )
            return

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"claude timed out after {self._timeout}s"},
            )
            return

        if proc.returncode != 0:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "nonzero_exit",
                    "error": f"claude exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        try:
            result = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "malformed_json", "error": f"claude output was not valid JSON: {exc}"},
            )
            return

        if not isinstance(result, dict) or result.get("is_error") or result.get("subtype", "success") != "success":
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "cli_error",
                    "error": f"claude reported an error result: {str(result)[:200]}",
                },
            )
            return

        summary = (result.get("result") or "").strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "claude returned empty result"},
            )
            return

        self._last_output[task.id] = summary
        yield WorkerEvent(type="metrics", payload=self._telemetry(result))
        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    def _telemetry(self, result: dict) -> dict:
        """Extract token/cost telemetry from a CLI result object, defensively."""
        usage = result.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)

        # The CLI resolves aliases ("sonnet" → "claude-sonnet-5"); prefer the
        # resolved name from modelUsage over the constructor-provided string.
        model = self._model
        model_usage = result.get("modelUsage") or {}
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            model = next(iter(model_usage))

        cost_usd = result.get("total_cost_usd")
        if cost_usd is None:
            if input_tokens or output_tokens:
                cost_usd = calculate_cost(model, input_tokens, output_tokens, cache_read_tokens)
            else:
                cost_usd = 0.0

        elapsed_s = (result.get("duration_ms") or 0) / 1000
        tps = round(output_tokens / elapsed_s, 1) if elapsed_s > 0 and output_tokens else 0.0

        return {
            "elapsed_s": round(elapsed_s, 2),
            "tokens": output_tokens,
            "throughput_tps": tps,
            "prompt_tokens": input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cost_usd": float(cost_usd),
            "model": model,
        }

    def clear_history(self, task_id: str) -> None:
        """Clear per-task retry context after the task reaches terminal state."""
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="claude not found in PATH. Install: npm install -g @anthropic-ai/claude-code",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
