"""Tests for the offline reward re-weighting experiment (reweight_replay.py
+ `orch bench report reweight`)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from backend.orchestrator.cli.main import app
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.reward import RewardCalculator, TaskOutcome
from backend.orchestrator.routing.reweight_replay import (
    StaticWeights,
    load_decisions,
    summarize,
)

runner = CliRunner()


# ── summarize() — pure function, no DB ─────────────────────────────────────────


def test_summarize_baseline_ties_when_only_quality_differs():
    """Two agents with equal success/latency/cost but different quality: under
    baseline weights (success+cost dominate, both agents tie there), the gap
    should be much smaller than under alt weights that emphasise quality."""
    rows = [
        {"agent": "a", "bucket": "code", "success": True, "latency_s": 2.0, "cost_usd": 0.0, "quality_score": 0.95},
        {"agent": "b", "bucket": "code", "success": True, "latency_s": 2.0, "cost_usd": 0.0, "quality_score": 0.55},
    ]
    # baseline code weights: (0.60, 0.20, 0.15, 0.05) — quality only 20% of the mix
    # alt: quality dominant
    alt = (0.20, 0.60, 0.15, 0.05)
    result = summarize(rows, alt)
    assert "code" in result
    cell = result["code"]
    assert cell["n"] == 2
    assert cell["alt_gap"] > cell["baseline_gap"]


def test_summarize_matches_reward_calculator_directly():
    """summarize()'s baseline numbers must equal RewardCalculator.compute()
    with no learner attached — it should never silently diverge from the
    production formula."""
    rows = [
        {"agent": "a", "bucket": "research", "success": True, "latency_s": 3.0, "cost_usd": 0.0, "quality_score": 0.8},
    ]
    result = summarize(rows, (0.35, 0.45, 0.10, 0.10))  # research's own baseline weights, as "alt"
    expected = RewardCalculator().compute(TaskOutcome(
        success=True, latency_s=3.0, cost_usd=0.0, quality_score=0.8,
        agent_name="a", bucket="research",
    ))
    assert result["research"]["baseline_avg"]["a"] == round(expected, 4)


def test_static_weights_falls_back_for_unmapped_bucket():
    sw = StaticWeights({"code": (0.5, 0.3, 0.1, 0.1)})
    assert sw.get_weights("code") == (0.5, 0.3, 0.1, 0.1)
    assert sw.get_weights("nonexistent-bucket") == (0.5, 0.3, 0.1, 0.1)


# ── load_decisions() — needs a real DB ──────────────────────────────────────────


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)
    return db


def _insert(conn: sqlite3.Connection, *, agent: str, bucket: str, quality: float, reward: float = 0.8) -> None:
    scores = {agent: {"ucb": 0.5, "exploit": 0.4, "explore": 0.1, "variance": 0.05, "bucket": bucket}}
    conn.execute(
        "INSERT INTO decisions ("
        "  timestamp, strategy, selected_agent, available_agents, "
        "  context_vector, scores, reward, success, latency_s, cost_usd, quality_score"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-07-09T00:00:00Z", "linucb_per_bucket", agent, json.dumps([agent]),
            json.dumps([0.5] * 9), json.dumps(scores), reward, 1, 2.0, 0.0, quality,
        ),
    )
    conn.commit()


def test_load_decisions_extracts_bucket_from_scores_json(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama:qwen3.5", bucket="security", quality=0.7)
    conn.close()

    rows = load_decisions(db)
    assert len(rows) == 1
    assert rows[0]["bucket"] == "security"
    assert rows[0]["agent"] == "ollama:qwen3.5"
    assert rows[0]["quality_score"] == 0.7


def test_load_decisions_respects_limit(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    for i in range(5):
        _insert(conn, agent="ollama:qwen3.5", bucket="code", quality=0.5 + i * 0.01)
    conn.close()

    assert len(load_decisions(db)) == 5
    assert len(load_decisions(db, limit=2)) == 2


def test_load_decisions_missing_db_returns_empty(tmp_path):
    assert load_decisions(tmp_path / "does-not-exist.db") == []


# ── CLI ─────────────────────────────────────────────────────────────────────────


def test_cli_reweight_runs_end_to_end(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama:qwen3.5", bucket="code", quality=0.9)
    _insert(conn, agent="ollama:granite4.1-8b", bucket="code", quality=0.5)
    conn.close()

    result = runner.invoke(app, [
        "bench", "report", "reweight",
        "--weights", "0.20,0.55,0.20,0.05",
        "--decisions-db", str(db),
        "--min-samples", "1",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "code" in payload
    assert payload["code"]["alt_gap"] >= payload["code"]["baseline_gap"]


def test_cli_reweight_rejects_bad_weight_count(tmp_path):
    db = _fresh_db(tmp_path)
    result = runner.invoke(app, [
        "bench", "report", "reweight", "--weights", "0.5,0.5", "--decisions-db", str(db),
    ])
    assert result.exit_code != 0


def test_cli_reweight_rejects_weight_below_floor(tmp_path):
    db = _fresh_db(tmp_path)
    result = runner.invoke(app, [
        "bench", "report", "reweight", "--weights", "0.90,0.02,0.04,0.04", "--decisions-db", str(db),
    ])
    assert result.exit_code != 0


def test_cli_reweight_no_data(tmp_path):
    db = _fresh_db(tmp_path)
    result = runner.invoke(app, [
        "bench", "report", "reweight", "--weights", "0.20,0.55,0.20,0.05", "--decisions-db", str(db),
    ])
    assert result.exit_code == 0
    assert "No data" in result.output


def test_cli_reweight_logs_itself_to_bench_runs(tmp_path):
    """Every reweight call should leave a durable record of what was tested
    and why — this is the 'note every test we do' ledger."""
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama:qwen3.5", bucket="code", quality=0.9)
    conn.close()

    result = runner.invoke(app, [
        "bench", "report", "reweight",
        "--weights", "0.20,0.55,0.20,0.05",
        "--decisions-db", str(db),
        "--notes", "testing quality-dominant weights",
    ])
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT mode, notes, task_count_planned FROM bench_runs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    mode, notes, task_count = row
    assert mode == "reweight"
    assert task_count == 1
    assert "testing quality-dominant weights" in notes
    assert "weights=" in notes


def test_cli_runs_lists_logged_experiments(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama:qwen3.5", bucket="code", quality=0.9)
    conn.close()

    runner.invoke(app, [
        "bench", "report", "reweight", "--weights", "0.20,0.55,0.20,0.05",
        "--decisions-db", str(db), "--notes", "smoke test",
    ])

    result = runner.invoke(app, ["bench", "report", "runs", "--decisions-db", str(db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["mode"] == "reweight"
    assert "smoke test" in payload[0]["notes"]


def test_cli_runs_no_data(tmp_path):
    db = _fresh_db(tmp_path)
    result = runner.invoke(app, ["bench", "report", "runs", "--decisions-db", str(db)])
    assert result.exit_code == 0
    assert "No data" in result.output


def test_cli_runs_filters_by_mode(tmp_path):
    db = _fresh_db(tmp_path)
    conn = sqlite3.connect(str(db))
    _insert(conn, agent="ollama:qwen3.5", bucket="code", quality=0.9)
    conn.close()
    runner.invoke(app, [
        "bench", "report", "reweight", "--weights", "0.20,0.55,0.20,0.05",
        "--decisions-db", str(db),
    ])

    result = runner.invoke(app, [
        "bench", "report", "runs", "--decisions-db", str(db), "--mode", "bandit", "--json",
    ])
    assert result.exit_code == 0
    assert "No data" in result.output
