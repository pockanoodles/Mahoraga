"""Tests for the organic-usage report (routing.usage_report).

This is the only measurement in the repo aimed at the person running Mahoraga
rather than at a benchmark, so its honesty properties are the thing under test:

  - bench traffic is excluded, or one 200-task forced-explore run swamps a
    month of real work and the local share becomes meaningless;
  - the counterfactual rate is MEASURED from this machine's own escalations,
    and when there is nothing to measure the report says "unknown" instead of
    substituting a price table;
  - unfinished decisions do not inflate the denominator.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.orchestrator.routing.usage_report import compute_usage, render_usage

_SCHEMA = """
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    selected_agent TEXT,
    success INTEGER,
    bench_run_id INTEGER,
    correctness REAL,
    escalated_to TEXT,
    escalation_cost REAL,
    escalation_reason TEXT
);
"""


def _db(tmp_path: Path, rows: list[tuple]) -> Path:
    """rows: (timestamp, agent, success, bench_run_id, correctness,
              escalated_to, escalation_cost, escalation_reason)"""
    path = tmp_path / "decisions.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO decisions (timestamp, selected_agent, success, bench_run_id,"
        " correctness, escalated_to, escalation_cost, escalation_reason)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _local(ts="2026-08-11", agent="granite", correctness=1.0):
    return (ts, agent, 1, None, correctness, None, 0.0, "")


def _escalated(ts="2026-08-11", cost=0.05, reason="judge"):
    return (ts, "granite", 1, None, 0.0, "claude-cli", cost, reason)


# ── counting ─────────────────────────────────────────────────────────────────


def test_counts_local_and_escalated(tmp_path):
    db = _db(tmp_path, [_local(), _local(), _escalated()])
    r = compute_usage(db)
    assert r.total_tasks == 3
    assert r.served_local == 2
    assert r.escalated == 1
    assert r.local_share == pytest.approx(2 / 3)


def test_bench_rows_are_excluded(tmp_path):
    """A forced-explore run is an experiment, not usage."""
    bench = ("2026-08-11", "granite", 1, 42, 1.0, None, 0.0, "")
    db = _db(tmp_path, [_local(), bench, bench, bench])
    r = compute_usage(db)
    assert r.total_tasks == 1, "bench_run_id rows must not count as usage"


def test_unfinished_decisions_are_excluded(tmp_path):
    """success IS NULL means the task never produced an outcome."""
    unfinished = ("2026-08-11", "granite", None, None, None, None, 0.0, "")
    db = _db(tmp_path, [_local(), unfinished])
    r = compute_usage(db)
    assert r.total_tasks == 1


def test_escalation_reasons_are_broken_out(tmp_path):
    db = _db(tmp_path, [
        _escalated(reason="judge"),
        _escalated(reason="exec_gate"),
        _escalated(reason="exec_gate"),
    ])
    r = compute_usage(db)
    assert r.escalated_by_reason == {"judge": 1, "exec_gate": 2}


def test_judge_verdicts_split(tmp_path):
    db = _db(tmp_path, [
        _local(correctness=1.0),
        _local(correctness=None),
        _escalated(),
    ])
    r = compute_usage(db)
    assert (r.judge_accepted, r.judge_rejected, r.judge_abstained) == (1, 1, 1)


# ── the measured counterfactual ──────────────────────────────────────────────


def test_rate_is_measured_from_this_machines_escalations(tmp_path):
    db = _db(tmp_path, [_local(), _local(), _local(),
                        _escalated(cost=0.04), _escalated(cost=0.06)])
    r = compute_usage(db)
    assert r.measured_task_rate == pytest.approx(0.05)
    assert r.avoided_spend == pytest.approx(0.15)  # 3 local × 0.05
    assert r.escalation_spend == pytest.approx(0.10)


def test_no_escalation_means_unknown_not_a_guess(tmp_path):
    """With nothing measured, the report must not invent a price."""
    db = _db(tmp_path, [_local(), _local()])
    r = compute_usage(db)
    assert r.measured_task_rate is None
    assert r.avoided_spend is None
    assert r.cost_reduction is None
    assert "unknown" in render_usage(r)


def test_zero_cost_escalations_do_not_drag_the_rate_down(tmp_path):
    """An unpriced call says nothing about what a paid one costs."""
    db = _db(tmp_path, [_local(), _escalated(cost=0.0), _escalated(cost=0.08)])
    r = compute_usage(db)
    assert r.measured_task_rate == pytest.approx(0.08)


def test_cost_reduction_is_share_of_the_counterfactual_bill(tmp_path):
    db = _db(tmp_path, [_local(), _escalated(cost=0.10)])
    r = compute_usage(db)
    # baseline = 1 local × 0.10 avoided + 0.10 actually spent = 0.20
    assert r.cost_reduction == pytest.approx(0.5)


# ── windowing ────────────────────────────────────────────────────────────────


def test_since_and_until_filter_by_date(tmp_path):
    db = _db(tmp_path, [
        _local(ts="2026-08-01"),
        _local(ts="2026-08-10"),
        _local(ts="2026-08-20"),
    ])
    assert compute_usage(db, since="2026-08-05").total_tasks == 2
    assert compute_usage(db, until="2026-08-10").total_tasks == 2
    assert compute_usage(db, since="2026-08-05", until="2026-08-15").total_tasks == 1


# ── degradation ──────────────────────────────────────────────────────────────


def test_pre_migration_db_returns_empty_rather_than_raising(tmp_path):
    """A DB from before the cascade shipped has no escalation columns."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE decisions (id INTEGER PRIMARY KEY, timestamp TEXT,"
        " selected_agent TEXT, success INTEGER, bench_run_id INTEGER);"
    )
    conn.commit()
    conn.close()
    r = compute_usage(path)
    assert r.total_tasks == 0


def test_render_is_explicit_about_the_baseline(tmp_path):
    """The substitution caveat must ship with the number, not beside it."""
    db = _db(tmp_path, [_local(), _escalated(cost=0.05)])
    text = render_usage(compute_usage(db))
    assert "substitution" in text
    assert "NOT a measure of interactive-session spend" in text


def test_empty_db_explains_what_to_do(tmp_path):
    db = _db(tmp_path, [])
    text = render_usage(compute_usage(db))
    assert "No organic traffic recorded yet" in text
