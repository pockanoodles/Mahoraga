from __future__ import annotations
import time
import aiosqlite


def _now() -> str:
    return str(time.time())


class EvalStore:
    """Stores A/B evaluation runs and per-task results."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS routing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                routing_enabled INTEGER NOT NULL DEFAULT 1,
                baseline_policy TEXT,
                task_suite_name TEXT NOT NULL DEFAULT '',
                repeat_index INTEGER NOT NULL DEFAULT 0,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS routing_run_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                task_text TEXT NOT NULL,
                bucket TEXT NOT NULL DEFAULT 'general',
                difficulty TEXT NOT NULL DEFAULT 'medium',
                selected_agent TEXT NOT NULL,
                worker_id TEXT,
                ttft_ms REAL,
                latency_ms REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                quality_score REAL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                escalation_count INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                reward REAL,
                final_status TEXT NOT NULL DEFAULT 'complete',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES routing_runs(id)
            );
        """)
        await self._conn.commit()

    async def create_run(
        self,
        run_type: str,
        routing_enabled: bool,
        baseline_policy: str | None,
        suite_name: str,
        repeat_index: int = 0,
        notes: str | None = None,
    ) -> int:
        cursor = await self._conn.execute(
            """INSERT INTO routing_runs
               (run_type, started_at, routing_enabled, baseline_policy, task_suite_name, repeat_index, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_type, _now(), int(routing_enabled), baseline_policy, suite_name, repeat_index, notes),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def finish_run(self, run_id: int) -> None:
        await self._conn.execute(
            "UPDATE routing_runs SET finished_at = ? WHERE id = ?",
            (_now(), run_id),
        )
        await self._conn.commit()

    async def insert_run_task(
        self,
        run_id: int,
        task_id: str,
        task_text: str,
        bucket: str,
        difficulty: str,
        selected_agent: str,
        latency_ms: float,
        success: bool,
        reward: float | None = None,
        ttft_ms: float | None = None,
        quality_score: float | None = None,
        retry_count: int = 0,
        escalation_count: int = 0,
        cost_usd: float = 0.0,
        worker_id: str | None = None,
        final_status: str = "complete",
    ) -> None:
        await self._conn.execute(
            """INSERT INTO routing_run_tasks
               (run_id, task_id, task_text, bucket, difficulty, selected_agent, worker_id,
                ttft_ms, latency_ms, success, quality_score, retry_count, escalation_count,
                cost_usd, reward, final_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, task_text, bucket, difficulty, selected_agent, worker_id,
             ttft_ms, latency_ms, int(success), quality_score, retry_count,
             escalation_count, cost_usd, reward, final_status, _now()),
        )
        await self._conn.commit()

    async def get_run_tasks(self, run_id: int) -> list[dict]:
        async with self._conn.execute(
            "SELECT * FROM routing_run_tasks WHERE run_id = ? ORDER BY id",
            (run_id,),
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]

    async def get_run_summary(self, run_id: int) -> dict:
        tasks = await self.get_run_tasks(run_id)
        if not tasks:
            return {"n": 0, "success_rate": 0.0, "median_latency_ms": None, "mean_reward": None}
        latencies = sorted(t["latency_ms"] for t in tasks)
        successes = [t["success"] for t in tasks]
        rewards = [t["reward"] for t in tasks if t["reward"] is not None]
        return {
            "n": len(tasks),
            "success_rate": sum(successes) / len(successes),
            "median_latency_ms": latencies[len(latencies) // 2],
            "p90_latency_ms": latencies[int(len(latencies) * 0.9)],
            "mean_reward": sum(rewards) / len(rewards) if rewards else None,
        }
