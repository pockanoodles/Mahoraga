"""Tests for L3.3 post-hoc analyze CLIs.

Each analysis is a pure SQL+Python reduction over the decisions DB —
test by building a small DB and verifying the aggregations match
hand-calculated expectations.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.cli.commands.analyze import (
    _a3_calibration,
    _composer_counterfactual,
    _drift_history,
    _escalation_roi,
    _override_roi,
)


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)
    return db


def _insert(
    conn: sqlite3.Connection,
    *,
    agent: str,
    bandit_pick: str | None = None,
    composer_would_pick: str | None = None,
    reward: float | None = None,
    cost: float = 0.0,
    latency: float = 1.0,
    a3_predictions: dict | None = None,
    escalation_strategy: str | None = None,
    importance_weight: float = 1.0,
    override_reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, bandit_pick, "
        "  composer_would_pick, a3_predictions, "
        "  escalation_strategy, importance_weight, override_reason, "
        "  reward, success, latency_s, cost_usd"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-05-07T00:00:00Z",
            "linucb_per_bucket",
            agent,
            bandit_pick or agent,
            composer_would_pick,
            json.dumps(a3_predictions) if a3_predictions else None,
            escalation_strategy,
            importance_weight,
            override_reason,
            reward,
            1 if reward is not None and reward > 0 else 0,
            latency,
            cost,
        ),
    )
    conn.commit()


# ── composer counterfactual ───────────────────────────────────────────────────


def test_composer_counterfactual_splits_alignment(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # Agreed: composer would have picked the same agent.
    for _ in range(10):
        _insert(conn, agent="ollama", bandit_pick="ollama",
                composer_would_pick="ollama", reward=0.5)
    # Disagreed: composer would have picked aider instead.
    for _ in range(10):
        _insert(conn, agent="ollama", bandit_pick="ollama",
                composer_would_pick="aider", reward=0.9)
    result = _composer_counterfactual(conn)
    conn.close()

    by_alignment = {r["alignment"]: r for r in result}
    assert by_alignment["agreed"]["n"] == 10
    assert by_alignment["agreed"]["mean_reward"] == 0.5
    assert by_alignment["disagreed"]["n"] == 10
    assert by_alignment["disagreed"]["mean_reward"] == 0.9


def test_composer_counterfactual_skips_no_shadow_data(tmp_path):
    """Rows without composer_would_pick are excluded entirely (not
    counted as 'agreed')."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama", reward=0.5)  # no composer_would_pick
    result = _composer_counterfactual(conn)
    conn.close()
    assert result == []


# ── escalation ROI ────────────────────────────────────────────────────────────


def test_escalation_roi_groups_by_strategy(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(5):
        _insert(conn, agent="ollama", reward=0.5)  # no escalation
    for _ in range(3):
        _insert(conn, agent="claude", reward=0.9, cost=0.05,
                escalation_strategy="claude_escalation")
    result = _escalation_roi(conn)
    conn.close()

    by_strat = {r["strategy"]: r for r in result}
    assert by_strat["none"]["n"] == 5
    assert by_strat["none"]["mean_reward"] == 0.5
    assert by_strat["claude_escalation"]["n"] == 3
    assert by_strat["claude_escalation"]["mean_cost_usd"] == 0.05


# ── A3 calibration ────────────────────────────────────────────────────────────


def test_a3_calibration_per_agent_mae(tmp_path):
    """|predicted - reward| averaged per agent."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama", reward=0.8,
            a3_predictions={"ollama": 0.9, "aider": 0.5})
    _insert(conn, agent="ollama", reward=0.6,
            a3_predictions={"ollama": 0.4, "aider": 0.5})
    _insert(conn, agent="aider", reward=0.4,
            a3_predictions={"ollama": 0.3, "aider": 0.5})
    result = _a3_calibration(conn)
    conn.close()

    by_agent = {r["agent"]: r for r in result}
    # ollama errors: |0.9-0.8| + |0.4-0.6| = 0.3 / 2 → 0.15
    assert by_agent["ollama"]["mae"] == pytest.approx(0.15, abs=1e-4)
    # aider error: |0.5-0.4| = 0.1
    assert by_agent["aider"]["mae"] == pytest.approx(0.1, abs=1e-4)


def test_a3_calibration_skips_unmatched(tmp_path):
    """Rows where selected_agent isn't in the predictions dict are skipped."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="claude", reward=0.5,
            a3_predictions={"ollama": 0.9})  # selected agent not in dict
    result = _a3_calibration(conn)
    conn.close()
    assert result == []


# ── drift history ────────────────────────────────────────────────────────────


def test_drift_history_returns_recent_events(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for i in range(3):
        conn.execute(
            "INSERT INTO drift_events ("
            "  timestamp, bucket, agent, deviation_sigmas, "
            "  window_mean, historical_mean, historical_std, window_size, resolution"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"2026-05-07T0{i}:00:00Z", "code", f"agent_{i}",
                3.0 + i, 0.1, 0.85, 0.05, 10,
                "auto_released" if i == 0 else None,
            ),
        )
    conn.commit()
    result = _drift_history(conn)
    conn.close()
    assert len(result) == 3
    # Most recent first.
    assert result[0]["agent"] == "agent_2"
    # Resolution status preserved.
    statuses = [r["resolution"] for r in result]
    assert "ACTIVE" in statuses  # i=1, 2
    assert "auto_released" in statuses  # i=0


def test_drift_history_handles_missing_table(tmp_path):
    """If the drift_events table doesn't exist (pre-F5 DB), return []
    rather than raise."""
    db = tmp_path / "no_drift.db"
    conn = sqlite3.connect(str(db))
    # Build a minimal `decisions` table but not drift_events.
    conn.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY)")
    result = _drift_history(conn)
    conn.close()
    assert result == []


# ── override ROI ─────────────────────────────────────────────────────────────


def test_override_roi_splits_by_override_flag(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for _ in range(5):
        _insert(conn, agent="ollama", reward=0.5)  # no override
    for _ in range(3):
        _insert(
            conn, agent="ollama", reward=0.8,
            override_reason="a3_override", importance_weight=0.3,
        )
    result = _override_roi(conn)
    conn.close()

    by_kind = {r["kind"]: r for r in result}
    assert by_kind["not_overridden"]["n"] == 5
    assert by_kind["not_overridden"]["mean_reward"] == 0.5
    assert by_kind["overridden"]["n"] == 3
    assert by_kind["overridden"]["mean_reward"] == 0.8
    assert by_kind["overridden"]["mean_importance_weight"] == pytest.approx(
        0.3, abs=1e-4,
    )
