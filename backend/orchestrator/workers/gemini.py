"""GeminiWorker — subprocess-based WorkerAdapter for Gemini CLI.

Google's open-source terminal coding agent.
Spawns `gemini -p <prompt> -y --approval-mode yolo` in headless mode.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 180


class GeminiWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "gemini:cli",
        binary_path: str = "gemini",
        timeout: int = _DEFAULT_TIMEOUT,
        cwd: str | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._timeout = timeout
        self._cwd = cwd

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["code", "refactor", "test", "explain", "general"]

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
            prompt += f"\n\nFeedback: {feedback}"

        cmd = [
            binary,
            "--prompt", prompt,
            "-y",                        # auto-accept all actions
            "--approval-mode", "yolo",   # non-interactive, no confirmations
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": "gemini binary not found. Install: npm install -g @google/gemini-cli"},
            )
            return

        # Fast-fail: if process dies within 5s without producing output, abort early
        first_line: bytes | None = None
        try:
            first_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        if first_line is None and proc.returncode is not None and proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read(4096)
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "fast_fail", "error": f"gemini exited immediately ({proc.returncode}): {stderr.decode(errors='replace')[:300]}"},
            )
            return

        collected: list[str] = []
        if first_line:
            collected.append(first_line.decode("utf-8", errors="replace"))

        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    collected.append(line.decode("utf-8", errors="replace"))
                await proc.wait()
        except TimeoutError:
            proc.kill()
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
                payload={"error_code": "nonzero_exit", "error": f"gemini exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}"},
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "gemini produced no output"},
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
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
