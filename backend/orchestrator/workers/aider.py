"""AiderWorker — subprocess-based WorkerAdapter for Aider CLI.

Requirements: pip install aider-install && aider-install
Spawns `aider` with --yes-always for non-interactive execution.
Can use Ollama for free local inference: model="ollama_chat/qwen3:4b"
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


class AiderWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "aider:default",
        binary_path: str = "aider",
        model: str = "ollama_chat/qwen3:4b",
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
        return ["code", "refactor", "test", "explain"]

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

        cmd = [
            binary,
            "--yes-always",
            "--no-git",
            "--model", self._model,
            "--message", message,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": "aider binary not found. Install: pip install aider-install && aider-install"},
            )
            return

        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace")
                    if not text.startswith("Aider") and not text.strip().startswith(">"):
                        collected.append(text)
                await proc.wait()
        except TimeoutError:
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"aider timed out after {self._timeout}s"},
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": str(exc)},
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
                payload={"error_code": "empty_response", "error": f"aider produced no output. stderr: {stderr_text}"},
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
                detail="aider not found in PATH. Install: pip install aider-install && aider-install",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}, model={self._model}")
