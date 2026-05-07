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


_DEFAULT_DB_PATH = Path.home() / ".mahoraga-v2" / "routing_decisions.db"

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
CREATE TABLE IF NOT EXISTS drift_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bucket TEXT NOT NULL,
    agent TEXT NOT NULL,
    window_mean REAL,
    historical_mean REAL,
    historical_std REAL,
    deviation_sigmas REAL,
    window_size INTEGER,
    resolution TEXT          -- 'auto_released' | 'manual_released' | NULL while active
);
CREATE INDEX IF NOT EXISTS idx_drift_ts ON drift_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_drift_cell ON drift_events(bucket, agent);
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
        # A1 off-policy correction columns. `selected_agent` keeps its old
        # meaning (the agent that actually ran). `bandit_pick` is what the
        # bandit would have chosen without composer override; identical to
        # selected_agent when no override occurred.
        if "bandit_pick" not in existing:
            self._conn.execute("ALTER TABLE decisions ADD COLUMN bandit_pick TEXT")
        if "ucb_scores" not in existing:
            self._conn.execute("ALTER TABLE decisions ADD COLUMN ucb_scores TEXT")
        if "bandit_probs" not in existing:
            self._conn.execute("ALTER TABLE decisions ADD COLUMN bandit_probs TEXT")
        if "override_reason" not in existing:
            self._conn.execute("ALTER TABLE decisions ADD COLUMN override_reason TEXT")
        if "importance_weight" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN importance_weight REAL"
            )
            # Backfill to the no-override default. Pre-A1 rows had no
            # composer, so importance_weight = 1.0 is the correct value.
            self._conn.execute(
                "UPDATE decisions SET importance_weight = 1.0 "
                "WHERE importance_weight IS NULL"
            )
        # A5 composer shadow telemetry. would_be_* captures what the
        # composer would have decided IF enabled — so we can compute
        # counterfactual cumulative reward offline before flipping the
        # switch. a3_predictions / brain_hit_count / brain_top_sim
        # capture the input signals at decision time.
        if "composer_would_pick" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN composer_would_pick TEXT"
            )
        if "composer_would_escalate" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN composer_would_escalate INTEGER"
            )
        if "a3_predictions" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN a3_predictions TEXT"
            )
        if "brain_hit_count" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN brain_hit_count INTEGER"
            )
        if "brain_top_sim" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN brain_top_sim REAL"
            )
        # A2: which escalation strategy the composer recommended for
        # this decision (NONE if not escalating).
        if "escalation_strategy" not in existing:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN escalation_strategy TEXT"
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
        bandit_pick: Optional[str] = None,
        ucb_scores: Optional[dict] = None,
        bandit_probs: Optional[dict] = None,
        override_reason: Optional[str] = None,
        importance_weight: Optional[float] = None,
        composer_would_pick: Optional[str] = None,
        composer_would_escalate: Optional[bool] = None,
        a3_predictions: Optional[dict] = None,
        brain_hit_count: Optional[int] = None,
        brain_top_sim: Optional[float] = None,
        escalation_strategy: Optional[str] = None,
    ) -> int:
        """Insert a routing decision row and return its row id.

        A1 off-policy fields:
          bandit_pick       — what the bandit would have picked pre-composer.
          ucb_scores        — JSON dict {agent: ucb} at decision time.
          bandit_probs      — JSON dict {agent: softmax(ucb/τ)}.
          override_reason   — composer adjustment kind (None if no override).
          importance_weight — w used for the bandit update (1.0 default).
        Defaults: bandit_pick falls back to selected_agent (no override),
        importance_weight to 1.0.
        """
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()
            ctx_vec = None
            if context is not None and hasattr(context, "to_vector"):
                ctx_vec = json.dumps(context.to_vector().tolist())

            cur = self._conn.execute(
                """
                INSERT INTO decisions
                    (timestamp, task_id, task_goal, strategy, selected_agent,
                     available_agents, context_vector, scores, bench_run_id,
                     bandit_pick, ucb_scores, bandit_probs, override_reason,
                     importance_weight, composer_would_pick,
                     composer_would_escalate, a3_predictions,
                     brain_hit_count, brain_top_sim, escalation_strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    bandit_pick if bandit_pick is not None else selected_agent,
                    json.dumps(ucb_scores) if ucb_scores else None,
                    json.dumps(bandit_probs) if bandit_probs else None,
                    override_reason,
                    importance_weight if importance_weight is not None else 1.0,
                    composer_would_pick,
                    (
                        None if composer_would_escalate is None
                        else (1 if composer_would_escalate else 0)
                    ),
                    json.dumps(a3_predictions) if a3_predictions else None,
                    brain_hit_count,
                    brain_top_sim,
                    escalation_strategy,
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

    def log_drift_event(self, alert) -> int:
        """F5: append a drift_events row when DriftDetector fires.

        `alert` is a `routing.drift_detector.DriftAlert` (kept un-typed
        here to avoid a circular import). Returns the row id.

        The `resolution` column starts NULL; future operator tooling
        can update it to "auto_released" / "manual_released" when the
        cell exits quarantine, giving us per-event resolution time.
        """
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()
            cur = self._conn.execute(
                """
                INSERT INTO drift_events (
                    timestamp, bucket, agent,
                    window_mean, historical_mean, historical_std,
                    deviation_sigmas, window_size, resolution
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    ts,
                    str(alert.bucket),
                    str(alert.agent),
                    float(alert.window_mean),
                    float(alert.historical_mean),
                    float(alert.historical_std),
                    float(alert.deviation_sigmas),
                    int(alert.window_size),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def mark_drift_resolved(
        self, bucket: str, agent: str, resolution: str = "auto_released",
    ) -> int:
        """Mark every active drift event for (bucket, agent) as resolved.

        Returns count of rows updated. Active = `resolution IS NULL`."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE drift_events SET resolution = ? "
                "WHERE bucket = ? AND agent = ? AND resolution IS NULL",
                (resolution, bucket, agent),
            )
            self._conn.commit()
            return cur.rowcount or 0

    def log_implicit_outcome(
        self,
        task_id: Optional[str],
        task_goal: str,
        agent_name: str,
        implicit_signal: float,
    ) -> bool:
        """Backfill outcome columns from an implicit retry/accept signal.

        Implicit signals (5-min retry → 0.0, accept-without-change → 0.6)
        carry less information than a full TaskOutcome, but they still
        give A3 supervision data for free. We update only rows whose
        success column is NULL — i.e. that haven't already been labelled
        by an explicit log_outcome call. This is "first writer wins":
        explicit observations always beat implicit ones.

        Returns True if a row was updated, False if no matching unlabelled
        decision was found.
        """
        with self._lock:
            row = None
            if task_id:
                row = self._conn.execute(
                    "SELECT id, selected_agent FROM decisions "
                    "WHERE task_id = ? AND success IS NULL "
                    "ORDER BY id DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT id, selected_agent FROM decisions "
                    "WHERE task_goal = ? AND success IS NULL "
                    "ORDER BY id DESC LIMIT 1",
                    (task_goal,),
                ).fetchone()
            if row is None:
                return False
            # If the recorded agent doesn't match, don't bind the signal —
            # the route() call may have picked a different agent than the
            # one the implicit tracker is reporting on.
            if row[1] and agent_name and row[1] != agent_name:
                return False
            success = 1 if implicit_signal >= 0.5 else 0
            self._conn.execute(
                "UPDATE decisions "
                "SET success = ?, quality_score = ?, reward = ? "
                "WHERE id = ?",
                (success, float(implicit_signal), float(implicit_signal), row[0]),
            )
            self._conn.commit()
            return True

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
