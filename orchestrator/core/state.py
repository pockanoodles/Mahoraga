from __future__ import annotations
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .models import Task, Event


class StateStore:
    def __init__(self, db_path: str = "~/.ollama-runtime/orchestrator.db"):
        self._db_path = str(Path(db_path).expanduser()) if db_path != ":memory:" else db_path
        # For :memory: databases we must reuse a single connection
        self._conn: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._conn is not None:
            yield self._conn
        else:
            async with aiosqlite.connect(self._db_path) as db:
                yield db

    async def init(self) -> None:
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            # Open and hold a persistent connection for in-memory DB
            self._conn = await aiosqlite.connect(self._db_path)

        async with self._connect() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_worker TEXT,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    validator_profile_json TEXT NOT NULL DEFAULT '[]',
                    escalation_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    worker_id TEXT,
                    content_json TEXT NOT NULL DEFAULT '{}',
                    ts REAL NOT NULL
                )
            """)
            await db.commit()

    async def save_task(self, task: Task) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO tasks (
                    id, parent_id, title, goal, task_type, priority, status,
                    assigned_worker, context_json, constraints_json,
                    artifacts_json, validator_profile_json, escalation_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id, task.parent_id, task.title, task.goal,
                    task.task_type, task.priority, task.status,
                    task.assigned_worker,
                    json.dumps(task.context),
                    json.dumps(task.constraints),
                    json.dumps(task.artifacts),
                    json.dumps(task.validator_profile),
                    task.escalation_count,
                ),
            )
            await db.commit()

    async def get_task(self, task_id: str) -> Task | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return Task(
            id=row["id"],
            parent_id=row["parent_id"],
            title=row["title"],
            goal=row["goal"],
            task_type=row["task_type"],
            priority=row["priority"],
            status=row["status"],
            assigned_worker=row["assigned_worker"],
            context=json.loads(row["context_json"]),
            constraints=json.loads(row["constraints_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            validator_profile=json.loads(row["validator_profile_json"]),
            escalation_count=row["escalation_count"],
        )

    async def update_task(self, task_id: str, **fields) -> None:
        """Update arbitrary task fields. JSON-serializes list/dict values."""
        column_map = {
            "status": "status",
            "assigned_worker": "assigned_worker",
            "escalation_count": "escalation_count",
            "priority": "priority",
            "context": "context_json",
            "constraints": "constraints_json",
            "artifacts": "artifacts_json",
            "validator_profile": "validator_profile_json",
        }
        sets = []
        values = []
        for key, val in fields.items():
            col = column_map.get(key)
            if col is None:
                raise ValueError(f"Unknown task field: {key!r}")
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            sets.append(f"{col} = ?")
            values.append(val)
        if not sets:
            return
        values.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
        async with self._connect() as db:
            await db.execute(sql, values)
            await db.commit()

    async def log_event(self, event: Event) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO events (task_id, event_type, worker_id, content_json, ts) VALUES (?, ?, ?, ?, ?)",
                (event.task_id, event.event_type, event.worker_id, json.dumps(event.content), event.ts),
            )
            await db.commit()

    async def get_events(self, task_id: str) -> list[Event]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY id ASC", (task_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            Event(
                event_type=row["event_type"],
                task_id=row["task_id"],
                worker_id=row["worker_id"],
                content=json.loads(row["content_json"]),
                ts=row["ts"],
            )
            for row in rows
        ]
