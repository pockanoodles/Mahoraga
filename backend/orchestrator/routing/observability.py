"""
R1.4 — Routing health observability.

Spec: docs/v2-debug-F1-F4.md §R1.4 + §Research Methodology Shift.

Single read-only entry point: `compute_health_snapshot(db_path) -> HealthSnapshot`.
Runs a small fixed set of SQL queries over the decisions DB and returns a
typed dict consumed by:
  - `GET /api/health/routing` (frontend dashboard)
  - `orch metrics live` (terminal watch mode)
  - F2 drift detector (reads windowed reward stats)

Designed to be cheap: < 100 ms even at 10K decisions because the DB is
indexed on id and timestamp. No mutations, safe to call from anywhere.

The snapshot intentionally derives ALL state from the decisions DB —
it never reads in-process router state. This means a separate process
(`orch metrics live` running while the FastAPI server is up) sees the
same numbers as the dashboard inside the server.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path.home() / ".mahoraga-v2" / "routing_decisions.db"

# Rolling window sizes for the three reporting horizons.
ROLLING_100 = 100
ROLLING_500 = 500


# ── Output shape ──────────────────────────────────────────────────────────────


@dataclass
class WindowStats:
    """Aggregate stats over a fixed-size window of recent decisions.

    `n` is the actual number of rows aggregated (capped by the window
    size). `n_with_outcome` is how many of those have a non-null
    success column, since outcome columns are filled by observe()
    asynchronously.
    """
    n: int
    n_with_outcome: int
    mean_reward: Optional[float]
    success_rate: Optional[float]
    mean_latency_s: Optional[float]
    mean_cost_usd: Optional[float]


@dataclass
class AgentStats:
    """Per-agent rollup over the all-time decisions table."""
    n: int
    n_with_outcome: int
    win_rate: Optional[float]
    mean_reward: Optional[float]
    mean_latency_s: Optional[float]


@dataclass
class ComposerShadow:
    """Counterfactual reward delta from the composer's would_be_pick.

    Compares mean reward when composer would have AGREED with the bandit
    vs. when it would have OVERRIDDEN. Positive `counterfactual_delta`
    means composer's overrides would have been better — flip
    MAHORAGA_COMPOSER_ENABLED=1 once this is consistently positive.
    """
    n_with_data: int
    n_disagreements: int
    mean_reward_when_agreed: Optional[float]
    mean_reward_when_disagreed: Optional[float]
    counterfactual_delta: Optional[float]


@dataclass
class EscalationStats:
    """How often each escalation strategy fired + outcomes.

    `n_total_escalations` is the count of decisions where escalation
    fired (escalation_strategy != 'none'). `by_strategy` is the
    breakdown — useful to see whether claude_escalation, double_run,
    or aggressive_verify is dominant.
    """
    n_total_escalations: int
    by_strategy: dict[str, int] = field(default_factory=dict)
    rate_per_100: Optional[float] = None


@dataclass
class BrainStats:
    """A4 brain retrieval activity."""
    n_with_hits: int
    n_total_with_data: int
    mean_hit_count: Optional[float]
    mean_top_sim: Optional[float]


@dataclass
class A3Stats:
    """A3 quality predictor calibration: how off are P(success) predictions
    from actual observed reward? Lower MAE = better calibration."""
    n_with_predictions: int
    calibration_mae: Optional[float]


@dataclass
class ImportanceWeightStats:
    """Off-policy weight distribution. `n_overrides` counts decisions
    where the composer override fired (weight < 1.0)."""
    n: int
    n_overrides: int
    mean: Optional[float]
    min: Optional[float]
    max: Optional[float]


@dataclass
class ExecutionPoolSnapshot:
    """F2 ExecutionPool live state. Reads from the in-process singleton
    if present; reports zeros for an idle / never-used pool. Mainly a
    glanceable signal — `depth > 0` means agents are mid-flight right
    now, `depth_norm == 1.0` means the queue is saturated."""
    max_concurrent: int
    depth: int
    depth_norm: float


@dataclass
class BudgetPacerSnapshot:
    """F1 budget pacer status, read from the persisted state file.

    None when the pacer state file doesn't exist (fresh install or
    pacer not yet observed any tasks)."""
    ceiling: Optional[float]
    hard_limit: Optional[float]
    window: Optional[int]
    lambda_: Optional[float]
    avg_cost: Optional[float]
    n_observed: Optional[int]
    headroom: Optional[float]
    over_ceiling: Optional[bool]


@dataclass
class HealthSnapshot:
    """Full read-only view of Mahoraga's current routing health.

    Everything derived from `routing_decisions.db` + side-state files
    (currently the budget pacer JSON). Consumers should treat this as
    immutable; re-call to get an updated snapshot.
    """
    timestamp: str
    db_path: str
    total_decisions: int
    total_with_outcome: int
    by_strategy: dict[str, int]
    rolling_100: WindowStats
    rolling_500: WindowStats
    all_time: WindowStats
    by_agent: dict[str, AgentStats]
    composer_shadow: ComposerShadow
    escalation: EscalationStats
    brain: BrainStats
    a3: A3Stats
    importance_weight: ImportanceWeightStats
    budget_pacer: BudgetPacerSnapshot
    execution_pool: ExecutionPoolSnapshot

    def to_dict(self) -> dict:
        return asdict(self)


# ── Query helpers ─────────────────────────────────────────────────────────────


def _aggregate_rows(rows: list[tuple]) -> WindowStats:
    """Build a WindowStats from raw (success, latency, cost, reward) rows.

    Rows where success IS NULL are still counted in `n` but excluded from
    the means. This separates "we observed an outcome but it was a
    failure" from "no outcome yet."
    """
    if not rows:
        return WindowStats(
            n=0, n_with_outcome=0, mean_reward=None, success_rate=None,
            mean_latency_s=None, mean_cost_usd=None,
        )
    n = len(rows)
    rewards: list[float] = []
    successes: list[int] = []
    latencies: list[float] = []
    costs: list[float] = []
    for success, latency, cost, reward in rows:
        if success is None:
            continue
        successes.append(int(success))
        if reward is not None:
            rewards.append(float(reward))
        if latency is not None:
            latencies.append(float(latency))
        if cost is not None:
            costs.append(float(cost))
    n_outcome = len(successes)

    def _mean(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 4) if xs else None

    return WindowStats(
        n=n,
        n_with_outcome=n_outcome,
        mean_reward=_mean(rewards),
        success_rate=_mean([float(x) for x in successes]),
        mean_latency_s=_mean(latencies),
        mean_cost_usd=_mean(costs),
    )


def _query_window(conn: sqlite3.Connection, limit: Optional[int]) -> list[tuple]:
    """Pull (success, latency_s, cost_usd, reward) for the most-recent `limit`
    decisions, or all decisions when limit is None."""
    if limit is None:
        return conn.execute(
            "SELECT success, latency_s, cost_usd, reward FROM decisions"
        ).fetchall()
    return conn.execute(
        "SELECT success, latency_s, cost_usd, reward FROM decisions "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def _by_agent_rollup(conn: sqlite3.Connection) -> dict[str, AgentStats]:
    """Group decisions by selected_agent (the agent that actually ran)."""
    rows = conn.execute(
        "SELECT selected_agent, "
        "       COUNT(*) AS n, "
        "       SUM(CASE WHEN success IS NOT NULL THEN 1 ELSE 0 END) AS n_outcome, "
        "       AVG(CASE WHEN success IS NOT NULL THEN success ELSE NULL END) AS win_rate, "
        "       AVG(reward) AS mean_reward, "
        "       AVG(latency_s) AS mean_latency "
        "FROM decisions "
        "WHERE selected_agent IS NOT NULL "
        "GROUP BY selected_agent"
    ).fetchall()
    out: dict[str, AgentStats] = {}
    for agent, n, n_outcome, win_rate, mean_reward, mean_latency in rows:
        out[agent] = AgentStats(
            n=int(n),
            n_with_outcome=int(n_outcome or 0),
            win_rate=(round(float(win_rate), 4) if win_rate is not None else None),
            mean_reward=(round(float(mean_reward), 4) if mean_reward is not None else None),
            mean_latency_s=(round(float(mean_latency), 4) if mean_latency is not None else None),
        )
    return out


def _by_strategy(conn: sqlite3.Connection) -> dict[str, int]:
    """Counts of decisions per routing strategy (linucb, linucb_per_bucket,
    override, etc.)."""
    rows = conn.execute(
        "SELECT strategy, COUNT(*) FROM decisions GROUP BY strategy"
    ).fetchall()
    return {str(s): int(n) for s, n in rows if s is not None}


def _composer_shadow(conn: sqlite3.Connection) -> ComposerShadow:
    """Mean reward when composer would have agreed vs. disagreed.

    Treats composer_would_pick = NULL as "no shadow data yet" and
    excludes it. Among rows with shadow data, computes mean reward
    split by whether the composer's would-be pick matched the actual
    bandit pick.
    """
    rows = conn.execute(
        "SELECT bandit_pick, composer_would_pick, reward "
        "FROM decisions "
        "WHERE composer_would_pick IS NOT NULL AND reward IS NOT NULL"
    ).fetchall()
    if not rows:
        return ComposerShadow(
            n_with_data=0, n_disagreements=0,
            mean_reward_when_agreed=None,
            mean_reward_when_disagreed=None,
            counterfactual_delta=None,
        )
    agreed_rewards: list[float] = []
    disagreed_rewards: list[float] = []
    for bandit_pick, would_pick, reward in rows:
        if bandit_pick == would_pick:
            agreed_rewards.append(float(reward))
        else:
            disagreed_rewards.append(float(reward))
    mean_agreed = (
        round(sum(agreed_rewards) / len(agreed_rewards), 4) if agreed_rewards else None
    )
    mean_disagreed = (
        round(sum(disagreed_rewards) / len(disagreed_rewards), 4)
        if disagreed_rewards else None
    )
    delta = None
    if mean_agreed is not None and mean_disagreed is not None:
        delta = round(mean_disagreed - mean_agreed, 4)
    return ComposerShadow(
        n_with_data=len(rows),
        n_disagreements=len(disagreed_rewards),
        mean_reward_when_agreed=mean_agreed,
        mean_reward_when_disagreed=mean_disagreed,
        counterfactual_delta=delta,
    )


def _escalation_stats(conn: sqlite3.Connection, total: int) -> EscalationStats:
    """How often each escalation strategy fired."""
    rows = conn.execute(
        "SELECT escalation_strategy, COUNT(*) "
        "FROM decisions "
        "WHERE escalation_strategy IS NOT NULL AND escalation_strategy != 'none' "
        "GROUP BY escalation_strategy"
    ).fetchall()
    by_strategy = {str(s): int(n) for s, n in rows}
    n_total = sum(by_strategy.values())
    rate = round(100.0 * n_total / total, 2) if total > 0 else None
    return EscalationStats(
        n_total_escalations=n_total,
        by_strategy=by_strategy,
        rate_per_100=rate,
    )


def _brain_stats(conn: sqlite3.Connection) -> BrainStats:
    """A4 brain retrieval activity."""
    rows = conn.execute(
        "SELECT brain_hit_count, brain_top_sim "
        "FROM decisions "
        "WHERE brain_hit_count IS NOT NULL"
    ).fetchall()
    if not rows:
        return BrainStats(
            n_with_hits=0, n_total_with_data=0,
            mean_hit_count=None, mean_top_sim=None,
        )
    hit_counts = [int(r[0]) for r in rows if r[0] is not None]
    sims = [float(r[1]) for r in rows if r[1] is not None]
    n_with_hits = sum(1 for c in hit_counts if c > 0)
    return BrainStats(
        n_with_hits=n_with_hits,
        n_total_with_data=len(rows),
        mean_hit_count=(round(sum(hit_counts) / len(hit_counts), 4) if hit_counts else None),
        mean_top_sim=(round(sum(sims) / len(sims), 4) if sims else None),
    )


def _a3_stats(conn: sqlite3.Connection) -> A3Stats:
    """A3 calibration: |max(predicted_p) - observed reward| averaged.

    a3_predictions is JSON {agent: P(success)}. We compare the predicted
    probability for the *selected* agent against the observed reward.
    Lower MAE = better calibration. >0.30 means the model is genuinely
    miscalibrated and a retrain might help.
    """
    rows = conn.execute(
        "SELECT a3_predictions, selected_agent, reward "
        "FROM decisions "
        "WHERE a3_predictions IS NOT NULL AND reward IS NOT NULL"
    ).fetchall()
    if not rows:
        return A3Stats(n_with_predictions=0, calibration_mae=None)
    errors: list[float] = []
    for predictions_json, agent, reward in rows:
        try:
            predictions = json.loads(predictions_json)
        except (json.JSONDecodeError, TypeError):
            continue
        p = predictions.get(agent)
        if p is None:
            continue
        errors.append(abs(float(p) - float(reward)))
    if not errors:
        return A3Stats(n_with_predictions=0, calibration_mae=None)
    return A3Stats(
        n_with_predictions=len(errors),
        calibration_mae=round(sum(errors) / len(errors), 4),
    )


def _execution_pool_snapshot() -> ExecutionPoolSnapshot:
    """Read live state from the F2 ExecutionPool singleton.

    Defensive: if the pool hasn't been instantiated yet (fresh process,
    no route() calls) we report zero state at the env-resolved cap.
    """
    try:
        from .execution_pool import (
            _DEFAULT_POOL,
            resolve_max_concurrent,
        )
    except Exception:  # noqa: BLE001
        return ExecutionPoolSnapshot(
            max_concurrent=0, depth=0, depth_norm=0.0,
        )
    if _DEFAULT_POOL is None:
        return ExecutionPoolSnapshot(
            max_concurrent=resolve_max_concurrent(),
            depth=0,
            depth_norm=0.0,
        )
    return ExecutionPoolSnapshot(
        max_concurrent=_DEFAULT_POOL.max_concurrent,
        depth=_DEFAULT_POOL.depth,
        depth_norm=round(_DEFAULT_POOL.queue_depth_norm, 4),
    )


def _budget_pacer_snapshot() -> BudgetPacerSnapshot:
    """Read the persisted F1 budget pacer state. Decoupled from the
    decisions DB because pacer state lives in its own JSON file —
    this lets `orch metrics live` show the same numbers the live
    server uses without going through the in-process router."""
    from .budget_pacer import BUDGET_PACER_STATE_PATH, BudgetPacer
    if not BUDGET_PACER_STATE_PATH.exists():
        return BudgetPacerSnapshot(
            ceiling=None, hard_limit=None, window=None,
            lambda_=None, avg_cost=None, n_observed=None,
            headroom=None, over_ceiling=None,
        )
    try:
        pacer = BudgetPacer.load(BUDGET_PACER_STATE_PATH)
    except Exception:  # noqa: BLE001
        return BudgetPacerSnapshot(
            ceiling=None, hard_limit=None, window=None,
            lambda_=None, avg_cost=None, n_observed=None,
            headroom=None, over_ceiling=None,
        )
    s = pacer.to_status_dict()
    return BudgetPacerSnapshot(
        ceiling=s["ceiling"],
        hard_limit=s["hard_limit"],
        window=s["window"],
        lambda_=s["lambda"],
        avg_cost=s["avg_cost"],
        n_observed=s["n_observed"],
        headroom=s["headroom"],
        over_ceiling=s["over_ceiling"],
    )


def _importance_weight_stats(conn: sqlite3.Connection) -> ImportanceWeightStats:
    """Distribution of importance weights — most should be 1.0 (no
    override). The fraction < 1.0 tells you how often the composer
    actually flipped the bandit's pick."""
    rows = conn.execute(
        "SELECT importance_weight FROM decisions "
        "WHERE importance_weight IS NOT NULL"
    ).fetchall()
    if not rows:
        return ImportanceWeightStats(
            n=0, n_overrides=0, mean=None, min=None, max=None,
        )
    weights = [float(r[0]) for r in rows]
    n_overrides = sum(1 for w in weights if w < 1.0 - 1e-9)
    return ImportanceWeightStats(
        n=len(weights),
        n_overrides=n_overrides,
        mean=round(sum(weights) / len(weights), 4),
        min=round(min(weights), 4),
        max=round(max(weights), 4),
    )


# ── Public entry point ────────────────────────────────────────────────────────


def compute_health_snapshot(
    db_path: Path = DEFAULT_DB_PATH,
) -> HealthSnapshot:
    """Build the full HealthSnapshot for the given decisions DB.

    Returns a snapshot with all-zero / all-None fields when the DB is
    missing or empty — the caller can render that as "no data yet"
    rather than crash. Cheap to call (< 100ms on 10K rows because
    every query hits indexed columns).
    """
    db = Path(db_path)
    if not db.exists():
        return _empty_snapshot(str(db))
    conn = sqlite3.connect(str(db))
    try:
        total_row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN success IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM decisions"
        ).fetchone()
        total = int(total_row[0]) if total_row else 0
        total_with_outcome = int(total_row[1] or 0) if total_row else 0
        if total == 0:
            return _empty_snapshot(str(db))

        snap = HealthSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            db_path=str(db),
            total_decisions=total,
            total_with_outcome=total_with_outcome,
            by_strategy=_by_strategy(conn),
            rolling_100=_aggregate_rows(_query_window(conn, ROLLING_100)),
            rolling_500=_aggregate_rows(_query_window(conn, ROLLING_500)),
            all_time=_aggregate_rows(_query_window(conn, None)),
            by_agent=_by_agent_rollup(conn),
            composer_shadow=_composer_shadow(conn),
            escalation=_escalation_stats(conn, total),
            brain=_brain_stats(conn),
            a3=_a3_stats(conn),
            importance_weight=_importance_weight_stats(conn),
            budget_pacer=_budget_pacer_snapshot(),
            execution_pool=_execution_pool_snapshot(),
        )
        return snap
    finally:
        conn.close()


def _empty_snapshot(db_path: str) -> HealthSnapshot:
    """Default snapshot for missing/empty DB. All counts zero, all
    aggregates None. Renders cleanly in the dashboard as 'no data yet'."""
    empty_window = WindowStats(
        n=0, n_with_outcome=0, mean_reward=None, success_rate=None,
        mean_latency_s=None, mean_cost_usd=None,
    )
    return HealthSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        db_path=db_path,
        total_decisions=0,
        total_with_outcome=0,
        by_strategy={},
        rolling_100=empty_window,
        rolling_500=empty_window,
        all_time=empty_window,
        by_agent={},
        composer_shadow=ComposerShadow(
            n_with_data=0, n_disagreements=0,
            mean_reward_when_agreed=None,
            mean_reward_when_disagreed=None,
            counterfactual_delta=None,
        ),
        escalation=EscalationStats(n_total_escalations=0, by_strategy={}, rate_per_100=None),
        brain=BrainStats(
            n_with_hits=0, n_total_with_data=0,
            mean_hit_count=None, mean_top_sim=None,
        ),
        a3=A3Stats(n_with_predictions=0, calibration_mae=None),
        importance_weight=ImportanceWeightStats(
            n=0, n_overrides=0, mean=None, min=None, max=None,
        ),
        budget_pacer=_budget_pacer_snapshot(),
        execution_pool=_execution_pool_snapshot(),
    )
