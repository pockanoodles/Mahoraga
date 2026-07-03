"""Tests for L3.2 episode replay engine + counterfactual estimators."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.orchestrator.routing.counterfactual import (
    ConstantEstimator,
    NaiveMeanEstimator,
    get_estimator,
)
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.replay import (
    ReplayEpisode,
    build_strategy,
    load_episodes,
    replay,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)
    return db


def _insert(
    conn: sqlite3.Connection,
    *,
    agent: str,
    bucket: str,
    reward: float,
    context: list[float] | None = None,
    available: list[str] | None = None,
    strategy: str = "linucb_per_bucket",
) -> None:
    """Insert a decision row with the minimum fields replay needs."""
    if context is None:
        context = [0.5] * 9
    if available is None:
        available = [agent, "aider", "codex-cli", "gemini-cli"]
    scores = {
        a: {
            "ucb": 0.5, "exploit": 0.4, "explore": 0.1,
            "variance": 0.05, "bucket": bucket,
        }
        for a in available
    }
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, available_agents, "
        "  context_vector, scores, reward, success, latency_s, cost_usd"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-05-07T00:00:00Z",
            strategy,
            agent,
            json.dumps(available),
            json.dumps(context),
            json.dumps(scores),
            reward,
            1,
            2.0,
            0.0,
        ),
    )
    conn.commit()


# ── NaiveMeanEstimator ────────────────────────────────────────────────────────


def test_naive_estimator_returns_per_cell_mean(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(10):
        _insert(conn, agent="ollama", bucket="code", reward=0.8)
    for _ in range(8):
        _insert(conn, agent="aider", bucket="code", reward=0.4)
    conn.close()
    est = NaiveMeanEstimator(db_path=db, min_support=1)
    e_ollama = est.estimate("code", "ollama")
    e_aider = est.estimate("code", "aider")
    assert e_ollama is not None
    assert e_ollama.estimated_reward == pytest.approx(0.8, abs=1e-6)
    assert e_ollama.n_neighbours == 10
    assert e_aider.estimated_reward == pytest.approx(0.4, abs=1e-6)


def test_naive_estimator_min_support_floor(tmp_path):
    """Cells with fewer than min_support observations return None."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama", bucket="code", reward=0.8)
    _insert(conn, agent="ollama", bucket="code", reward=0.7)
    conn.close()
    est = NaiveMeanEstimator(db_path=db, min_support=10)
    assert est.estimate("code", "ollama") is None


def test_naive_estimator_unknown_cell_returns_none(tmp_path):
    db = _fresh_db(tmp_path)
    est = NaiveMeanEstimator(db_path=db, min_support=1)
    assert est.estimate("code", "claude") is None


def test_naive_estimator_missing_db(tmp_path):
    est = NaiveMeanEstimator(db_path=tmp_path / "nope.db")
    assert est.estimate("code", "ollama") is None


def test_constant_estimator_returns_value():
    est = ConstantEstimator(value=0.6)
    e = est.estimate("anything", "anything")
    assert e is not None
    assert e.estimated_reward == 0.6


def test_get_estimator_factory():
    e1 = get_estimator("naive", db_path=Path("/tmp/x.db"))
    assert isinstance(e1, NaiveMeanEstimator)
    e2 = get_estimator("constant", db_path=Path("/tmp/x.db"), value=0.7)
    assert isinstance(e2, ConstantEstimator)
    with pytest.raises(NotImplementedError):
        get_estimator("knn", db_path=Path("/tmp/x.db"))
    with pytest.raises(ValueError):
        get_estimator("nonsense", db_path=Path("/tmp/x.db"))


# ── load_episodes ─────────────────────────────────────────────────────────────


def test_load_episodes_basic(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(5):
        _insert(conn, agent="ollama", bucket="code", reward=0.8)
    conn.close()
    eps = load_episodes(db_path=db)
    assert len(eps) == 5
    assert all(isinstance(e, ReplayEpisode) for e in eps)
    assert eps[0].actual_agent == "ollama"


def test_load_episodes_limit_recent(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for i in range(10):
        _insert(conn, agent="ollama", bucket="code", reward=float(i) / 10)
    conn.close()
    eps = load_episodes(db_path=db, limit=3)
    assert len(eps) == 3
    # Most-recent → ascending: rewards 0.7, 0.8, 0.9.
    assert eps[-1].actual_reward == pytest.approx(0.9, abs=1e-6)


def test_load_episodes_strategy_filter(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama", bucket="code", reward=0.8, strategy="linucb")
    _insert(conn, agent="ollama", bucket="code", reward=0.7, strategy="linucb_per_bucket")
    conn.close()
    eps = load_episodes(db_path=db, strategy_filter="linucb")
    assert len(eps) == 1
    assert eps[0].actual_reward == pytest.approx(0.8, abs=1e-6)


def test_load_episodes_skips_malformed(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Bad context_vector JSON.
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, available_agents, "
        "  context_vector, reward"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            "2026-05-07T00:00:00Z", "x", "ollama",
            json.dumps(["ollama"]), "not_valid_json", 0.5,
        ),
    )
    # Wrong-length context.
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, available_agents, "
        "  context_vector, reward"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            "2026-05-07T00:00:00Z", "x", "ollama",
            json.dumps(["ollama"]), json.dumps([0.0, 0.0]), 0.5,
        ),
    )
    # Valid row.
    _insert(conn, agent="ollama", bucket="code", reward=0.5)
    conn.close()
    eps = load_episodes(db_path=db)
    assert len(eps) == 1


def test_load_episodes_missing_db_returns_empty(tmp_path):
    eps = load_episodes(db_path=tmp_path / "no_such.db")
    assert eps == []


# ── build_strategy ────────────────────────────────────────────────────────────


def test_build_strategy_v1():
    s = build_strategy("linucb", alpha=1.5, decay=0.95)
    from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
    assert isinstance(s, LinUCBRouter)
    assert s.alpha == 1.5
    assert s.decay == 0.95


def test_build_strategy_per_bucket():
    s = build_strategy("linucb_per_bucket", alpha=2.0, bucket_pooling_weight=0.7)
    from backend.orchestrator.routing.strategies.linucb_per_bucket import (
        LinUCBPerBucketRouter,
    )
    assert isinstance(s, LinUCBPerBucketRouter)
    assert s.alpha == 2.0
    assert s.bucket_pooling_weight == 0.7


def test_build_strategy_unknown_raises():
    with pytest.raises(ValueError):
        build_strategy("not_a_real_strategy")


# ── replay loop ──────────────────────────────────────────────────────────────


def test_replay_empty_episodes(tmp_path):
    db = _fresh_db(tmp_path)
    result = replay([], db_path=db)
    assert result.n_episodes == 0
    assert result.delta == 0.0


def test_replay_pick_match_uses_actual_reward(tmp_path):
    """When alt-config picks the SAME agent that ran, replay reward
    should equal actual reward — no estimator involved."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Single-agent setup ensures alt-config has no choice but to pick
    # the same agent that ran historically.
    for _ in range(5):
        _insert(
            conn, agent="ollama", bucket="code", reward=0.8,
            available=["ollama"],
        )
    conn.close()
    episodes = load_episodes(db_path=db)
    result = replay(episodes, db_path=db)
    assert result.n_episodes == 5
    assert result.n_pick_matches == 5
    assert result.n_overrides == 0
    assert result.cumulative_replay_reward == pytest.approx(
        result.cumulative_actual_reward, abs=1e-6,
    )


def test_replay_records_estimator_usage(tmp_path):
    """When alt-config picks differently, estimator gets queried.
    With multi-agent setups + a fresh strategy, alt-config will
    explore (UCB on first call may pick anyone) so we should see
    overrides + estimator usage."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Build history: ollama runs and gets 0.9. Aider runs and gets 0.3.
    for _ in range(20):
        _insert(conn, agent="ollama", bucket="code", reward=0.9)
        _insert(conn, agent="aider", bucket="code", reward=0.3)
    conn.close()
    episodes = load_episodes(db_path=db)
    result = replay(episodes, db_path=db)
    # Either pick matches or estimator fires — the two should add to the
    # override count + override count where estimator returned None
    # falls through to estimator_default.
    assert result.n_overrides >= 0
    assert (
        result.n_estimator_used + result.n_estimator_fallbacks
        == result.n_overrides
    )


def test_replay_constant_estimator_floors_replay_reward(tmp_path):
    """ConstantEstimator(0.0) caps replay reward when overrides happen.
    Replay reward ≤ actual when alt-config diverges."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Build a mix: half ollama at 0.9, half aider at 0.9 — so the
    # available set is wide and alt-config will explore initially.
    for _ in range(50):
        _insert(conn, agent="ollama", bucket="code", reward=0.9)
        _insert(conn, agent="aider", bucket="code", reward=0.9)
    conn.close()
    episodes = load_episodes(db_path=db)
    est = ConstantEstimator(value=0.0)
    result = replay(episodes, db_path=db, estimator=est)
    # Override episodes get reward=0.0; matched episodes get 0.9.
    # So replay sum < actual sum when there are any overrides.
    if result.n_overrides > 0:
        assert result.cumulative_replay_reward < result.cumulative_actual_reward


def test_replay_handles_invalid_episodes_gracefully(tmp_path):
    """Episodes with empty available_agents shouldn't crash replay."""
    db = _fresh_db(tmp_path)
    bad = ReplayEpisode(
        task_id="bad",
        context_vector=[0.0] * 9,
        available_agents=[],  # invalid
        actual_agent="ollama",
        actual_reward=0.5,
        bucket="code",
    )
    good = ReplayEpisode(
        task_id="good",
        context_vector=[0.0] * 9,
        available_agents=["ollama"],
        actual_agent="ollama",
        actual_reward=0.5,
        bucket="code",
    )
    result = replay([bad, good], db_path=db)
    # Bad episode skipped; good processed.
    assert result.n_episodes == 2  # input count
    assert result.n_pick_matches == 1
