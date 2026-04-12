from __future__ import annotations
import json
import time
from typing import Optional
import aiosqlite
from ..domain.models import (
    Task, TaskStatus, Dependency,
    TaskAttempt, AttemptStatus,
)


class TaskStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ── Task ───────────────────────────────────────────────────────────────

    async def save(self, task: Task) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO tasks VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id, task.run_id, task.parent_task_id,
                task.title, task.goal,
                json.dumps(task.scope),
                json.dumps(task.context_refs),
                task.done_criteria,
                json.dumps([d.to_dict() for d in task.dependencies]),
                json.dumps(task.constraints),
                task.preferred_worker_type,
                json.dumps(task.required_capabilities),
                task.escalation_count, task.status.value,
                task.created_at, task.updated_at,
            ),
        )
        await self._conn.commit()

    async def get(self, task_id: str) -> Optional[Task]:
        async with self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    async def update_status(self, task_id: str, status: TaskStatus) -> None:
        await self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, time.time(), task_id),
        )
        await self._conn.commit()

    async def increment_escalation(self, task_id: str) -> None:
        await self._conn.execute(
            "UPDATE tasks SET escalation_count = escalation_count + 1, updated_at = ? WHERE id = ?",
            (time.time(), task_id),
        )
        await self._conn.commit()

    async def list_by_run(self, run_id: str) -> list[Task]:
        async with self._conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_task(r) for r in rows]

    async def list_by_status(self, status: TaskStatus) -> list[Task]:
        async with self._conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC",
            (status.value,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row) -> Task:
        return Task(
            id=row["id"], run_id=row["run_id"],
            parent_task_id=row["parent_task_id"],
            title=row["title"], goal=row["goal"],
            scope=json.loads(row["scope"]),
            context_refs=json.loads(row["context_refs"]),
            done_criteria=row["done_criteria"],
            dependencies=[
                Dependency.from_dict(d)
                for d in json.loads(row["dependencies"])
            ],
            constraints=json.loads(row["constraints"]),
            preferred_worker_type=row["preferred_worker_type"],
            required_capabilities=json.loads(row["required_capabilities"]),
            escalation_count=row["escalation_count"],
            status=TaskStatus(row["status"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ── TaskAttempt ────────────────────────────────────────────────────────

    async def save_attempt(self, attempt: TaskAttempt) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO task_attempts VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt.id, attempt.task_id, attempt.worker_id,
                attempt.status.value,
                attempt.error_code, attempt.blocking_reason,
                attempt.started_at, attempt.ended_at,
                attempt.summary,
                attempt.output,
                json.dumps(attempt.artifact_refs),
                json.dumps(attempt.validator_refs),
            ),
        )
        await self._conn.commit()

    async def get_attempt(self, attempt_id: str) -> Optional[TaskAttempt]:
        async with self._conn.execute(
            "SELECT * FROM task_attempts WHERE id = ?", (attempt_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_attempt(row)

    async def update_attempt_status(self, attempt_id: str, status: AttemptStatus) -> None:
        await self._conn.execute(
            "UPDATE task_attempts SET status = ? WHERE id = ?",
            (status.value, attempt_id),
        )
        await self._conn.commit()

    async def update_attempt_result(
        self,
        attempt_id: str,
        status: AttemptStatus,
        summary: str,
        artifact_refs: list[str] | None = None,
        error_code: str = "",
        blocking_reason: str = "",
    ) -> None:
        await self._conn.execute(
            """UPDATE task_attempts
               SET status = ?, summary = ?, artifact_refs = ?,
                   error_code = ?, blocking_reason = ?, ended_at = ?
               WHERE id = ?""",
            (
                status.value, summary,
                json.dumps(artifact_refs or []),
                error_code, blocking_reason,
                time.time(), attempt_id,
            ),
        )
        await self._conn.commit()

    async def list_attempts(self, task_id: str) -> list[TaskAttempt]:
        async with self._conn.execute(
            "SELECT * FROM task_attempts WHERE task_id = ? ORDER BY rowid ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_attempt(r) for r in rows]

    def _row_to_attempt(self, row) -> TaskAttempt:
        # Guard for legacy DB rows that pre-date the output column (DEFAULT '')
        keys = row.keys() if hasattr(row, "keys") else []
        return TaskAttempt(
            id=row["id"], task_id=row["task_id"], worker_id=row["worker_id"],
            status=AttemptStatus(row["status"]),
            error_code=row["error_code"],
            blocking_reason=row["blocking_reason"],
            started_at=row["started_at"], ended_at=row["ended_at"],
            summary=row["summary"],
            output=row["output"] if "output" in keys else "",
            artifact_refs=json.loads(row["artifact_refs"]),
            validator_refs=json.loads(row["validator_refs"]),
        )
