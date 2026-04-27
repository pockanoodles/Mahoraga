from __future__ import annotations
import time
import aiosqlite


def _now() -> str:
    return str(time.time())


class RankingsStore:
    """Stores benchmark run summaries and materialized ranking snapshots."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                bucket TEXT,
                difficulty TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                avg_latency_ms REAL,
                median_latency_ms REAL,
                p90_latency_ms REAL,
                win_rate REAL,
                reward_mean REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'harness'
            );
            CREATE TABLE IF NOT EXISTS model_rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL,
                rank INTEGER NOT NULL,
                win_rate REAL,
                ci_low REAL,
                ci_high REAL,
                avg_latency_ms REAL,
                avg_reward REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)
        await self._conn.commit()

    async def upsert_benchmark_run(
        self,
        agent: str,
        bucket: str | None,
        difficulty: str | None,
        avg_latency_ms: float | None,
        median_latency_ms: float | None,
        p90_latency_ms: float | None,
        win_rate: float | None,
        reward_mean: float | None,
        sample_count: int,
        source: str = "harness",
    ) -> None:
        now = _now()
        await self._conn.execute(
            """INSERT INTO benchmark_runs
               (agent, bucket, difficulty, started_at, finished_at,
                avg_latency_ms, median_latency_ms, p90_latency_ms,
                win_rate, reward_mean, sample_count, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent, bucket, difficulty, now, now, avg_latency_ms, median_latency_ms,
             p90_latency_ms, win_rate, reward_mean, sample_count, source),
        )
        await self._conn.commit()

    async def get_benchmark_runs(
        self,
        agent: str | None = None,
        bucket: str | None = None,
        difficulty: str | None = None,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if bucket:
            conditions.append("bucket = ?")
            params.append(bucket)
        if difficulty:
            conditions.append("difficulty = ?")
            params.append(difficulty)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with self._conn.execute(
            f"SELECT * FROM benchmark_runs {where} ORDER BY id DESC",
            params,
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]

    async def replace_scope_rankings(
        self, scope_type: str, scope_value: str, rankings: list[dict]
    ) -> None:
        await self._conn.execute(
            "DELETE FROM model_rankings WHERE scope_type = ? AND scope_value = ?",
            (scope_type, scope_value),
        )
        now = _now()
        for row in rankings:
            await self._conn.execute(
                """INSERT INTO model_rankings
                   (agent, scope_type, scope_value, rank, win_rate, ci_low, ci_high,
                    avg_latency_ms, avg_reward, sample_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["agent"], scope_type, scope_value, row["rank"],
                 row.get("win_rate"), row.get("ci_low"), row.get("ci_high"),
                 row.get("avg_latency_ms"), row.get("avg_reward"),
                 row.get("sample_count", 0), now),
            )
        await self._conn.commit()

    async def get_rankings(
        self,
        scope_type: str = "overall",
        scope_value: str = "all",
        limit: int = 20,
    ) -> list[dict]:
        async with self._conn.execute(
            """SELECT * FROM model_rankings
               WHERE scope_type = ? AND scope_value = ?
               ORDER BY rank ASC LIMIT ?""",
            (scope_type, scope_value, limit),
        ) as cursor:
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) async for row in cursor]
