from __future__ import annotations
import json
import time
from typing import Optional
import aiosqlite
from ..domain.models import Mission, MissionStatus, Plan, PlanStatus, Run, RunMode, RunStatus


class MissionStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ── Mission ────────────────────────────────────────────────────────────

    async def save(self, mission: Mission) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO missions VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mission.id, mission.title, mission.goal, mission.background,
                mission.success_condition,
                json.dumps(mission.context_refs),
                json.dumps(mission.global_constraints),
                json.dumps(mission.preferences),
                mission.status.value,
                mission.created_at, mission.updated_at,
            ),
        )
        await self._conn.commit()

    async def get(self, mission_id: str) -> Optional[Mission]:
        async with self._conn.execute(
            "SELECT * FROM missions WHERE id = ?", (mission_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Mission(
            id=row["id"], title=row["title"], goal=row["goal"],
            background=row["background"],
            success_condition=row["success_condition"],
            context_refs=json.loads(row["context_refs"]),
            global_constraints=json.loads(row["global_constraints"]),
            preferences=json.loads(row["preferences"]),
            status=MissionStatus(row["status"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    async def update_status(self, mission_id: str, status: MissionStatus) -> None:
        await self._conn.execute(
            "UPDATE missions SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, time.time(), mission_id),
        )
        await self._conn.commit()

    async def list(self) -> list[Mission]:
        async with self._conn.execute(
            "SELECT * FROM missions ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Mission(
                id=r["id"], title=r["title"], goal=r["goal"],
                background=r["background"],
                success_condition=r["success_condition"],
                context_refs=json.loads(r["context_refs"]),
                global_constraints=json.loads(r["global_constraints"]),
                preferences=json.loads(r["preferences"]),
                status=MissionStatus(r["status"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ── Plan ───────────────────────────────────────────────────────────────

    async def save_plan(self, plan: Plan) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.id, plan.mission_id, plan.version,
                json.dumps(plan.phases),
                json.dumps(plan.worker_strategy),
                json.dumps(plan.validation_strategy),
                plan.task_graph_shape, plan.status.value, plan.created_at,
            ),
        )
        await self._conn.commit()

    async def get_plan(self, plan_id: str) -> Optional[Plan]:
        async with self._conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Plan(
            id=row["id"], mission_id=row["mission_id"], version=row["version"],
            phases=json.loads(row["phases"]),
            worker_strategy=json.loads(row["worker_strategy"]),
            validation_strategy=json.loads(row["validation_strategy"]),
            task_graph_shape=row["task_graph_shape"],
            status=PlanStatus(row["status"]), created_at=row["created_at"],
        )

    async def update_plan_status(self, plan_id: str, status: PlanStatus) -> None:
        await self._conn.execute(
            "UPDATE plans SET status = ? WHERE id = ?", (status.value, plan_id)
        )
        await self._conn.commit()

    async def list_plans(self, mission_id: str) -> list[Plan]:
        async with self._conn.execute(
            "SELECT * FROM plans WHERE mission_id = ? ORDER BY created_at DESC",
            (mission_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Plan(
                id=r["id"], mission_id=r["mission_id"], version=r["version"],
                phases=json.loads(r["phases"]),
                worker_strategy=json.loads(r["worker_strategy"]),
                validation_strategy=json.loads(r["validation_strategy"]),
                task_graph_shape=r["task_graph_shape"],
                status=PlanStatus(r["status"]), created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── Run ────────────────────────────────────────────────────────────────

    async def save_run(self, run: Run) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run.id, run.mission_id, run.plan_id,
                run.mode.value, run.status.value,
                run.created_at, run.updated_at,
            ),
        )
        await self._conn.commit()

    async def get_run(self, run_id: str) -> Optional[Run]:
        async with self._conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Run(
            id=row["id"], mission_id=row["mission_id"], plan_id=row["plan_id"],
            mode=RunMode(row["mode"]), status=RunStatus(row["status"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    async def update_run_status(self, run_id: str, status: RunStatus) -> None:
        await self._conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, time.time(), run_id),
        )
        await self._conn.commit()

    async def list_runs(self, mission_id: str) -> list[Run]:
        async with self._conn.execute(
            "SELECT * FROM runs WHERE mission_id = ? ORDER BY created_at DESC",
            (mission_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Run(
                id=r["id"], mission_id=r["mission_id"], plan_id=r["plan_id"],
                mode=RunMode(r["mode"]), status=RunStatus(r["status"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def list_all_runs(self) -> list[Run]:
        async with self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Run(
                id=r["id"], mission_id=r["mission_id"], plan_id=r["plan_id"],
                mode=RunMode(r["mode"]), status=RunStatus(r["status"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]
