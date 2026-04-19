from __future__ import annotations
import re
import time
from typing import Any
import aiosqlite

_BUCKET_KEYWORDS: dict[str, list[str]] = {
    "code":     ["write", "implement", "function", "class", "script", "module", "def ", "return"],
    "test":     ["test", "pytest", "unittest", "spec", "assert"],
    "refactor": ["refactor", "rename", "restructure", "clean up", "simplify"],
    "debug":    ["debug", "fix", "bug", "error", "traceback", "exception"],
    "research": ["search", "summarize", "find", "look up", "research", "what is"],
    "plan":     ["plan", "design", "architect", "outline", "steps", "approach"],
    "review":   ["review", "audit", "check", "evaluate", "assess"],
    "security": ["security", "vulnerability", "exploit", "auth", "injection"],
}


def _classify_bucket(text: str, hint: str | None = None) -> str:
    """Map a prompt (and optional capability hint) to a reward bucket name."""
    if hint and hint in _BUCKET_KEYWORDS:
        return hint
    lower = text.lower()
    for bucket, keywords in _BUCKET_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return bucket
    return "general"


class MetricsStore:
    """Persistent store for task execution metrics."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        """Create task_metrics table if not present."""
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                task_id TEXT NOT NULL,
                task_hash TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                capability_bucket TEXT NOT NULL DEFAULT '',
                wall_time_ms REAL,
                routing_time_ms REAL,
                agent_spawn_time_ms REAL,
                tokens_generated INTEGER,
                tokens_per_second REAL,
                prompt_tokens INTEGER,
                prompt_eval_rate REAL,
                model_was_warm INTEGER,
                bandit_ucb_score REAL,
                bandit_exploration_flag INTEGER,
                reward_score REAL,
                success INTEGER,
                quality_score REAL,
                cost_usd REAL,
                implicit_quality REAL DEFAULT NULL
            )
            """
        )
        await self._conn.commit()

    async def record(
        self,
        task_id: str,
        task_hash: str = "",
        agent_name: str = "",
        capability_bucket: str = "",
        wall_time_ms: float | None = None,
        routing_time_ms: float | None = None,
        agent_spawn_time_ms: float | None = None,
        tokens_generated: int | None = None,
        tokens_per_second: float | None = None,
        prompt_tokens: int | None = None,
        prompt_eval_rate: float | None = None,
        model_was_warm: bool | None = None,
        bandit_ucb_score: float | None = None,
        bandit_exploration_flag: bool | None = None,
        reward_score: float | None = None,
        success: bool | None = None,
        quality_score: float | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Insert a metrics row for a completed task."""
        await self._conn.execute(
            """
            INSERT INTO task_metrics (
                timestamp, task_id, task_hash, agent_name, capability_bucket,
                wall_time_ms, routing_time_ms, agent_spawn_time_ms,
                tokens_generated, tokens_per_second, prompt_tokens, prompt_eval_rate,
                model_was_warm, bandit_ucb_score, bandit_exploration_flag,
                reward_score, success, quality_score, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                task_id,
                task_hash,
                agent_name,
                capability_bucket,
                wall_time_ms,
                routing_time_ms,
                agent_spawn_time_ms,
                tokens_generated,
                tokens_per_second,
                prompt_tokens,
                prompt_eval_rate,
                int(model_was_warm) if model_was_warm is not None else None,
                bandit_ucb_score,
                int(bandit_exploration_flag) if bandit_exploration_flag is not None else None,
                reward_score,
                int(success) if success is not None else None,
                quality_score,
                cost_usd,
            ),
        )
        await self._conn.commit()

    async def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent task metrics rows."""
        cur = await self._conn.execute(
            """
            SELECT * FROM task_metrics
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_implicit_quality(self, task_id: str, implicit_quality: float) -> None:
        """Set the implicit quality signal for a completed task."""
        await self._conn.execute(
            "UPDATE task_metrics SET implicit_quality = ? WHERE task_id = ?",
            (implicit_quality, task_id),
        )
        await self._conn.commit()

    async def get_quality_correlation(self) -> dict:
        """Compute Pearson r between heuristic quality_score and implicit_quality.

        Requires ≥10 tasks with both scores. Returns dict with keys:
        'correlation', 'n_paired', 'status'
        """
        cur = await self._conn.execute(
            """
            SELECT quality_score, implicit_quality
            FROM task_metrics
            WHERE implicit_quality IS NOT NULL AND quality_score IS NOT NULL
            """
        )
        rows = await cur.fetchall()
        n = len(rows)
        if n < 10:
            return {"correlation": None, "n_paired": n, "status": f"need ≥10 paired samples (have {n})"}

        import statistics
        hs = [r[0] for r in rows]
        iq = [r[1] for r in rows]
        mean_h, mean_i = statistics.mean(hs), statistics.mean(iq)
        cov = sum((h - mean_h) * (i - mean_i) for h, i in zip(hs, iq)) / n
        std_h = statistics.pstdev(hs) or 1e-9
        std_i = statistics.pstdev(iq) or 1e-9
        r = cov / (std_h * std_i)
        return {"correlation": round(r, 4), "n_paired": n, "status": "ok"}
