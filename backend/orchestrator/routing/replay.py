"""
L3.2 — Episode replay engine.

Spec: docs/specs/v2-debug-F1-F4.md §L3.2.

Re-execute logged routing decisions under a hypothetical config and
report what would have happened. The decisions DB carries everything
we need: context_vector (handcraft 9-dim), available_agents, ucb_scores,
selected_agent, reward.

Replay loop, per logged episode:

  1. Reconstruct TaskContext from context_vector.
  2. Run alternative config's `select_agent(ctx, available)`.
  3. If alt-config picked the same agent that actually ran → reward
     observed → use logged reward.
  4. Else → reward un-observed → use counterfactual estimator
     (NaiveMeanEstimator by default — per-(bucket, agent) historical
     mean from the same DB).
  5. Step alt-config's strategy.update(ctx, alt_pick, alt_reward).
  6. Aggregate cumulative reward, regret-vs-actual.

Replay is a SCREENING tool. A config that loses on replay almost
certainly loses live; a config that wins on replay needs A/B
confirmation before adoption. The signal-to-noise is bounded by the
counterfactual estimator's MAE against the true reward.

This module is self-contained (no FastAPI imports) so it can run
offline against any decisions DB without booting a server.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import numpy as np

from .context import TaskContext
from .counterfactual import (
    CounterfactualEstimate,
    Estimator,
    NaiveMeanEstimator,
    get_estimator,
)
from .strategies.linucb import LinUCBRouter
from .strategies.linucb_per_bucket import LinUCBPerBucketRouter
from .strategies.static import classify_bucket

_log = logging.getLogger(__name__)


# ── Episode loader ────────────────────────────────────────────────────────────


@dataclass
class ReplayEpisode:
    """One row of the decisions DB, normalised for replay."""
    task_id: Optional[str]
    context_vector: list[float]
    available_agents: list[str]
    actual_agent: str
    actual_reward: Optional[float]
    bucket: str

    @property
    def context(self) -> TaskContext:
        v = self.context_vector
        # TaskContext fields, in to_vector() order. Build via dataclasses.replace
        # would be cleaner, but TaskContext is a frozen dataclass — construct
        # directly with positional args matching to_vector().
        from .context import TaskContext as _TC
        return _TC(
            word_count_norm=float(v[0]),
            code_keyword_density=float(v[1]),
            is_question=float(v[2]),
            complexity_tier=float(v[3]),
            file_count=float(v[4]),
            has_error_keywords=float(v[5]),
            has_creation_keywords=float(v[6]),
            has_research_keywords=float(v[7]),
            queue_depth_norm=float(v[8]) if len(v) >= 9 else 0.0,
        )


def load_episodes(
    db_path: Path,
    limit: Optional[int] = None,
    strategy_filter: Optional[str] = None,
) -> list[ReplayEpisode]:
    """Pull replay-shaped rows from the decisions DB.

    `limit` caps to the most-recent N decisions; None = all.
    `strategy_filter` restricts to one strategy name (e.g. "linucb_per_bucket")
    so replay focuses on a coherent traffic slice.

    Skips rows missing context_vector, available_agents, or reward.
    """
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        sql = (
            "SELECT task_id, context_vector, available_agents, "
            "       selected_agent, reward, scores "
            "FROM decisions "
            "WHERE context_vector IS NOT NULL "
            "  AND available_agents IS NOT NULL "
            "  AND selected_agent IS NOT NULL "
            "  AND reward IS NOT NULL"
        )
        params: list = []
        if strategy_filter:
            sql += " AND strategy = ?"
            params.append(strategy_filter)
        if limit is not None:
            # Most-recent N: pull DESC, then reverse in Python so the
            # returned list stays chronological for the replay loop.
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(int(limit))
        else:
            sql += " ORDER BY id ASC"
        rows = conn.execute(sql, params).fetchall()
        if limit is not None:
            rows = list(reversed(rows))
    finally:
        conn.close()

    out: list[ReplayEpisode] = []
    for task_id, ctx_json, avail_json, agent, reward, scores_json in rows:
        try:
            ctx_vec = json.loads(ctx_json)
            available = json.loads(avail_json)
            scores = json.loads(scores_json) if scores_json else {}
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(ctx_vec, list) or len(ctx_vec) < 9:
            continue
        if not isinstance(available, list) or not available:
            continue
        # Bucket from logged scores (per-bucket bandit) or from classifier.
        bucket = "default"
        if isinstance(scores, dict):
            agent_score = scores.get(agent)
            if isinstance(agent_score, dict) and "bucket" in agent_score:
                bucket = agent_score["bucket"]
        out.append(ReplayEpisode(
            task_id=task_id,
            context_vector=ctx_vec,
            available_agents=available,
            actual_agent=agent,
            actual_reward=float(reward),
            bucket=bucket,
        ))
    return out


# ── Strategy factory ─────────────────────────────────────────────────────────


def build_strategy(
    name: str,
    *,
    alpha: float = 1.0,
    decay: float = 0.98,
    bucket_pooling_weight: float = 0.5,
):
    """Construct a fresh strategy for replay.

    Replay always starts the strategy from scratch — we want to see
    what the alt-config's *learning trajectory* looks like, not what
    happens with pre-loaded weights from another strategy."""
    name = name.strip().lower()
    if name in ("linucb", "linucb_v1", "v1"):
        return LinUCBRouter(d=9, alpha=alpha, decay=decay)
    if name in ("linucb_per_bucket", "per_bucket", "v2"):
        return LinUCBPerBucketRouter(
            d=9, alpha=alpha, decay=decay,
            bucket_pooling_weight=bucket_pooling_weight,
        )
    raise ValueError(f"unsupported replay strategy: {name!r}")


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class ReplayResult:
    """Aggregate metrics from a replay run."""
    config_name: str
    n_episodes: int
    n_pick_matches: int        # alt-config picked same agent that actually ran
    n_overrides: int            # alt-config picked something different
    cumulative_actual_reward: float
    cumulative_replay_reward: float
    delta: float                # replay - actual
    n_estimator_used: int       # how many overrides got a real estimate
    n_estimator_fallbacks: int  # how many fell back to default
    estimator: str

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in (
            "cumulative_actual_reward", "cumulative_replay_reward", "delta",
        ):
            d[k] = round(d[k], 4)
        return d


# ── Replay loop ──────────────────────────────────────────────────────────────


def replay(
    episodes: list[ReplayEpisode],
    strategy_name: str = "linucb_per_bucket",
    *,
    db_path: Path,
    estimator: Optional[Estimator] = None,
    estimator_default: float = 0.5,
    alpha: float = 1.0,
    decay: float = 0.98,
    bucket_pooling_weight: float = 0.5,
) -> ReplayResult:
    """Re-execute episodes under a new strategy config.

    Returns a ReplayResult with cumulative reward, override counts, and
    estimator usage stats. `delta = replay - actual` is the headline:
    positive means the alt-config would have outperformed actual on
    this traffic slice (subject to estimator bias).
    """
    if not episodes:
        return ReplayResult(
            config_name=strategy_name, n_episodes=0,
            n_pick_matches=0, n_overrides=0,
            cumulative_actual_reward=0.0, cumulative_replay_reward=0.0,
            delta=0.0, n_estimator_used=0, n_estimator_fallbacks=0,
            estimator=(estimator or NaiveMeanEstimator(db_path)).__class__.__name__,
        )

    if estimator is None:
        estimator = NaiveMeanEstimator(db_path=db_path)

    strategy = build_strategy(
        strategy_name, alpha=alpha, decay=decay,
        bucket_pooling_weight=bucket_pooling_weight,
    )

    n_match = 0
    n_override = 0
    n_estimator_used = 0
    n_estimator_fallback = 0
    cum_actual = 0.0
    cum_replay = 0.0

    for ep in episodes:
        ctx = ep.context
        try:
            alt_pick = strategy.select_agent(ctx, ep.available_agents)
        except (ValueError, IndexError):
            # Empty or invalid available set — skip this episode.
            continue

        if alt_pick == ep.actual_agent:
            replay_reward = ep.actual_reward or 0.0
            n_match += 1
        else:
            n_override += 1
            est = estimator.estimate(ep.bucket, alt_pick)
            if est is not None:
                replay_reward = est.estimated_reward
                n_estimator_used += 1
            else:
                replay_reward = estimator_default
                n_estimator_fallback += 1

        cum_actual += float(ep.actual_reward or 0.0)
        cum_replay += float(replay_reward)

        # Step the alt-config's bandit so its trajectory evolves like
        # the real bandit's would have under this config.
        try:
            strategy.update(ctx, alt_pick, replay_reward)
        except (ValueError, np.linalg.LinAlgError):
            pass

    return ReplayResult(
        config_name=strategy_name,
        n_episodes=len(episodes),
        n_pick_matches=n_match,
        n_overrides=n_override,
        cumulative_actual_reward=cum_actual,
        cumulative_replay_reward=cum_replay,
        delta=cum_replay - cum_actual,
        n_estimator_used=n_estimator_used,
        n_estimator_fallbacks=n_estimator_fallback,
        estimator=estimator.__class__.__name__,
    )
