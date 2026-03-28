from __future__ import annotations
import asyncio
from typing import AsyncGenerator

import httpx

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth

_POLL_INTERVAL = 2.0


class ExtensionWorker(WorkerAdapter):
    """Worker backed by the VS Code extension over HTTP with status polling."""

    _id = "extension"
    _capabilities = ["file_editing", "cheap_repetitive"]

    def __init__(self, base_url: str = "http://localhost:3000") -> None:
        self._base_url = base_url.rstrip("/")

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    async def execute(self, attempt: TaskAttempt, task: Task) -> AsyncGenerator[WorkerEvent, None]:
        payload = {
            "attempt_id": attempt.id,
            "task_id": task.id,
            "title": task.title,
            "goal": task.goal,
            "context_refs": task.context_refs,
            "constraints": task.constraints,
            "done_criteria": task.done_criteria,
        }
        client = httpx.AsyncClient(base_url=self._base_url, timeout=300.0)
        try:
            try:
                resp = await client.post("/execute", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "http_error", "error": str(exc)},
                )
                return

            yield WorkerEvent(type="attempt.started", payload={})

            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    status_resp = await client.get(f"/execute/{attempt.id}/status")
                    status_resp.raise_for_status()
                    data = status_resp.json()
                except httpx.HTTPError as exc:
                    yield WorkerEvent(
                        type="attempt.failed",
                        payload={"error_code": "poll_error", "error": str(exc)},
                    )
                    return

                status = data.get("status")
                if status == "completed":
                    yield WorkerEvent(
                        type="attempt.completed",
                        payload={"summary": data.get("summary", "")},
                    )
                    return
                elif status == "failed":
                    yield WorkerEvent(
                        type="attempt.failed",
                        payload={
                            "error_code": data.get("error_code", ""),
                            "error": data.get("error", ""),
                        },
                    )
                    return
                elif status == "blocked":
                    yield WorkerEvent(
                        type="attempt.blocked",
                        payload={"reason": data.get("reason", "")},
                    )
                    return
                elif status not in ("running", "pending"):
                    yield WorkerEvent(
                        type="attempt.failed",
                        payload={
                            "error_code": "unknown_status",
                            "error": f"Unknown poll status: {status!r}",
                        },
                    )
                    return
                # status in ("running", "pending") — keep polling
        finally:
            await client.aclose()

    async def cancel(self, attempt_id: str) -> None:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=10.0) as client:
            try:
                await client.delete(f"/execute/{attempt_id}")
            except httpx.HTTPError:
                pass

    async def health(self) -> WorkerHealth:
        client = httpx.AsyncClient(base_url=self._base_url, timeout=5.0)
        try:
            resp = await client.get("/health")
            resp.raise_for_status()
            return WorkerHealth(worker_id=self.id, healthy=True)
        except httpx.HTTPError as exc:
            return WorkerHealth(worker_id=self.id, healthy=False, detail=str(exc))
        finally:
            await client.aclose()
