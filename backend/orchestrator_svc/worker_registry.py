from __future__ import annotations
from .worker_adapter import WorkerAdapter


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerAdapter] = {}

    def register(self, adapter: WorkerAdapter) -> None:
        self._workers[adapter.worker_id] = adapter

    def get(self, worker_id: str) -> WorkerAdapter:
        if worker_id not in self._workers:
            raise KeyError(f"Worker '{worker_id}' not registered")
        return self._workers[worker_id]

    def list_workers(self) -> list[str]:
        return list(self._workers.keys())

    async def health_all(self) -> dict[str, dict]:
        results: dict[str, dict] = {}
        for wid, adapter in self._workers.items():
            try:
                results[wid] = await adapter.health()
            except Exception as exc:
                results[wid] = {"status": "down", "error": str(exc)}
        return results
