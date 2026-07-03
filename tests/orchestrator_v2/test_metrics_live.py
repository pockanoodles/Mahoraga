"""Tests for the upgraded orch metrics live dashboard.

Covers:
 - alert detection (_alerts) for quarantine, budget, low success rate
 - recent decisions query (_recent_decisions) for ordering and field extraction
 - _render_text smoke test (doesn't crash on empty or populated snapshots)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.observability import (
    _empty_snapshot,
    QuarantineSnapshot,
    BudgetPacerSnapshot,
    WindowStats,
)
from backend.orchestrator.cli.commands.metrics import (
    _alerts,
    _recent_decisions,
    _render_text,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_snap(**overrides):
    """Build a minimal HealthSnapshot-like object from _empty_snapshot +
    field overrides. We use MagicMock so tests only need to set what matters."""
    snap = _empty_snapshot("/tmp/test.db")

    # Apply overrides to the mutable dataclass fields
    for k, v in overrides.items():
        object.__setattr__(snap, k, v)
    return snap


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)
    return db


def _insert(conn: sqlite3.Connection, *, agent: str, goal: str = "write code task",
            reward: float | None = None, success: int | None = None,
            latency_s: float = 1.0, escalation: str | None = None) -> None:
    """Insert a synthetic decisions row. Uses task_goal for bucket derivation."""
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, task_goal, "
        "  reward, success, latency_s, cost_usd, "
        "  escalation_strategy"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-05-09T00:00:00Z", "linucb_per_bucket",
            agent, goal, reward, success, latency_s, 0.0, escalation,
        ),
    )
    conn.commit()


# ── _alerts ───────────────────────────────────────────────────────────────────

def test_alerts_all_clear_on_empty_snapshot():
    snap = _make_snap()
    assert _alerts(snap) == []


def test_alerts_quarantine_active():
    qs = QuarantineSnapshot(
        n_active=1,
        entries=[{"bucket": "code", "agent": "ollama",
                  "deviation_sigmas": 3.2, "probe_successes": 0, "probe_attempts": 0}],
        n_drift_events_total=1,
        n_drift_events_unresolved=1,
    )
    snap = _make_snap(quarantine=qs)
    alerts = _alerts(snap)
    assert any("quarantine" in a for a in alerts)
    assert any("code/ollama" in a for a in alerts)


def test_alerts_budget_over_ceiling():
    bp = BudgetPacerSnapshot(
        avg_cost=0.10,
        ceiling=0.05,
        hard_limit=0.20,
        window=100,
        lambda_=1.5,
        n_observed=20,
        headroom=-0.05,
        over_ceiling=True,
    )
    snap = _make_snap(budget_pacer=bp)
    alerts = _alerts(snap)
    assert any("budget" in a for a in alerts)


def test_alerts_low_success_rate():
    w = WindowStats(
        n=50, n_with_outcome=50,
        mean_reward=0.2, success_rate=0.3,
        mean_latency_s=1.0, mean_cost_usd=0.0,
    )
    snap = _make_snap(rolling_100=w)
    alerts = _alerts(snap)
    assert any("success rate" in a for a in alerts)


def test_alerts_no_false_positive_on_healthy_snapshot():
    """A snapshot with good stats should produce no alerts."""
    w = WindowStats(
        n=50, n_with_outcome=50,
        mean_reward=0.8, success_rate=0.9,
        mean_latency_s=1.0, mean_cost_usd=0.0,
    )
    snap = _make_snap(rolling_100=w)
    assert _alerts(snap) == []


# ── _recent_decisions ─────────────────────────────────────────────────────────

def test_recent_decisions_returns_empty_for_missing_db(tmp_path):
    result = _recent_decisions(tmp_path / "nonexistent.db", n=10)
    assert result == []


def test_recent_decisions_chronological_order(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for agent in ["ollama", "aider", "gemini-cli"]:
        _insert(conn, agent=agent, goal="write a test file", reward=0.7, success=1)
    conn.close()

    result = _recent_decisions(db, n=10)
    assert len(result) == 3
    # Oldest first (chronological order for the tail display)
    assert result[0]["agent"] == "ollama"
    assert result[-1]["agent"] == "gemini-cli"


def test_recent_decisions_respects_limit(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for i in range(20):
        _insert(conn, agent="ollama", goal="write a python function")
    conn.close()

    result = _recent_decisions(db, n=5)
    assert len(result) == 5


def test_recent_decisions_includes_key_fields(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    # "debug" goal so classify_bucket returns "debug"
    _insert(conn, agent="aider", goal="fix the bug in parser.py",
            reward=0.85, success=1, latency_s=2.3, escalation="double_run")
    conn.close()

    result = _recent_decisions(db, n=1)
    assert len(result) == 1
    d = result[0]
    assert d["agent"] == "aider"
    assert d["bucket"] == "debug"
    assert d["reward"] == pytest.approx(0.85, abs=1e-4)
    assert d["success"] == 1
    assert d["latency_s"] == pytest.approx(2.3, abs=1e-3)
    assert d["escalation"] == "double_run"


# ── _render_text ──────────────────────────────────────────────────────────────

def test_render_text_does_not_crash_on_empty(tmp_path):
    snap = _make_snap()
    text = _render_text(snap, [])
    assert "Mahoraga Routing Health" in text
    assert "All systems nominal" in text


def test_render_text_shows_alert_on_quarantine():
    qs = QuarantineSnapshot(
        n_active=1,
        entries=[{"bucket": "code", "agent": "ollama",
                  "deviation_sigmas": 3.1, "probe_successes": 0, "probe_attempts": 0}],
        n_drift_events_total=1,
        n_drift_events_unresolved=1,
    )
    snap = _make_snap(quarantine=qs)
    text = _render_text(snap, [])
    assert "quarantine" in text.lower()
    assert "All systems nominal" not in text


def test_render_text_shows_recent_decisions_tail(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama", goal="write a python function", reward=0.8, success=1)
    conn.close()

    recent = _recent_decisions(db, n=5)
    snap = _make_snap()
    text = _render_text(snap, recent)
    assert "ollama" in text
    assert "code" in text
