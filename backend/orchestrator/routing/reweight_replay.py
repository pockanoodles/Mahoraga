"""
reweight_replay.py — offline reward re-weighting experiment.

Recomputes RewardCalculator.compute() over already-logged decisions under a
hypothetical weight vector, using the same success/quality/latency/cost
columns that produced the actual logged reward. No new inference — this
re-scores decisions that already happened, it doesn't generate new ones.
Complements `bench.py` (which runs new tasks) and `replay.py` (which tests
alternative bandit configs, not alternative reward weights).

Motivating question (2026-07-09): on real traffic, qwen3.5 and
granite4.1-8b tie in composite reward across most buckets. Structurally,
BUCKET_WEIGHTS gives 0.55-0.65 combined weight to success+cost per bucket,
and both are free local models that almost always succeed — that's weight
with no separating power between them. This tool answers: does shifting
weight toward quality/speed open a gap on the SAME logged data, or are the
models just genuinely similar regardless of weighting?
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .reward import RewardCalculator, TaskOutcome
from .vocab import BUCKETS

Weights = tuple[float, float, float, float]


def log_offline_run(db_path: Path, *, mode: str, task_count: int, notes: str) -> None:
    """Insert a bench_runs row for an offline (no-inference) experiment.

    `bench_runs` already has the columns live `orch bench run` batches use;
    this puts offline analyses (reweight, replay) in the same ledger so
    `orch bench report runs` shows every experiment in one place, not just
    the ones that ran new tasks.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO bench_runs (started_at, ended_at, mode, task_count_planned, "
            "task_count_completed, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (now, now, mode, task_count, task_count, notes),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass
class StaticWeights:
    """`RewardWeightLearner`-shaped stand-in: same (w_s, w_q, w_sp, w_c) for
    whichever buckets are given, falling back to the first entry for any
    bucket not covered (keeps callers from needing every bucket populated)."""
    weights_by_bucket: dict[str, Weights]

    def get_weights(self, bucket: str) -> Weights:
        if bucket in self.weights_by_bucket:
            return self.weights_by_bucket[bucket]
        return next(iter(self.weights_by_bucket.values()))


def load_decisions(db_path: Path, limit: Optional[int] = None) -> list[dict]:
    """Pull the columns needed to recompute reward, plus bucket from `scores`.

    Bucket isn't a column on `decisions` — it's nested per-agent inside the
    logged `scores` JSON (same extraction `replay.py.load_episodes` uses).
    """
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        sql = (
            "SELECT selected_agent, scores, success, latency_s, cost_usd, quality_score "
            "FROM decisions "
            "WHERE quality_score IS NOT NULL AND latency_s IS NOT NULL AND success IS NOT NULL "
            "ORDER BY id ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            rows = conn.execute(sql, (int(limit),)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for agent, scores_json, success, latency_s, cost_usd, quality_score in rows:
        try:
            scores = json.loads(scores_json) if scores_json else {}
        except json.JSONDecodeError:
            scores = {}
        bucket = "general"
        agent_score = scores.get(agent) if isinstance(scores, dict) else None
        if isinstance(agent_score, dict) and "bucket" in agent_score:
            bucket = agent_score["bucket"]
        out.append({
            "agent": agent,
            "bucket": bucket,
            "success": bool(success),
            "latency_s": float(latency_s),
            "cost_usd": float(cost_usd or 0.0),
            "quality_score": float(quality_score),
        })
    return out


def summarize(
    rows: list[dict],
    alt_weights: Union[Weights, dict[str, Weights]],
) -> dict[str, dict]:
    """Recompute reward under baseline BUCKET_WEIGHTS and under alt_weights.

    Returns, per bucket: sample count, each agent's baseline/alt average
    reward, and the baseline/alt gap (max-min across agents in that
    bucket) — the gap is the headline number: does it widen under alt?
    """
    baseline_calc = RewardCalculator()  # no learner attached — static BUCKET_WEIGHTS

    alt_map = {b: alt_weights for b in BUCKETS} if isinstance(alt_weights, tuple) else alt_weights
    alt_calc = RewardCalculator(learner=StaticWeights(alt_map))

    baseline_vals: dict[tuple[str, str], list[float]] = {}
    alt_vals: dict[tuple[str, str], list[float]] = {}

    for r in rows:
        outcome = TaskOutcome(
            success=r["success"],
            latency_s=r["latency_s"],
            cost_usd=r["cost_usd"],
            quality_score=r["quality_score"],
            agent_name=r["agent"],
            bucket=r["bucket"],
        )
        key = (r["bucket"], r["agent"])
        baseline_vals.setdefault(key, []).append(baseline_calc.compute(outcome))
        alt_vals.setdefault(key, []).append(alt_calc.compute(outcome))

    buckets = sorted({b for b, _a in baseline_vals})
    result: dict[str, dict] = {}
    for bucket in buckets:
        agents = sorted({a for b, a in baseline_vals if b == bucket})
        b_avg = {a: sum(baseline_vals[(bucket, a)]) / len(baseline_vals[(bucket, a)]) for a in agents}
        a_avg = {a: sum(alt_vals[(bucket, a)]) / len(alt_vals[(bucket, a)]) for a in agents}
        b_gap = (max(b_avg.values()) - min(b_avg.values())) if len(b_avg) > 1 else 0.0
        a_gap = (max(a_avg.values()) - min(a_avg.values())) if len(a_avg) > 1 else 0.0
        result[bucket] = {
            "n": sum(len(baseline_vals[(bucket, a)]) for a in agents),
            "baseline_avg": {a: round(v, 4) for a, v in b_avg.items()},
            "alt_avg": {a: round(v, 4) for a, v in a_avg.items()},
            "baseline_gap": round(b_gap, 4),
            "alt_gap": round(a_gap, 4),
        }
    return result
