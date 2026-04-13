"""GeminiWorker — subprocess-based WorkerAdapter for Gemini CLI.

Requirements: npm install -g @google/gemini-cli
              gemini auth login  (one-time Google OAuth, or set GEMINI_API_KEY env var)
Free tier: 60 requests/minute, 1000 requests/day.

NOTE: Verify non-interactive flags with `gemini --help` after installation.
The -p flag follows the same convention as Claude Code and OpenCode.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120


class GeminiWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "gemini:cli",
        binary_path: str = "gemini",
        model: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._model = model
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["code", "research", "explain", "general"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        message = f"{task.title}\n\n{task.goal}"
        if task.done_criteria:
            message += f"\n\nDone when: {task.done_criteria}"
        if feedback:
            message += f"\n\nFeedback: {feedback}"

        # -p: non-interactive prompt flag (same convention as Claude Code / OpenCode)
        # If `gemini --help` shows a different flag (e.g. --prompt), update here.
        cmd = [binary, "-p", message]
        if self._model:
            cmd.extend(["--model", self._model])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "binary_not_found",
                    "error": "gemini binary not found. Install: npm install -g @google/gemini-cli",
                },
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
            await proc.wait()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"gemini timed out after {self._timeout}s"},
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
                    "error": f"gemini exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            stderr_text = ""
            if proc.stderr:
                stderr_bytes = await proc.stderr.read(4096)
                stderr_text = stderr_bytes.decode(errors="replace")[:300]
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": f"gemini produced no output. stderr: {stderr_text}"},
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
                detail="gemini not found in PATH. Install: npm install -g @google/gemini-cli",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}, model={self._model or 'auto'}")
