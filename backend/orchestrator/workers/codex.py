"""CodexWorker — subprocess-based WorkerAdapter for OpenAI Codex CLI.

Requirements: npm install -g @openai/codex (or ChatGPT Plus auth).
Spawns `codex` as a subprocess, streams stdout as token events.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 180  # seconds


class CodexWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "codex:cli",
        binary_path: str = "codex",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["code", "refactor", "test", "explain"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        prompt = f"{task.title}\n\n{task.goal}"
        if task.done_criteria:
            prompt += f"\n\nDone when: {task.done_criteria}"
        if feedback:
            prompt += f"\n\nPrevious attempt feedback: {feedback}"

        cmd = [binary, "--approval-mode", "full-auto", "--quiet", prompt]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": f"codex binary not found at {self._binary!r}. Install: npm install -g @openai/codex"},
            )
            return

        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace")
                    collected.append(text)
                await proc.wait()
        except TimeoutError:
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"codex timed out after {self._timeout}s"},
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": str(exc)},
            )
            return

        if proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "nonzero_exit",
                    "error": f"codex exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "codex produced no output"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail=f"codex not found in PATH. Install: npm install -g @openai/codex",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
