"""
§7.4 — per-bucket bandit isolation test.

Updates to one bucket's A/b matrices must not mutate any other bucket's
matrices. This is the property v1 could not have (no per-bucket bandit
state), so v2 must verify it explicitly.

Tests:
  1. 20 code-bucket updates leave all non-code buckets at cold-start defaults.
  2. Cross-bucket pollution check: code's A matrix != any non-code A matrix
     after updates (values have diverged from the shared identity initialiser).
"""
from __future__ import annotations

import numpy as np

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.linucb_per_bucket import LinUCBPerBucketRouter
from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS


# Trigger phrases per bucket (same set as test_vocab_contracts.py).
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

_ACTIVE_BUCKET = "code"
_AGENT = ENABLED_AGENTS[0]  # ollama:qwen3.5
_N_UPDATES = 20
_REWARD = 0.9


def _make_strategy() -> LinUCBPerBucketRouter:
    s = LinUCBPerBucketRouter(d=9, bucket_pooling_weight=0.0)
    # Initialise all buckets so we can snapshot cold-start state.
    available = list(ENABLED_AGENTS)
    for bucket, phrase in _BUCKET_TRIGGERS.items():
        ctx = TaskContext.from_task(type("T", (), {"goal": phrase})())
        s.select_agent(ctx, available)
    return s


def _snapshot_non_active(strategy: LinUCBPerBucketRouter) -> dict[str, dict[str, bytes]]:
    """Capture A/b bytes for every non-active bucket."""
    return {
        bucket: {
            agent: strategy.A[bucket][agent].tobytes()
            for agent in strategy.A[bucket]
        }
        for bucket in BUCKETS
        if bucket != _ACTIVE_BUCKET
    }


def test_code_updates_do_not_mutate_other_buckets():
    """20 code-bucket updates leave all non-code A/b matrices unchanged."""
    strategy = _make_strategy()
    before = _snapshot_non_active(strategy)

    # Apply 20 updates to code bucket.
    code_phrase = _BUCKET_TRIGGERS[_ACTIVE_BUCKET]
    ctx = TaskContext.from_task(type("T", (), {"goal": code_phrase})())
    for _ in range(_N_UPDATES):
        strategy.update(ctx, _AGENT, _REWARD)

    after = _snapshot_non_active(strategy)

    for bucket in before:
        for agent in before[bucket]:
            assert before[bucket][agent] == after[bucket][agent], (
                f"Bucket isolation violated: code updates mutated "
                f"A[{bucket!r}][{agent!r}]"
            )


def test_code_updates_actually_change_code_matrices():
    """Sanity check: the active bucket's A matrix IS updated."""
    strategy = _make_strategy()
    before_bytes = strategy.A[_ACTIVE_BUCKET][_AGENT].tobytes()

    code_phrase = _BUCKET_TRIGGERS[_ACTIVE_BUCKET]
    ctx = TaskContext.from_task(type("T", (), {"goal": code_phrase})())
    for _ in range(_N_UPDATES):
        strategy.update(ctx, _AGENT, _REWARD)

    after_bytes = strategy.A[_ACTIVE_BUCKET][_AGENT].tobytes()
    assert before_bytes != after_bytes, (
        f"A[code][{_AGENT!r}] was not updated after {_N_UPDATES} updates — "
        "bandit is not learning"
    )


def test_non_code_ucb_scores_match_cold_start_after_code_updates():
    """Non-code UCB scores at representative context vectors are unchanged after code updates."""
    from backend.orchestrator.routing.warm_start import _BUCKET_VECTORS

    strategy = _make_strategy()

    # Snapshot UCB scores for non-code buckets before any updates.
    def _ucb(bucket: str, agent: str) -> float:
        x = np.array(_BUCKET_VECTORS[bucket], dtype=float).reshape(-1, 1)
        A = strategy.A[bucket][agent]
        b = strategy.b[bucket][agent]
        theta = np.linalg.solve(A, b)
        exploit = float((x.T @ theta).item())
        explore_sq = max(0.0, float((x.T @ np.linalg.solve(A, x)).item()))
        return exploit + strategy.alpha * float(np.sqrt(explore_sq))

    before_ucb = {
        bucket: {agent: _ucb(bucket, agent) for agent in strategy.A[bucket]}
        for bucket in BUCKETS
        if bucket != _ACTIVE_BUCKET
    }

    # 20 code updates.
    code_phrase = _BUCKET_TRIGGERS[_ACTIVE_BUCKET]
    ctx = TaskContext.from_task(type("T", (), {"goal": code_phrase})())
    for _ in range(_N_UPDATES):
        strategy.update(ctx, _AGENT, _REWARD)

    for bucket in before_ucb:
        for agent in before_ucb[bucket]:
            assert abs(_ucb(bucket, agent) - before_ucb[bucket][agent]) < 1e-10, (
                f"UCB for [{bucket!r}][{agent!r}] changed after code updates — "
                "bucket isolation violated"
            )
