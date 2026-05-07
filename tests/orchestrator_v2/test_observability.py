"""Tests for R1.4 — routing observability snapshot."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.observability import (
    HealthSnapshot,
    compute_health_snapshot,
)


# ── DB builders ───────────────────────────────────────────────────────────────


def _fresh_db(tmp_path: Path) -> Path:
    """Return a path to a freshly migrated decisions DB (empty)."""
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)  # triggers _migrate()
    return db


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    agent: str,
    bandit_pick: str | None = None,
    success: int | None = None,
    reward: float | None = None,
    latency: float | None = None,
    cost: float | None = None,
    strategy: str = "linucb_per_bucket",
    composer_would_pick: str | None = None,
    a3_predictions: dict | None = None,
    brain_hit_count: int | None = None,
    brain_top_sim: float | None = None,
    escalation_strategy: str | None = None,
    importance_weight: float | None = 1.0,
) -> None:
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, bandit_pick, success, reward, "
        "  latency_s, cost_usd, composer_would_pick, a3_predictions, "
        "  brain_hit_count, brain_top_sim, escalation_strategy, importance_weight"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-05-07T00:00:00Z",
            strategy,
            agent,
            bandit_pick or agent,
            success,
            reward,
            latency,
            cost,
            composer_would_pick,
            json.dumps(a3_predictions) if a3_predictions else None,
            brain_hit_count,
            brain_top_sim,
            escalation_strategy,
            importance_weight,
        ),
    )
    conn.commit()


# ── empty / missing ───────────────────────────────────────────────────────────


def test_missing_db_returns_empty_snapshot(tmp_path):
    snap = compute_health_snapshot(db_path=tmp_path / "nope.db")
    assert isinstance(snap, HealthSnapshot)
    assert snap.total_decisions == 0
    assert snap.rolling_100.n == 0
    assert snap.composer_shadow.n_with_data == 0


def test_empty_db_returns_empty_snapshot(tmp_path):
    db = _fresh_db(tmp_path)
    snap = compute_health_snapshot(db_path=db)
    assert snap.total_decisions == 0
    assert snap.rolling_100.mean_reward is None


# ── basic counts + windows ────────────────────────────────────────────────────


def test_total_count_matches(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for i in range(150):
        _insert_decision(conn, agent="ollama", success=1, reward=0.8)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.total_decisions == 150
    assert snap.total_with_outcome == 150


def test_rolling_100_caps_at_window(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(250):
        _insert_decision(conn, agent="ollama", success=1, reward=0.5)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.rolling_100.n == 100
    assert snap.rolling_500.n == 250  # under window cap
    assert snap.all_time.n == 250


def test_no_outcome_yet_means_means_are_none(tmp_path):
    """Decisions logged but observe() not yet called — counts increment but
    means stay None until outcomes arrive."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(10):
        _insert_decision(conn, agent="ollama", success=None, reward=None)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.rolling_100.n == 10
    assert snap.rolling_100.n_with_outcome == 0
    assert snap.rolling_100.mean_reward is None


# ── per-agent rollup ──────────────────────────────────────────────────────────


def test_by_agent_rollup_aggregates_correctly(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(10):
        _insert_decision(conn, agent="ollama", success=1, reward=0.9)
    for _ in range(5):
        _insert_decision(conn, agent="aider", success=0, reward=0.2)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert "ollama" in snap.by_agent
    assert "aider" in snap.by_agent
    assert snap.by_agent["ollama"].n == 10
    assert snap.by_agent["ollama"].mean_reward == 0.9
    assert snap.by_agent["ollama"].win_rate == 1.0
    assert snap.by_agent["aider"].mean_reward == 0.2
    assert snap.by_agent["aider"].win_rate == 0.0


# ── composer shadow ───────────────────────────────────────────────────────────


def test_composer_shadow_splits_agree_vs_disagree(tmp_path):
    """Agreed: bandit_pick == composer_would_pick. Disagreed: differ.
    Mean reward should differ between them per the synthetic data."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Agreed cases — all rewarded 0.5.
    for _ in range(10):
        _insert_decision(
            conn, agent="ollama",
            bandit_pick="ollama", composer_would_pick="ollama",
            success=1, reward=0.5,
        )
    # Disagreed cases — would-be pick is aider, rewarded 0.9.
    for _ in range(10):
        _insert_decision(
            conn, agent="ollama",
            bandit_pick="ollama", composer_would_pick="aider",
            success=1, reward=0.9,
        )
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    cs = snap.composer_shadow
    assert cs.n_with_data == 20
    assert cs.n_disagreements == 10
    assert cs.mean_reward_when_agreed == 0.5
    assert cs.mean_reward_when_disagreed == 0.9
    assert cs.counterfactual_delta == 0.4


def test_composer_shadow_handles_no_data(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert_decision(conn, agent="ollama", success=1, reward=0.8)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.composer_shadow.n_with_data == 0
    assert snap.composer_shadow.counterfactual_delta is None


# ── escalation ────────────────────────────────────────────────────────────────


def test_escalation_stats_count_per_strategy(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(100):
        _insert_decision(conn, agent="ollama", success=1)
    for _ in range(5):
        _insert_decision(
            conn, agent="claude", success=1,
            escalation_strategy="claude_escalation",
        )
    for _ in range(3):
        _insert_decision(
            conn, agent="ollama", success=1,
            escalation_strategy="aggressive_verify",
        )
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.escalation.n_total_escalations == 8
    assert snap.escalation.by_strategy["claude_escalation"] == 5
    assert snap.escalation.by_strategy["aggressive_verify"] == 3
    # Rate per 100: 8 / 108 * 100 ≈ 7.41
    assert 7.0 < snap.escalation.rate_per_100 < 8.0


def test_none_escalations_excluded(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert_decision(conn, agent="ollama", success=1, escalation_strategy="none")
    _insert_decision(
        conn, agent="ollama", success=1,
        escalation_strategy="aggressive_verify",
    )
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.escalation.n_total_escalations == 1


# ── brain ─────────────────────────────────────────────────────────────────────


def test_brain_stats(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert_decision(conn, agent="ollama", success=1, brain_hit_count=3, brain_top_sim=0.5)
    _insert_decision(conn, agent="ollama", success=1, brain_hit_count=0, brain_top_sim=None)
    _insert_decision(conn, agent="ollama", success=1, brain_hit_count=2, brain_top_sim=0.7)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.brain.n_with_hits == 2  # rows where brain_hit_count > 0
    assert snap.brain.n_total_with_data == 3
    assert snap.brain.mean_top_sim == pytest.approx(0.6, abs=1e-4)


# ── A3 calibration ────────────────────────────────────────────────────────────


def test_a3_calibration_mae(tmp_path):
    """If predicted P(success | ollama) = 0.9 and observed reward = 0.8, error = 0.1.
    Across 3 such rows, MAE should be the mean error."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert_decision(
        conn, agent="ollama", success=1, reward=0.8,
        a3_predictions={"ollama": 0.9, "aider": 0.5},
    )
    _insert_decision(
        conn, agent="ollama", success=1, reward=0.6,
        a3_predictions={"ollama": 0.4, "aider": 0.5},
    )
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    # Errors: |0.9 - 0.8| + |0.4 - 0.6| = 0.1 + 0.2 = 0.3 → mean 0.15.
    assert snap.a3.calibration_mae == pytest.approx(0.15, abs=1e-4)
    assert snap.a3.n_with_predictions == 2


def test_a3_skips_rows_with_no_match(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Selected agent isn't in the predictions dict → row skipped.
    _insert_decision(
        conn, agent="claude", success=1, reward=0.8,
        a3_predictions={"ollama": 0.9, "aider": 0.5},
    )
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.a3.n_with_predictions == 0


# ── importance weight ────────────────────────────────────────────────────────


def test_importance_weight_counts_overrides(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(10):
        _insert_decision(conn, agent="ollama", importance_weight=1.0)
    for _ in range(3):
        _insert_decision(conn, agent="ollama", importance_weight=0.2)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    iw = snap.importance_weight
    assert iw.n == 13
    assert iw.n_overrides == 3
    assert iw.min == 0.2
    assert iw.max == 1.0


# ── snapshot serialisation ────────────────────────────────────────────────────


def test_to_dict_is_json_serialisable(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert_decision(conn, agent="ollama", success=1, reward=0.8)
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    d = snap.to_dict()
    # No exception → serialisable. Round-trip via JSON to confirm.
    s = json.dumps(d)
    parsed = json.loads(s)
    assert parsed["total_decisions"] == 1


def test_strategy_distribution_grouped(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(5):
        _insert_decision(conn, agent="ollama", strategy="linucb")
    for _ in range(8):
        _insert_decision(conn, agent="ollama", strategy="linucb_per_bucket")
    conn.close()
    snap = compute_health_snapshot(db_path=db)
    assert snap.by_strategy["linucb"] == 5
    assert snap.by_strategy["linucb_per_bucket"] == 8
