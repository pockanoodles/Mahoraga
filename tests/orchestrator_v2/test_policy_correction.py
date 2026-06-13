"""Tests for A1 — off-policy correction (routing/policy_correction.py).

Spec: docs/specs/v2-remaining-work.md §A1.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.policy_correction import (
    WEIGHT_FLOOR,
    auto_temperature,
    bandit_probs_from_scores,
    importance_weight,
)
from backend.orchestrator.routing.reward import TaskOutcome
from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
from backend.orchestrator.routing.strategies.linucb_per_bucket import (
    LinUCBPerBucketRouter,
)


# ── auto_temperature ──────────────────────────────────────────────────────────


def test_auto_temperature_uses_std():
    assert auto_temperature([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(
        np.std([1.0, 2.0, 3.0, 4.0, 5.0]), abs=1e-6,
    )


def test_auto_temperature_floor_when_identical():
    """All-equal UCB → std=0; must not produce τ=0 (division by zero)."""
    t = auto_temperature([0.5, 0.5, 0.5, 0.5])
    assert t > 0.0


def test_auto_temperature_handles_single_score():
    t = auto_temperature([1.0])
    assert t > 0.0


# ── bandit_probs_from_scores ──────────────────────────────────────────────────


def test_bandit_probs_sum_to_one():
    scores = {
        "a": {"ucb": 1.0},
        "b": {"ucb": 0.5},
        "c": {"ucb": 0.0},
    }
    p = bandit_probs_from_scores(scores)
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-6)


def test_bandit_probs_peaked_when_one_dominates():
    """With auto τ = std, the spread is invariant to score magnitude — but
    a clear winner among 4 candidates concentrates the bulk of probability
    on it, while the underdogs split the remainder."""
    scores = {
        "winner": {"ucb": 10.0},
        "u1": {"ucb": 0.0},
        "u2": {"ucb": 0.0},
        "u3": {"ucb": 0.0},
    }
    p = bandit_probs_from_scores(scores)
    assert p["winner"] > 0.6
    for u in ("u1", "u2", "u3"):
        assert p[u] < 0.20


def test_bandit_probs_uniform_when_tied():
    """All-equal UCB → near-uniform probabilities (auto-temp keeps τ small
    but the exponent for each is the same, so they all share equally)."""
    scores = {
        "a": {"ucb": 0.5},
        "b": {"ucb": 0.5},
        "c": {"ucb": 0.5},
    }
    p = bandit_probs_from_scores(scores)
    for v in p.values():
        assert abs(v - 1.0 / 3) < 1e-6


def test_bandit_probs_falls_back_to_exploit():
    """Strategies without 'ucb' key (UCB1/Thompson) fall back to exploit."""
    scores = {
        "a": {"exploit": 1.0},
        "b": {"exploit": 0.0},
    }
    p = bandit_probs_from_scores(scores)
    assert p["a"] > p["b"]
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-6)


def test_bandit_probs_empty_input():
    assert bandit_probs_from_scores({}) == {}


# ── importance_weight ─────────────────────────────────────────────────────────


def test_no_override_yields_weight_one():
    scores = {
        "a": {"ucb": 1.0},
        "b": {"ucb": 0.5},
    }
    w = importance_weight(bandit_pick="a", final_agent="a", scores=scores)
    assert w == 1.0


def test_override_with_close_call_keeps_high_weight():
    """When the bandit was nearly indifferent, override weight stays moderate."""
    scores = {
        "a": {"ucb": 0.51},
        "b": {"ucb": 0.49},
    }
    w = importance_weight(bandit_pick="a", final_agent="b", scores=scores)
    assert 0.30 < w < 0.70  # near-uniform when scores are tight


def test_override_against_strong_dominance_yields_low_weight():
    """When the bandit clearly preferred its pick, overriding to a
    different agent must produce a meaningfully smaller weight than the
    no-override case (1.0). Auto τ = std bounds the peakedness, so we
    expect ~0.1–0.2, not arbitrarily small."""
    scores = {
        "winner": {"ucb": 10.0},
        "u1": {"ucb": 0.0},
        "u2": {"ucb": 0.0},
        "u3": {"ucb": 0.0},
    }
    w = importance_weight(
        bandit_pick="winner", final_agent="u1", scores=scores,
    )
    assert w < 0.20  # well below close-call territory
    assert w > WEIGHT_FLOOR


def test_floor_prevents_zero_update():
    """Even with extreme dominance, weight is never below the floor —
    otherwise we'd never learn from a series of strong overrides."""
    scores = {
        "winner": {"ucb": 1000.0},
        "underdog": {"ucb": -1000.0},
    }
    w = importance_weight(
        bandit_pick="winner", final_agent="underdog", scores=scores,
    )
    assert w >= WEIGHT_FLOOR


def test_unknown_final_agent_uses_floor():
    scores = {"a": {"ucb": 1.0}, "b": {"ucb": 0.5}}
    w = importance_weight(bandit_pick="a", final_agent="z", scores=scores)
    assert w == WEIGHT_FLOOR


def test_empty_scores_yields_one():
    """No scores means we can't compute a probability — degrade to standard."""
    w = importance_weight(bandit_pick="a", final_agent="b", scores={})
    assert w == 1.0


# ── strategy.update() with weight ─────────────────────────────────────────────


@pytest.fixture
def linucb():
    return LinUCBRouter(d=9, alpha=1.0, decay=1.0)


@pytest.fixture
def linucb_per_bucket():
    return LinUCBPerBucketRouter(d=9, alpha=1.0, decay=1.0)


class _Task:
    def __init__(self, goal: str = "what is gradient descent"):
        self.title = goal
        self.goal = goal


def test_linucb_update_weight_one_unchanged(linucb):
    """weight=1.0 must produce identical A/b to the no-weight call."""
    t = _Task()
    ctx = TaskContext.from_task(t)
    linucb.update(ctx, "ollama", 0.8)
    a_after_default = linucb.A["ollama"].copy()
    b_after_default = linucb.b["ollama"].copy()

    linucb2 = LinUCBRouter(d=9, alpha=1.0, decay=1.0)
    linucb2.update(ctx, "ollama", 0.8, weight=1.0)
    assert np.allclose(linucb2.A["ollama"], a_after_default)
    assert np.allclose(linucb2.b["ollama"], b_after_default)


def test_linucb_update_weight_zero_makes_no_change(linucb):
    """w=0 means we don't learn from the observation."""
    t = _Task()
    ctx = TaskContext.from_task(t)
    linucb._init_agent("ollama")
    a_before = linucb.A["ollama"].copy()
    b_before = linucb.b["ollama"].copy()
    linucb.update(ctx, "ollama", 0.8, weight=0.0)
    assert np.allclose(linucb.A["ollama"], a_before)
    assert np.allclose(linucb.b["ollama"], b_before)


def test_linucb_update_weight_scales_delta(linucb):
    """A 0.5-weighted update should produce half the delta of a 1.0 update."""
    t = _Task()
    ctx = TaskContext.from_task(t)
    linucb._init_agent("ollama")
    a_before = linucb.A["ollama"].copy()

    # Reference: weight=1.0
    linucb_ref = LinUCBRouter(d=9, alpha=1.0, decay=1.0)
    linucb_ref._init_agent("ollama")
    linucb_ref.update(ctx, "ollama", 0.8, weight=1.0)
    delta_ref = linucb_ref.A["ollama"] - a_before

    # Half-weighted
    linucb.update(ctx, "ollama", 0.8, weight=0.5)
    delta_half = linucb.A["ollama"] - a_before

    assert np.allclose(delta_half, 0.5 * delta_ref, atol=1e-9)


def test_linucb_per_bucket_weight(linucb_per_bucket):
    """Same scaling property must hold for per-bucket bandit."""
    t = _Task("Implement a binary search tree")
    ctx = TaskContext.from_task(t)
    linucb_per_bucket.update(ctx, "ollama", 0.8, weight=0.5)
    # Pick a bucket — the classifier picks based on context.
    from backend.orchestrator.routing.strategies.static import classify_bucket
    bucket = classify_bucket(ctx)
    assert "ollama" in linucb_per_bucket.A[bucket]
    # Comparing against a reference run.
    ref = LinUCBPerBucketRouter(d=9, alpha=1.0, decay=1.0)
    ref.update(ctx, "ollama", 0.8, weight=1.0)
    half = linucb_per_bucket.A[bucket]["ollama"]
    full = ref.A[bucket]["ollama"]
    base = np.identity(9)  # initial A
    assert np.allclose(half - base, 0.5 * (full - base), atol=1e-9)


# ── BanditRouter end-to-end: route() → observe() weight threading ─────────────


@pytest.fixture
def router(tmp_path):
    return BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )


def test_route_meta_stashed_and_consumed(router):
    """route() should populate _pending_route_meta; observe() pops it."""
    class T:
        id = "abc1"
        title = "Refactor auth.py"
        goal = "Refactor auth.py"
    t = T()
    router.route(t, available_agents=["ollama", "aider"])
    # Meta must exist.
    assert any(k.startswith("id:abc1") for k in router._pending_route_meta)
    # observe() pops it.
    router.observe(t, TaskOutcome(
        success=True, latency_s=1.0, cost_usd=0.001,
        quality_score=0.8, agent_name="ollama",
    ))
    assert not any(k.startswith("id:abc1") for k in router._pending_route_meta)


def test_no_composer_means_weight_one(router):
    """Without composer override, importance_weight stays 1.0."""
    class T:
        id = "no_override"
        title = "Plan the migration"
        goal = "Plan the migration"
    t = T()
    router.route(t, available_agents=["ollama", "aider"])
    key = next(k for k in router._pending_route_meta if "no_override" in k)
    meta = router._pending_route_meta[key]
    assert meta["importance_weight"] == 1.0
    assert meta["override_reason"] is None


def test_route_meta_persists_to_decisions_db(router):
    """The off-policy fields must land in the DB row."""
    class T:
        id = "log_test"
        title = "Add type hints"
        goal = "Add type hints"
    router.route(T(), available_agents=["ollama", "aider"])
    row = router.logger._conn.execute(
        "SELECT bandit_pick, importance_weight, ucb_scores, bandit_probs "
        "FROM decisions WHERE task_id = ?", ("log_test",),
    ).fetchone()
    assert row is not None
    assert row[0] in ("ollama", "aider")
    assert row[1] == 1.0
    # ucb_scores and bandit_probs are JSON dicts.
    import json as _json
    assert isinstance(_json.loads(row[2]), dict)
    assert isinstance(_json.loads(row[3]), dict)


def test_meta_evicted_when_bound_exceeded(router):
    """Bounded FIFO so the dict can't grow without bound."""
    router._pending_route_meta_max = 4
    for i in range(20):
        class T:
            pass
        t = T()
        t.id = f"task_{i}"
        t.title = "x"
        t.goal = "x"
        router.route(t, available_agents=["ollama", "aider"])
    assert len(router._pending_route_meta) <= router._pending_route_meta_max


def test_observe_falls_back_to_weight_one_when_meta_missing(router):
    """If observe() runs without a prior route() (e.g. orch-batch override
    path), weight should default to 1.0 and no exception should fire."""
    class T:
        id = "no_route"
        title = "Random task"
        goal = "Random task"
    t = T()
    # No route() call.
    router.observe(t, TaskOutcome(
        success=True, latency_s=1.0, cost_usd=0.001,
        quality_score=0.7, agent_name="ollama",
    ))
    # Bandit should have learned with weight=1.0.
    from backend.orchestrator.routing.strategies.static import classify_bucket
    ctx = TaskContext.from_task(t)
    bucket = classify_bucket(ctx)
    assert "ollama" in router.strategy.A[bucket]
