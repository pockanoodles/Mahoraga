from __future__ import annotations
import asyncio
import json
import time
from typing import AsyncIterator

import httpx

from backend.orchestrator_svc.models import Task, WorkerResult, Event

EXTENSION_URL = "http://localhost:11278"


class ExtensionAdapter:
    worker_id = "extension"
    display_name = "Ollama Extension Worker"

    def __init__(self, base_url: str = EXTENSION_URL) -> None:
        self._base_url = base_url
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, WorkerResult] = {}

    async def submit_task(self, task: Task) -> str:
        bg = asyncio.create_task(self._run_task(task))
        self._active_tasks[task.id] = bg
        return task.id

    async def _run_task(self, task: Task) -> None:
        tokens: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat",
                    json={
                        "message": task.goal,
                        "workspace": task.context.get("workspace", ""),
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                if data.get("type") == "token":
                                    tokens.append(data.get("content", ""))
                            except json.JSONDecodeError:
                                pass

            self._results[task.id] = WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                status="completed",
                summary="".join(tokens)[:4000],
                created_at=time.time(),
            )
        except Exception as exc:
            self._results[task.id] = WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                status="failed",
                summary=str(exc),
                created_at=time.time(),
            )

    async def stream_events(self, task_id: str) -> AsyncIterator[Event]:
        for _ in range(300):
            if task_id in self._results:
                result = self._results[task_id]
                event_type = "task.completed" if result.status == "completed" else "task.failed"
                yield Event(
                    type=event_type,
                    task_id=task_id,
                    worker_id=self.worker_id,
                    content={"summary": result.summary},
                )
                return
            await asyncio.sleep(1)
        yield Event(
            type="task.failed",
            task_id=task_id,
            worker_id=self.worker_id,
            content={"error": "timeout after 300s"},
        )

    async def get_result(self, task_id: str) -> WorkerResult:
        if task_id not in self._results:
            raise RuntimeError(f"Result for task '{task_id}' not ready")
        return self._results[task_id]

    async def cancel_task(self, task_id: str) -> None:
        bg = self._active_tasks.get(task_id)
        if bg and not bg.done():
            bg.cancel()

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                if resp.status_code == 200:
                    return {"status": "ok", "worker_id": self.worker_id}
                return {"status": "degraded", "http_status": resp.status_code}
        except Exception as exc:
            return {"status": "down", "worker_id": self.worker_id, "error": str(exc)}
