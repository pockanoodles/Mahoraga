from __future__ import annotations
import json
from typing import Optional
import aiosqlite
from ..domain.models import Artifact


class ArtifactStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save(self, artifact: Artifact) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.id, artifact.run_id, artifact.task_id,
                artifact.attempt_id, artifact.type,
                json.dumps(artifact.location), artifact.created_at,
            ),
        )
        await self._conn.commit()

    async def get(self, artifact_id: str) -> Optional[Artifact]:
        async with self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_artifact(row)

    async def list_by_task(self, task_id: str) -> list[Artifact]:
        async with self._conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_artifact(r) for r in rows]

    async def list_by_run(self, run_id: str) -> list[Artifact]:
        async with self._conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def _row_to_artifact(self, row) -> Artifact:
        return Artifact(
            id=row["id"], run_id=row["run_id"],
            task_id=row["task_id"], attempt_id=row["attempt_id"],
            type=row["type"],
            location=json.loads(row["location"]),
            created_at=row["created_at"],
        )
