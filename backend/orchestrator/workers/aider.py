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
        cwd: str | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._model = model
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
                cwd=self._cwd,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": "aider binary not found. Install: pip install aider-install && aider-install"},
            )
            return

        # Fast-fail: if aider dies within 5s without producing output, it's a startup error
        # (model not found, auth failure, etc.) — bail immediately instead of waiting 180s.
        first_line: bytes | None = None
        try:
            first_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        if first_line is None and proc.returncode is not None and proc.returncode != 0:
            stderr_text = ""
            if proc.stderr:
                stderr_bytes = await proc.stderr.read(4096)
                stderr_text = stderr_bytes.decode(errors="replace")[:300]
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "fast_fail", "error": f"aider exited immediately ({proc.returncode}): {stderr_text}"},
            )
            return

        collected: list[str] = []
        if first_line:
            text = first_line.decode("utf-8", errors="replace")
            if not text.startswith("Aider") and not text.strip().startswith(">"):
                collected.append(text)

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
        # Tier 1: binary exists
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="aider not found in PATH. Install: pip install aider-install && aider-install",
            )
        # Tier 2: verify Ollama has the model aider needs
        # Strip the "ollama_chat/" prefix to get the bare Ollama model name
        if "ollama" in self._model.lower():
            ollama_model = self._model.replace("ollama_chat/", "").replace("ollama/", "")
            try:
                import httpx
                resp = await httpx.AsyncClient().get(
                    "http://localhost:11434/api/tags", timeout=3.0
                )
                if resp.status_code == 200:
                    model_names = [m["name"] for m in resp.json().get("models", [])]
                    model_base = ollama_model.split(":")[0]
                    if not any(m.startswith(model_base) for m in model_names):
                        return WorkerHealth(
                            worker_id=self._worker_id,
                            healthy=False,
                            detail=f"model {ollama_model!r} not found in Ollama — run: ollama pull {ollama_model}",
                        )
            except Exception as exc:
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail=f"could not reach Ollama to verify model: {exc}",
                )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}, model={self._model}")
