"""
Counterfactual reward estimators for L3.2 episode replay.

When replay() picks an agent that didn't actually run for a given
historical task, we don't directly observe the reward we'd have got.
We have to estimate. Three estimators in increasing sophistication;
ship the cheap one as default, leave the others as opt-in scaffolding
for when F3 / A1.5 land.

  - NaiveMeanEstimator (default): per-(bucket, agent) historical mean
    from the decisions DB. Cheap, biased, fine for a screening tool.
  - KNNEstimator (deferred): k-nearest-neighbours over the episodic
    memory's embedding space. Implements after F3 establishes the
    embedding infrastructure end-to-end.
  - DoublyRobustEstimator (deferred): combines k-NN with importance-
    weighted observed reward when the actual policy had nonzero
    probability on the hypothetical agent.

Replay is a SCREENING tool — a config that loses on replay almost
certainly loses live. A config that wins on replay needs A/B
validation before adoption. Counterfactual estimator quality
bounds replay's signal-to-noise; better estimator → tighter signal.

`Estimator` is a Protocol so callers can swap in custom logic without
touching the replay loop.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

_log = logging.getLogger(__name__)


@dataclass
class CounterfactualEstimate:
    """One estimator's guess for the un-observed reward.

    `n_neighbours` is the count of observations the estimate is based
    on — used by replay() to weight the estimate's contribution to
    cumulative metrics. Estimates with n < min_support fall back to
    a passed-in default (replay decides what default to use)."""
    estimated_reward: float
    n_neighbours: int
    estimator: str

    def to_dict(self) -> dict:
        return {
            "estimated_reward": round(self.estimated_reward, 4),
            "n_neighbours": int(self.n_neighbours),
            "estimator": self.estimator,
        }


class Estimator(Protocol):
    """Contract: estimate(bucket, agent) → CounterfactualEstimate."""

    def estimate(
        self, bucket: str, agent: str,
    ) -> Optional[CounterfactualEstimate]:
        ...


# ── Naive per-(bucket, agent) mean ────────────────────────────────────────────


class NaiveMeanEstimator:
    """Per-(bucket, agent) historical mean from the decisions DB.

    Ships as the default for L3.2 because it has zero dependencies
    beyond what we already log. Caches per-cell means at construction
    so a 500-episode replay doesn't issue 500 SQL queries.
    """

    def __init__(self, db_path: Path, min_support: int = 5) -> None:
        self.db_path = Path(db_path)
        self.min_support = max(1, int(min_support))
        self._cell_stats: dict[tuple[str, str], tuple[float, int]] = {}
        self._loaded = False

    def _load_stats(self) -> None:
        """One-shot SQL pull of (bucket, agent) → (mean_reward, n).

        Bucket isn't a column in the decisions DB; reconstruct from the
        scores JSON which carries `bucket` per agent (per the per-bucket
        bandit's score format). Falls back to "default" when bucket
        can't be inferred — the spec's StaticRouter catch-all bucket.
        """
        if self._loaded:
            return
        self._loaded = True
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT scores, selected_agent, reward "
                "FROM decisions "
                "WHERE reward IS NOT NULL AND scores IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            _log.warning("counterfactual: DB read failed (%s)", exc)
            return
        finally:
            conn.close()

        import json as _json
        # Aggregate (bucket, agent) → list of rewards.
        agg: dict[tuple[str, str], list[float]] = {}
        for scores_json, agent, reward in rows:
            try:
                scores = _json.loads(scores_json)
            except (TypeError, _json.JSONDecodeError):
                continue
            agent_score = scores.get(agent) if isinstance(scores, dict) else None
            bucket = "default"
            if isinstance(agent_score, dict) and "bucket" in agent_score:
                bucket = agent_score["bucket"]
            agg.setdefault((bucket, agent), []).append(float(reward))

        for cell, rewards in agg.items():
            if len(rewards) >= self.min_support:
                self._cell_stats[cell] = (
                    sum(rewards) / len(rewards),
                    len(rewards),
                )

    def estimate(
        self, bucket: str, agent: str,
    ) -> Optional[CounterfactualEstimate]:
        self._load_stats()
        stats = self._cell_stats.get((bucket, agent))
        if stats is None:
            return None
        mean, n = stats
        return CounterfactualEstimate(
            estimated_reward=mean,
            n_neighbours=n,
            estimator="naive_mean",
        )


# ── Constant-default fallback ────────────────────────────────────────────────


class ConstantEstimator:
    """Return a fixed reward for every (bucket, agent) query.

    Useful as a worst-case sanity baseline — replay regret should be
    *worse* with this estimator than with NaiveMeanEstimator. If the
    other way round, the replay loop has a bug."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = float(value)

    def estimate(
        self, bucket: str, agent: str,
    ) -> Optional[CounterfactualEstimate]:
        return CounterfactualEstimate(
            estimated_reward=self.value,
            n_neighbours=0,
            estimator="constant",
        )


# ── Factory ──────────────────────────────────────────────────────────────────


def get_estimator(
    name: str,
    db_path: Path,
    **kwargs,
) -> Estimator:
    """Lookup by name. Naive is the only one wired today; others raise
    a clear NotImplementedError until F3 / A1.5 add them."""
    name = (name or "naive").strip().lower()
    if name in ("naive", "naive_mean", "default"):
        return NaiveMeanEstimator(db_path=db_path, **kwargs)
    if name == "constant":
        value = kwargs.pop("value", 0.5)
        return ConstantEstimator(value=value)
    if name == "knn":
        raise NotImplementedError(
            "k-NN estimator depends on F3 episodic-memory infrastructure; "
            "ship F3 first then revisit"
        )
    if name == "doubly_robust":
        raise NotImplementedError(
            "Doubly-robust estimator depends on k-NN + A1.5 importance "
            "weights; deferred until F3 lands"
        )
    raise ValueError(f"unknown estimator: {name!r}")
