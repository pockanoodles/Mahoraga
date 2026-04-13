"""GooseWorker — subprocess-based WorkerAdapter for Block's Goose AI agent.

Block's open-source AI agent for automating engineering tasks.
NOTE: requires Block's AI agent (github.com/block/goose), NOT the goose DB migration tool.
Install: brew install block/goose/goose  or  pipx install goose-ai

Health check verifies this is the AI agent (not the DB migration tool) by testing
that `goose run --help` exits cleanly.
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

        # Block's Goose: `goose run --text <prompt>` for non-interactive execution
        cmd = [binary, "run", "--text", prompt]

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
                payload={"error_code": "binary_not_found", "error": "goose binary not found. Install Block's AI agent from github.com/block/goose"},
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
                payload={"error_code": "fast_fail", "error": f"goose exited immediately ({proc.returncode}): {stderr.decode(errors='replace')[:300]}"},
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
                payload={"error_code": "nonzero_exit", "error": f"goose exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}"},
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "goose produced no output"},
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
                detail="goose not found in PATH. Install Block's AI agent: github.com/block/goose",
            )
        # Verify this is Block's AI goose, not the DB migration tool.
        # The AI goose has `goose run` as a valid subcommand; the DB tool does not.
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "run", "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail=f"goose binary at {binary} does not support 'run' subcommand — may be DB migration tool, not Block's AI agent",
                )
        except Exception as exc:
            return WorkerHealth(worker_id=self._worker_id, healthy=False, detail=str(exc))

        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
