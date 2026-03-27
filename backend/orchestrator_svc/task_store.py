from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import Task, WorkerResult, Event

_DEFAULT_DB = Path.home() / ".ollama-runtime" / "orchestrator.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    task_type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_worker TEXT,
    context TEXT NOT NULL DEFAULT '{}',
    constraints TEXT NOT NULL DEFAULT '[]',
    artifacts TEXT NOT NULL DEFAULT '[]',
    validator_profile TEXT NOT NULL DEFAULT '[]',
    escalation_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worker_id TEXT,
    content TEXT NOT NULL DEFAULT '{}',
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task_id_ts ON events(task_id, ts);

CREATE TABLE IF NOT EXISTS results (
    task_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    artifacts TEXT NOT NULL DEFAULT '[]',
    validator_results TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
"""


class TaskStore:
    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def save_task(self, task: Task) -> None:
        task.updated_at = time.time()
        await self._conn.execute(
            """INSERT OR REPLACE INTO tasks VALUES (
                :id, :parent_id, :title, :goal, :task_type, :priority, :status,
                :assigned_worker, :context, :constraints, :artifacts,
                :validator_profile, :escalation_count, :created_at, :updated_at
            )""",
            {
                "id": task.id,
                "parent_id": task.parent_id,
                "title": task.title,
                "goal": task.goal,
                "task_type": task.task_type,
                "priority": task.priority,
                "status": task.status,
                "assigned_worker": task.assigned_worker,
                "context": json.dumps(task.context),
                "constraints": json.dumps(task.constraints),
                "artifacts": json.dumps(task.artifacts),
                "validator_profile": json.dumps(task.validator_profile),
                "escalation_count": task.escalation_count,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
        )
        await self._conn.commit()

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row:
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
            context=json.loads(row["context"]),
            constraints=json.loads(row["constraints"]),
            artifacts=json.loads(row["artifacts"]),
            validator_profile=json.loads(row["validator_profile"]),
            escalation_count=row["escalation_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_task_status(
        self, task_id: str, status: str, assigned_worker: str | None = None
    ) -> None:
        if assigned_worker is not None:
            await self._conn.execute(
                "UPDATE tasks SET status=?, assigned_worker=?, updated_at=? WHERE id=?",
                (status, assigned_worker, time.time(), task_id),
            )
        else:
            await self._conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, time.time(), task_id),
            )
        await self._conn.commit()

    async def increment_escalation(self, task_id: str) -> None:
        await self._conn.execute(
            "UPDATE tasks SET escalation_count = escalation_count + 1, updated_at=? WHERE id=?",
            (time.time(), task_id),
        )
        await self._conn.commit()

    async def log_event(self, event: Event) -> None:
        event_id = str(uuid.uuid4())
        await self._conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, event.type, event.task_id, event.worker_id,
             json.dumps(event.content), event.ts),
        )
        await self._conn.commit()

    async def get_events(self, task_id: str) -> list[Event]:
        async with self._conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY ts ASC", (task_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [
            Event(
                type=row["type"],
                task_id=row["task_id"],
                worker_id=row["worker_id"],
                content=json.loads(row["content"]),
                ts=row["ts"],
            )
            for row in rows
        ]

    async def save_result(self, result: WorkerResult) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.task_id,
                result.worker_id,
                result.status,
                result.summary,
                json.dumps(result.artifacts),
                json.dumps(result.validator_results),
                result.created_at,
            ),
        )
        await self._conn.commit()

    async def get_result(self, task_id: str) -> Optional[WorkerResult]:
        async with self._conn.execute(
            "SELECT * FROM results WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return WorkerResult(
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            status=row["status"],
            summary=row["summary"],
            artifacts=json.loads(row["artifacts"]),
            validator_results=json.loads(row["validator_results"]),
            created_at=row["created_at"],
        )

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        if status:
            async with self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ) as cur:
                rows = await cur.fetchall()
        return [
            Task(
                id=row["id"],
                parent_id=row["parent_id"],
                title=row["title"],
                goal=row["goal"],
                task_type=row["task_type"],
                priority=row["priority"],
                status=row["status"],
                assigned_worker=row["assigned_worker"],
                context=json.loads(row["context"]),
                constraints=json.loads(row["constraints"]),
                artifacts=json.loads(row["artifacts"]),
                validator_profile=json.loads(row["validator_profile"]),
                escalation_count=row["escalation_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
