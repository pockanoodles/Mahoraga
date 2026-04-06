from __future__ import annotations

import time

import aiosqlite


class CostLedger:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        """Create cost_ledger table and indices if they don't exist."""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cost_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cost_ledger_user_id ON cost_ledger(user_id);
            CREATE INDEX IF NOT EXISTS idx_cost_ledger_mission_id ON cost_ledger(mission_id);
        """)
        await self._conn.commit()

    async def record(
        self,
        user_id: str,
        mission_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cost_usd: float,
    ) -> None:
        """Insert a cost record for one API call."""
        await self._conn.execute(
            """
            INSERT INTO cost_ledger
                (user_id, mission_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, mission_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd, time.time()),
        )
        await self._conn.commit()

    async def total_cost(self, user_id: str) -> float:
        """Return total spend in USD for a user across all time."""
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_ledger WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return float(row[0])

    async def cost_since(self, user_id: str, since: float) -> float:
        """Return total spend in USD for a user since the given Unix timestamp."""
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_ledger WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ) as cur:
            row = await cur.fetchone()
            return float(row[0])

    async def mission_cost(self, mission_id: str) -> float:
        """Return total spend in USD for a single mission."""
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_ledger WHERE mission_id = ?",
            (mission_id,),
        ) as cur:
            row = await cur.fetchone()
            return float(row[0])

    async def cost_by_model(
        self,
        user_id: str,
        since: float | None = None,
    ) -> list[dict]:
        """Return per-model cost breakdown for a user, optionally since a timestamp."""
        if since is not None:
            query = """
                SELECT model, COALESCE(SUM(cost_usd), 0.0) as total
                FROM cost_ledger
                WHERE user_id = ? AND created_at >= ?
                GROUP BY model
                ORDER BY total DESC
            """
            params = (user_id, since)
        else:
            query = """
                SELECT model, COALESCE(SUM(cost_usd), 0.0) as total
                FROM cost_ledger
                WHERE user_id = ?
                GROUP BY model
                ORDER BY total DESC
            """
            params = (user_id,)
        async with self._conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [{"model": row[0], "cost_usd": float(row[1])} for row in rows]
