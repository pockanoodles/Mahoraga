"""
SQLite-backed decision logger for bandit routing.
Records every routing decision and its eventual outcome for analysis and replay.
"""

import csv
import json
import sqlite3
import threading
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
    quality_structural REAL,
    quality_novelty REAL,
    quality_not_plan REAL,
    quality_length REAL,
    quality_embed REAL,
    reward REAL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy ON decisions(strategy);
CREATE INDEX IF NOT EXISTS idx_agent ON decisions(selected_agent);
CREATE INDEX IF NOT EXISTS idx_ts ON decisions(timestamp);
CREATE TABLE IF NOT EXISTS bench_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mode TEXT,
    git_sha TEXT,
    git_dirty INTEGER,
    ollama_version TEXT,
    hostname TEXT,
    on_charger INTEGER,
    bandit_seed INTEGER,
    prompt_seed INTEGER,
    prompts_file TEXT,
    agents TEXT,
    repeats INTEGER,
    task_count_planned INTEGER,
    task_count_completed INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_bench_runs_started ON bench_runs(started_at);
"""

_QUALITY_COMPONENT_COLUMNS = [
    "quality_structural",
    "quality_novelty",
    "quality_not_plan",
    "quality_length",
    "quality_embed",
]


def _task_id(task) -> Optional[str]:
    if hasattr(task, "id"):
        return str(task.id)
    if isinstance(task, dict):
        val = task.get("id")
        return str(val) if val is not None else None
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
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns to existing DBs that pre-date the current schema."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(decisions)").fetchall()
        }
        for col in _QUALITY_COMPONENT_COLUMNS:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} REAL")
        if "bench_run_id" not in existing:
            self._conn.execute("ALTER TABLE decisions ADD COLUMN bench_run_id INTEGER")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_bench_run ON decisions(bench_run_id)"
            )
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
        bench_run_id: Optional[int] = None,
    ) -> int:
        """Insert a routing decision row and return its row id."""
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()
            ctx_vec = None
            if context is not None and hasattr(context, "to_vector"):
                ctx_vec = json.dumps(context.to_vector().tolist())

            cur = self._conn.execute(
                """
                INSERT INTO decisions
                    (timestamp, task_id, task_goal, strategy, selected_agent,
                     available_agents, context_vector, scores, bench_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    bench_run_id,
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def create_bench_run(self, **fields) -> int:
        """Insert a bench_runs row and return its id."""
        columns = [
            "started_at", "mode", "git_sha", "git_dirty", "ollama_version",
            "hostname", "on_charger", "bandit_seed", "prompt_seed",
            "prompts_file", "agents", "repeats", "task_count_planned", "notes",
        ]
        unknown = set(fields) - set(columns)
        if unknown:
            raise ValueError(f"create_bench_run: unknown fields {unknown}")
        col_names = []
        values = []
        for col in columns:
            if col in fields:
                col_names.append(col)
                values.append(fields[col])
        if "started_at" not in fields:
            col_names.append("started_at")
            values.append(datetime.now(timezone.utc).isoformat())
        placeholders = ", ".join("?" * len(col_names))
        sql = f"INSERT INTO bench_runs ({', '.join(col_names)}) VALUES ({placeholders})"
        with self._lock:
            cur = self._conn.execute(sql, values)
            self._conn.commit()
            return cur.lastrowid

    def finalize_bench_run(self, run_id: int, task_count_completed: int) -> None:
        """Set ended_at + task_count_completed on the bench_runs row."""
        ended_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE bench_runs SET ended_at = ?, task_count_completed = ? WHERE id = ?",
                (ended_at, task_count_completed, run_id),
            )
            self._conn.commit()

    def log_outcome(self, task, outcome: TaskOutcome, reward: float) -> None:
        """Back-fill outcome columns on the most-recent decision for this task."""
        with self._lock:
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

            qc = outcome.quality_components or {}
            self._conn.execute(
                """
                UPDATE decisions
                SET success            = ?,
                    latency_s          = ?,
                    cost_usd           = ?,
                    quality_score      = ?,
                    quality_structural = ?,
                    quality_novelty    = ?,
                    quality_not_plan   = ?,
                    quality_length     = ?,
                    quality_embed      = ?,
                    reward             = ?,
                    error_message      = ?
                WHERE id = ?
                """,
                (
                    1 if outcome.success else 0,
                    outcome.latency_s,
                    outcome.cost_usd,
                    outcome.quality_score,
                    qc.get("structural"),
                    qc.get("novelty"),
                    qc.get("not_plan"),
                    qc.get("length"),
                    qc.get("embed"),
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
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute("SELECT * FROM decisions ORDER BY id")
            col_names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(col_names)
            writer.writerows(rows)

    def get_recent(
        self,
        limit: int = 10,
        agent: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        """Return recent routing decisions, newest first.

        Columns returned: id, timestamp, task_id, task_goal, strategy,
        selected_agent, scores, success, latency_s, reward, error_message.
        """
        with self._lock:
            filters, params = [], []
            if agent:
                filters.append("selected_agent = ?")
                params.append(agent)
            if since:
                filters.append("timestamp >= ?")
                params.append(since)
            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            cur = self._conn.execute(
                f"SELECT id, timestamp, task_id, task_goal, strategy, selected_agent, "
                f"scores, success, latency_s, reward, error_message "
                f"FROM decisions {where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            )
            col_names = [d[0] for d in cur.description]
            return [dict(zip(col_names, row)) for row in cur.fetchall()]

    def get_decision_by_task_id(self, task_id: str) -> dict | None:
        """Return the most-recent decision row for the given task_id as a dict.

        Returned dict includes at least: task_id, task_goal, selected_agent,
        context_vector.  Returns None if no matching row is found.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT task_id, task_goal, selected_agent, context_vector "
                "FROM decisions WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            col_names = [d[0] for d in cur.description]
            return dict(zip(col_names, row))

    def count(self) -> int:
        """Return total number of logged decisions."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass
