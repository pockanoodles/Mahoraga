from __future__ import annotations
import asyncio
from .models import Event
from .task_store import TaskStore


class EventBus:
    def __init__(self, store: TaskStore) -> None:
        self._store = store
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}

    async def publish(self, event: Event) -> None:
        await self._store.log_event(event)
        for q in self._subscribers.get(event.task_id, []):
            await q.put(event)

    async def subscribe(self, task_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue[Event]) -> None:
        queues = self._subscribers.get(task_id, [])
        if q in queues:
            queues.remove(q)
