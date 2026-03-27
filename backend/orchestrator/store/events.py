from __future__ import annotations
import json
import aiosqlite
from ..domain.models import Event


class EventStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def append(self, event: Event) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.id, event.run_id, event.task_id, event.attempt_id,
                event.type, json.dumps(event.payload), event.ts,
            ),
        )
        await self._conn.commit()

    async def list_by_run(self, run_id: str) -> list[Event]:
        async with self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY ts ASC", (run_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def list_by_task(self, task_id: str) -> list[Event]:
        async with self._conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY ts ASC", (task_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def list_by_type(self, run_id: str, event_type: str) -> list[Event]:
        async with self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? AND type = ? ORDER BY ts ASC",
            (run_id, event_type),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row) -> Event:
        return Event(
            id=row["id"], run_id=row["run_id"],
            task_id=row["task_id"], attempt_id=row["attempt_id"],
            type=row["type"],
            payload=json.loads(row["payload"]),
            ts=row["ts"],
        )
