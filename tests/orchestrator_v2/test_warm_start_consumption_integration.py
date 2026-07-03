"""
§7.3 / §15 Step 5 — warm-start consumption integration test.

In-process test (no subprocess): instantiate BanditRouter with a
compatibility matrix present in a temp path, then assert UCB scores
differ from cold-start-without-matrix defaults.

Without this test, the warm-start path can silently no-op — the unit
test only validates the injection logic in isolation, not that BanditRouter
actually calls it during __init__.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.strategies.linucb_per_bucket import LinUCBPerBucketRouter
from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS
from backend.orchestrator.routing.warm_start import _BUCKET_VECTORS


def _make_router(tmp_path: Path, matrix: dict | None) -> BanditRouter:
    with patch(
        "backend.orchestrator.routing.bandit_router.load_compatibility_matrix",
        return_value=matrix,
    ):
        return BanditRouter(
            strategy="linucb_per_bucket",
            registry=None,
            logger=DecisionLogger(db_path=tmp_path / "d.db"),
            state_path=tmp_path / "state.json",
        )


def _ucb_score(strategy: LinUCBPerBucketRouter, bucket: str, agent: str) -> float:
    """Compute UCB score for (bucket, agent) at the bucket's representative context vector."""
    x = np.array(_BUCKET_VECTORS[bucket], dtype=float).reshape(-1, 1)
    A = strategy.A[bucket][agent]
    b = strategy.b[bucket][agent]
    theta = np.linalg.solve(A, b)
    exploit = float((x.T @ theta).item())
    explore_sq = max(0.0, float((x.T @ np.linalg.solve(A, x)).item()))
    return exploit + strategy.alpha * float(np.sqrt(explore_sq))


def test_warm_start_shifts_ucb_scores_from_cold_start(tmp_path):
    """UCB scores differ between warm-started and cold-started routers for every bucket.

    The compatibility matrix assigns reward=0.9 to qwen3.5 and 0.3 to granite4.1-8b.
    After warm-start, qwen3.5's UCB score must exceed granite4.1-8b's score in every
    bucket — the bandit already has signal to prefer the better arm.
    """
    agent_good = ENABLED_AGENTS[0]   # ollama:qwen3.5  — high reward in matrix
    agent_bad  = ENABLED_AGENTS[1]   # ollama:granite4.1-8b — low reward

    matrix = {
        agent_good: {b: 0.9 for b in BUCKETS},
        agent_bad:  {b: 0.3 for b in BUCKETS},
    }
    warm_router = _make_router(tmp_path / "warm", matrix)
    cold_router = _make_router(tmp_path / "cold", None)

    warm_strategy: LinUCBPerBucketRouter = warm_router.strategy  # type: ignore[assignment]
    cold_strategy: LinUCBPerBucketRouter = cold_router.strategy  # type: ignore[assignment]

    # Force cold-start router to initialise all buckets (so we can compare scores).
    for bucket in BUCKETS:
        ctx = TaskContext.from_task(type("T", (), {"goal": _BUCKET_TRIGGERS[bucket]})())
        cold_strategy.select_agent(ctx, list(ENABLED_AGENTS))

    # Ensure warm router also has all buckets initialised.
    assert set(warm_strategy.A.keys()) == set(BUCKETS), (
        f"Warm-started router missing buckets: {set(BUCKETS) - set(warm_strategy.A.keys())}"
    )

    for bucket in BUCKETS:
        good_ucb = _ucb_score(warm_strategy, bucket, agent_good)
        bad_ucb  = _ucb_score(warm_strategy, bucket, agent_bad)

        assert good_ucb > bad_ucb, (
            f"Warm-start failed for bucket={bucket!r}: "
            f"{agent_good} UCB={good_ucb:.4f} ≤ {agent_bad} UCB={bad_ucb:.4f}"
        )

        # Warm UCB for the good arm must differ from the cold-start UCB.
        cold_ucb = _ucb_score(cold_strategy, bucket, agent_good)
        assert abs(good_ucb - cold_ucb) > 0.01, (
            f"Warm-start had no effect on {agent_good} in bucket={bucket!r}: "
            f"warm={good_ucb:.4f} vs cold={cold_ucb:.4f}"
        )


# Bucket trigger phrases (same as test_vocab_contracts.py).
_BUCKET_TRIGGERS: dict[str, str] = {
    "debug":    "the service throws a null pointer exception on startup",
    "test":     "write unit tests for the payment processing module using pytest",
    "refactor": "refactor the user repository to decouple the database layer",
    "security": "audit the login endpoint for SQL injection and XSS vulnerabilities",
    "review":   "please review this pull request and give feedback on the approach",
    "research": "explain how transformer attention mechanisms work and compare different types",
    "plan": (
        "we need to design the full architecture for a distributed caching system. "
        "the system must support multi-region replication, automatic failover, TTL-based "
        "eviction, and a pluggable backend. document the component breakdown, the data "
        "flow from client to cache to backend store, the consistency model, the failure "
        "modes, and the operational runbook for cache warm-up after a regional outage."
    ),
    "code":     "implement a REST endpoint that queries the database and returns paginated results",
    "general":  "update the team on the current project status and next steps",
}
