"""GooseWorker — subprocess-based WorkerAdapter for Goose CLI (Block/Square).

Requirements: brew install goose
              OR: curl -fsSL https://github.com/block/goose/releases/latest/download/install.sh | bash
General-purpose agent — not code-specific. Best for research, writing, automation.

NOTE: Goose's CLI is actively evolving. Verify `goose run` syntax with `goose --help`.
Some versions may use `goose session --non-interactive` instead.
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


class GooseWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "goose:default",
        binary_path: str = "goose",
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
        return ["research", "general", "explain"]

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

        # `goose run` is the non-interactive single-shot mode.
        # If this fails, try: goose session --non-interactive --prompt "..."
        cmd = [binary, "run", message]

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
                    "error": "goose binary not found. Install: brew install goose",
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
                payload={"error_code": "timeout", "error": f"goose timed out after {self._timeout}s"},
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
                    "error": f"goose exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
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
                payload={"error_code": "empty_response", "error": f"goose produced no output. stderr: {stderr_text}"},
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
                detail="goose not found in PATH. Install: brew install goose",
            )

        # Verify this is the Block AI Goose agent, not the Pressly DB migration tool.
        # The DB migration tool prints keywords like "postgres", "mysql", "sqlite3",
        # "driver", or "migration" in its version output.
        _DB_MIGRATION_KEYWORDS = {"postgres", "mysql", "sqlite3", "driver", "migration"}
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    binary, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=3.0,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            version_output = (stdout + stderr).decode("utf-8", errors="replace").lower()
            if not version_output.strip():
                raise ValueError("empty version output")
            if any(kw in version_output for kw in _DB_MIGRATION_KEYWORDS):
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail=(
                        "goose binary is a DB migration tool, not the Block AI agent. "
                        "See https://github.com/block/goose for install instructions."
                    ),
                )
        except Exception as exc:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail=(
                    "goose binary is a DB migration tool, not the Block AI agent. "
                    "See https://github.com/block/goose for install instructions."
                ),
            )

        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
