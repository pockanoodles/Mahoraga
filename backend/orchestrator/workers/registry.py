from __future__ import annotations
import asyncio
from .base import WorkerAdapter, WorkerHealth


class WorkerNotFoundError(KeyError):
    pass


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerAdapter] = {}

    def register(self, worker: WorkerAdapter) -> None:
        self._workers[worker.id] = worker

    def get(self, worker_id: str) -> WorkerAdapter:
        try:
            return self._workers[worker_id]
        except KeyError:
            raise WorkerNotFoundError(f"Worker {worker_id!r} not registered")

    def list_all(self) -> list[WorkerAdapter]:
        return list(self._workers.values())

    async def health_all(self) -> dict[str, WorkerHealth]:
        workers = list(self._workers.values())
        results = await asyncio.gather(*(w.health() for w in workers))
        return {w.id: r for w, r in zip(workers, results)}
