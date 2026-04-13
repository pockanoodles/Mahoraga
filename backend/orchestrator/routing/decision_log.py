"""
SQLite-backed decision logger for bandit routing.
Records every routing decision and its eventual outcome for analysis and replay.
"""

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .reward import TaskOutcome


_DEFAULT_DB_PATH = Path.home() / ".mahoraga" / "routing_decisions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    task_id TEXT,
    task_goal TEXT,
    strategy TEXT NOT NULL,
    selected_agent TEXT NOT NULL,
    available_agents TEXT,
    context_vector TEXT,
    scores TEXT,
    success INTEGER,
    latency_s REAL,
    cost_usd REAL,
    quality_score REAL,
    reward REAL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy ON decisions(strategy);
CREATE INDEX IF NOT EXISTS idx_agent ON decisions(selected_agent);
CREATE INDEX IF NOT EXISTS idx_ts ON decisions(timestamp);
"""


def _task_id(task) -> Optional[str]:
    if hasattr(task, "id"):
        return str(task.id)
    if isinstance(task, dict):
        return str(task.get("id", ""))
    return None


def _task_goal(task) -> str:
    if hasattr(task, "goal"):
        return task.goal
    if isinstance(task, dict):
        return task.get("goal", str(task))
    return str(task)


class DecisionLogger:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def log_decision(
        self,
        task,
        context,
        selected_agent: str,
        available_agents: list,
        strategy: str,
        scores: Optional[dict] = None,
    ) -> int:
        """Insert a routing decision row and return its row id."""
        ts = datetime.now(timezone.utc).isoformat()
        ctx_vec = None
        if context is not None and hasattr(context, "to_vector"):
            ctx_vec = json.dumps(context.to_vector().tolist())

        cur = self._conn.execute(
            """
            INSERT INTO decisions
                (timestamp, task_id, task_goal, strategy, selected_agent,
                 available_agents, context_vector, scores)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                _task_id(task),
                _task_goal(task),
                strategy,
                selected_agent,
                json.dumps(available_agents),
                ctx_vec,
                json.dumps(scores) if scores else None,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def log_outcome(self, task, outcome: TaskOutcome, reward: float) -> None:
        """Back-fill outcome columns on the most-recent decision for this task."""
        tid = _task_id(task)
        goal = _task_goal(task)

        # Match on task_id if available, otherwise fall back to task_goal.
        if tid:
            row = self._conn.execute(
                "SELECT id FROM decisions WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                (tid,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id FROM decisions WHERE task_goal = ? ORDER BY id DESC LIMIT 1",
                (goal,),
            ).fetchone()

        if row is None:
            return  # no matching decision row; nothing to update

        self._conn.execute(
            """
            UPDATE decisions
            SET success       = ?,
                latency_s     = ?,
                cost_usd      = ?,
                quality_score = ?,
                reward        = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                1 if outcome.success else 0,
                outcome.latency_s,
                outcome.cost_usd,
                outcome.quality_score,
                reward,
                outcome.error_message,
                row[0],
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get_stats(
        self,
        strategy: Optional[str] = None,
        agent: Optional[str] = None,
        last_n: Optional[int] = None,
    ) -> dict:
        """Return aggregate stats, optionally filtered by strategy / agent / recency."""
        filters, params = [], []

        if strategy:
            filters.append("strategy = ?")
            params.append(strategy)
        if agent:
            filters.append("selected_agent = ?")
            params.append(agent)

        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        # Optionally restrict to the last N rows (by id).
        if last_n:
            subq = f"SELECT * FROM decisions {where} ORDER BY id DESC LIMIT ?"
            params_sub = params + [last_n]
            base = f"SELECT * FROM ({subq}) AS sub"
            params_base = params_sub
        else:
            base = f"SELECT * FROM decisions {where}"
            params_base = params

        cur = self._conn.execute(base, params_base)
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()

        if not rows:
            return {
                "total": 0,
                "successes": 0,
                "success_rate": 0.0,
                "avg_latency": 0.0,
                "avg_cost": 0.0,
                "total_cost": 0.0,
                "avg_reward": 0.0,
                "total_reward": 0.0,
            }

        def col(name):
            idx = col_names.index(name)
            return [r[idx] for r in rows]

        successes_col = col("success")
        latency_col = [v for v in col("latency_s") if v is not None]
        cost_col = [v for v in col("cost_usd") if v is not None]
        reward_col = [v for v in col("reward") if v is not None]

        total = len(rows)
        successes = sum(1 for v in successes_col if v == 1)

        return {
            "total": total,
            "successes": successes,
            "success_rate": round(successes / total, 4) if total else 0.0,
            "avg_latency": round(sum(latency_col) / len(latency_col), 4) if latency_col else 0.0,
            "avg_cost": round(sum(cost_col) / len(cost_col), 6) if cost_col else 0.0,
            "total_cost": round(sum(cost_col), 6),
            "avg_reward": round(sum(reward_col) / len(reward_col), 4) if reward_col else 0.0,
            "total_reward": round(sum(reward_col), 4),
        }

    def export_csv(self, path: str) -> None:
        """Dump all rows to a CSV file."""
        cur = self._conn.execute("SELECT * FROM decisions ORDER BY id")
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(col_names)
            writer.writerows(rows)

    def count(self) -> int:
        """Return total number of logged decisions."""
        row = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass
