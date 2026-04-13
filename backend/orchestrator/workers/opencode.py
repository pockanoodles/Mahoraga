"""OpenCodeWorker — subprocess-based WorkerAdapter for OpenCode CLI.

Anomaly's open-source coding agent. Supports any OpenAI-compatible API including Ollama.
Spawns `opencode -p <prompt> -c <cwd> -q` in non-interactive mode.
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
_OPENCODE_BINARY = "opencode"


class OpenCodeWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "opencode:default",
        binary_path: str = _OPENCODE_BINARY,
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
            prompt += f"\n\nFeedback: {feedback}"

        cmd = [binary, "-p", prompt, "-q"]   # -q hides spinner in non-interactive mode
        if self._cwd:
            cmd += ["-c", self._cwd]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": f"opencode binary not found at {self._binary!r}. Install from https://github.com/sst/opencode"},
            )
            return

        # Fast-fail: if process dies within 5s without output, abort early
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
                payload={"error_code": "fast_fail", "error": f"opencode exited immediately ({proc.returncode}): {stderr.decode(errors='replace')[:300]}"},
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
                payload={"error_code": "timeout", "error": f"opencode timed out after {self._timeout}s"},
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
                payload={"error_code": "nonzero_exit", "error": f"opencode exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}"},
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "opencode produced no output"},
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
                detail=f"opencode not found in PATH. Install from https://github.com/sst/opencode",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
