"""Tests for bench report compat-matrix and cost subcommands."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from backend.orchestrator.cli.main import app

runner = CliRunner()

# Epoch values for 2026 dates (UTC)
_EPOCH_2026_JAN = "1767225600.0"  # 2026-01-01 00:00:00 UTC
_EPOCH_2026_APR = "1775001600.0"  # 2026-04-01 00:00:00 UTC


def _make_metrics_db(path: Path, rows: list[dict]) -> None:
    """Create a task_metrics DB at path with given rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE task_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            task_id TEXT,
            task_hash TEXT DEFAULT '',
            agent_name TEXT DEFAULT '',
            capability_bucket TEXT DEFAULT '',
            wall_time_ms REAL,
            routing_time_ms REAL,
            agent_spawn_time_ms REAL,
            tokens_generated INTEGER,
            tokens_per_second REAL,
            prompt_tokens INTEGER,
            prompt_eval_rate REAL,
            model_was_warm INTEGER,
            bandit_ucb_score REAL,
            bandit_exploration_flag INTEGER,
            reward_score REAL,
            success INTEGER,
            quality_score REAL,
            cost_usd REAL,
            implicit_quality REAL
        )
    """)
    for r in rows:
        conn.execute(
            """INSERT INTO task_metrics
               (timestamp, task_id, agent_name, capability_bucket,
                wall_time_ms, tokens_generated, tokens_per_second,
                prompt_tokens, reward_score, success, quality_score, cost_usd)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("timestamp", "1777000000.0"),
                r.get("task_id", "t1"),
                r.get("agent_name", "agent-a"),
                r.get("capability_bucket", "code"),
                r.get("wall_time_ms", 1000.0),
                r.get("tokens_generated", 100),
                r.get("tokens_per_second", 50.0),
                r.get("prompt_tokens"),
                r.get("reward_score", 0.8),
                r.get("success", 1),
                r.get("quality_score", 0.75),
                r.get("cost_usd"),
            ),
        )
    conn.commit()
    conn.close()


def _make_decisions_db(path: Path, rows: list[dict]) -> None:
    """Create a minimal decisions DB with bench_run_id column."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            task_id TEXT,
            task_goal TEXT,
            strategy TEXT DEFAULT '',
            selected_agent TEXT DEFAULT '',
            bench_run_id INTEGER
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO decisions (task_id, strategy, selected_agent, bench_run_id)"
            " VALUES (?,?,?,?)",
            (
                r["task_id"],
                r.get("strategy", "linucb"),
                r.get("agent", "a"),
                r.get("bench_run_id"),
            ),
        )
    conn.commit()
    conn.close()


def test_compat_matrix_aggregates_correctly(tmp_path):
    """Avg quality is computed correctly for a known set of rows."""
    db = tmp_path / "metrics.db"
    # 5 rows for (code, agent-a): avg = (0.8+0.9+0.7+0.6+1.0)/5 = 0.80
    rows = [
        {
            "task_id": f"t{i}",
            "agent_name": "agent-a",
            "capability_bucket": "code",
            "quality_score": q,
            "success": 1,
            "wall_time_ms": 2000.0,
        }
        for i, q in enumerate([0.8, 0.9, 0.7, 0.6, 1.0])
    ]
    _make_metrics_db(db, rows)

    result = runner.invoke(
        app, ["bench", "report", "compat-matrix", "--db", str(db), "--min-samples", "3"]
    )
    assert result.exit_code == 0
    assert "0.80" in result.output
    assert "agent-a" in result.output
    assert "code" in result.output


def test_min_samples_suppresses_cells(tmp_path):
    """Cells with fewer than --min-samples rows show — in the matrix body."""
    db = tmp_path / "metrics.db"
    # 3 rows for (code, agent-a), 1 row for (plan, agent-a)
    rows = [
        {
            "task_id": f"c{i}",
            "agent_name": "agent-a",
            "capability_bucket": "code",
            "quality_score": 0.7,
            "success": 1,
        }
        for i in range(3)
    ] + [
        {
            "task_id": "p1",
            "agent_name": "agent-a",
            "capability_bucket": "plan",
            "quality_score": 0.9,
            "success": 1,
        }
    ]
    _make_metrics_db(db, rows)

    result = runner.invoke(
        app, ["bench", "report", "compat-matrix", "--db", str(db), "--min-samples", "5"]
    )
    assert result.exit_code == 0
    assert "compat-matrix" in result.output
    # code cell (N=3) is below threshold — value must not appear in matrix body row
    lines = result.output.splitlines()
    code_line = next((ln for ln in lines if ln.strip().startswith("code")), None)
    assert code_line is not None
    assert "0.70" not in code_line


def test_bench_run_id_filters_correctly(tmp_path):
    """--bench-run-id restricts rows via join on the decisions DB."""
    db = tmp_path / "metrics.db"
    dec_db = tmp_path / "decisions.db"

    rows = [
        {"task_id": "t1", "agent_name": "agent-a", "capability_bucket": "code",
         "quality_score": 0.9, "success": 1},
        {"task_id": "t2", "agent_name": "agent-a", "capability_bucket": "code",
         "quality_score": 0.8, "success": 1},
        {"task_id": "t3", "agent_name": "agent-b", "capability_bucket": "code",
         "quality_score": 0.3, "success": 0},
        {"task_id": "t4", "agent_name": "agent-b", "capability_bucket": "code",
         "quality_score": 0.2, "success": 0},
    ]
    _make_metrics_db(db, rows)
    _make_decisions_db(dec_db, [
        {"task_id": "t1", "bench_run_id": 1},
        {"task_id": "t2", "bench_run_id": 1},
        {"task_id": "t3", "bench_run_id": 2},
        {"task_id": "t4", "bench_run_id": 2},
    ])

    result = runner.invoke(app, [
        "bench", "report", "compat-matrix",
        "--db", str(db),
        "--decisions-db", str(dec_db),
        "--bench-run-id", "1",
        "--min-samples", "1",
    ])
    assert result.exit_code == 0
    assert "agent-a" in result.output
    assert "agent-b" not in result.output


def test_json_output_valid(tmp_path):
    """--json emits valid JSON with matrix/per_agent/per_bucket keys."""
    db = tmp_path / "metrics.db"
    rows = [
        {"task_id": f"t{i}", "agent_name": "agent-a", "capability_bucket": "code",
         "quality_score": 0.7, "success": 1}
        for i in range(4)
    ]
    _make_metrics_db(db, rows)

    result = runner.invoke(app, [
        "bench", "report", "compat-matrix", "--db", str(db), "--json", "--min-samples", "1"
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "matrix" in data
    assert "per_agent" in data
    assert "per_bucket" in data
    assert "code" in data["matrix"]
    assert "agent-a" in data["matrix"]["code"]
    cell = data["matrix"]["code"]["agent-a"]
    assert cell["n"] == 4
    assert abs(cell["avg_quality"] - 0.7) < 0.001


def test_since_filter(tmp_path):
    """--since filters out rows with timestamps before the given date."""
    db = tmp_path / "metrics.db"
    rows = [
        {"task_id": "old1", "timestamp": _EPOCH_2026_JAN, "agent_name": "agent-a",
         "capability_bucket": "code", "quality_score": 0.4, "success": 0},
        {"task_id": "old2", "timestamp": _EPOCH_2026_JAN, "agent_name": "agent-a",
         "capability_bucket": "code", "quality_score": 0.4, "success": 0},
        {"task_id": "new1", "timestamp": _EPOCH_2026_APR, "agent_name": "agent-a",
         "capability_bucket": "code", "quality_score": 0.9, "success": 1},
        {"task_id": "new2", "timestamp": _EPOCH_2026_APR, "agent_name": "agent-a",
         "capability_bucket": "code", "quality_score": 0.9, "success": 1},
        {"task_id": "new3", "timestamp": _EPOCH_2026_APR, "agent_name": "agent-a",
         "capability_bucket": "code", "quality_score": 0.9, "success": 1},
    ]
    _make_metrics_db(db, rows)

    result = runner.invoke(app, [
        "bench", "report", "compat-matrix",
        "--db", str(db),
        "--since", "2026-04-01",
        "--min-samples", "1",
    ])
    assert result.exit_code == 0
    # Only April rows remain; avg quality = 0.90
    assert "0.90" in result.output
    # If January rows were included, avg would be (0.4*2 + 0.9*3)/5 = 0.62
    assert "0.62" not in result.output


def test_empty_db_no_error(tmp_path):
    """Empty DB returns exit 0 and 'No data' message."""
    db = tmp_path / "empty.db"
    _make_metrics_db(db, [])

    result = runner.invoke(app, ["bench", "report", "compat-matrix", "--db", str(db)])
    assert result.exit_code == 0
    assert "No data" in result.output


def test_csv_output(tmp_path):
    """--csv emits a header row followed by one row per (bucket, agent) cell."""
    db = tmp_path / "metrics.db"
    rows = [
        {"task_id": f"t{i}", "agent_name": "agent-a", "capability_bucket": "code",
         "quality_score": 0.7, "success": 1}
        for i in range(3)
    ]
    _make_metrics_db(db, rows)

    result = runner.invoke(app, [
        "bench", "report", "compat-matrix", "--db", str(db), "--csv", "--min-samples", "1"
    ])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("bucket,agent,n,")
    assert len(lines) == 2
    assert "code" in lines[1]
    assert "agent-a" in lines[1]
    assert ",3," in lines[1]


def _cost_rows() -> list[dict]:
    """Mixed local/cloud/unpriced rows with hand-checkable sonnet-4-6 math.

    At $3/M input + $15/M output:
      t1 local ok      cf = 0.1*3 + 0.2*15 = 3.3
      t2 local failed  cf = 0.1*3 + 0.1*15 = 1.8
      t3 local ok      cf = 0.2*3 + 0.1*15 = 2.1  (plan bucket)
      t4 cloud ok      cf = 1.8, actual = 1.8
      t5 local ok      unpriced (no token data)
    Totals: actual=1.8, counterfactual=9.0, avoided=7.2 (gross), 5.4 (success-only).
    """
    return [
        {"task_id": "t1", "agent_name": "ollama:qwen3.5", "capability_bucket": "code",
         "prompt_tokens": 100_000, "tokens_generated": 200_000, "cost_usd": 0.0, "success": 1},
        {"task_id": "t2", "agent_name": "ollama:qwen3.5", "capability_bucket": "code",
         "prompt_tokens": 100_000, "tokens_generated": 100_000, "cost_usd": 0.0, "success": 0},
        {"task_id": "t3", "agent_name": "ollama:granite4.1-8b", "capability_bucket": "plan",
         "prompt_tokens": 200_000, "tokens_generated": 100_000, "cost_usd": 0.0, "success": 1},
        {"task_id": "t4", "agent_name": "claude", "capability_bucket": "code",
         "prompt_tokens": 100_000, "tokens_generated": 100_000, "cost_usd": 1.8, "success": 1},
        {"task_id": "t5", "agent_name": "ollama:qwen3.5", "capability_bucket": "code",
         "prompt_tokens": None, "tokens_generated": None, "cost_usd": None, "success": 1},
    ]


def test_cost_totals_and_savings(tmp_path):
    """Human-readable output carries the right totals, savings, and coverage counts."""
    db = tmp_path / "metrics.db"
    _make_metrics_db(db, _cost_rows())

    result = runner.invoke(app, ["bench", "report", "cost", "--db", str(db)])
    assert result.exit_code == 0
    assert "local=4 [80.0%]" in result.output
    assert "cloud=1" in result.output
    assert "unpriced=1" in result.output
    assert "$1.8000" in result.output    # actual spend
    assert "$9.0000" in result.output    # all-cloud counterfactual
    assert "$7.2000" in result.output    # avoided, gross
    assert "$5.4000" in result.output    # avoided, success-only
    assert "80.0% of the all-cloud bill" in result.output
    assert "$1440.00" in result.output   # avoided per 1,000 tasks
    assert "claude-sonnet-4-6" in result.output
    assert "methodology (frozen)" in result.output


def test_cost_json_shape(tmp_path):
    """--json emits totals, per_bucket, and methodology with correct math."""
    db = tmp_path / "metrics.db"
    _make_metrics_db(db, _cost_rows())

    result = runner.invoke(app, ["bench", "report", "cost", "--db", str(db), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["reference_model"] == "claude-sonnet-4-6"
    assert data["pricing_as_of"]
    assert "methodology" in data

    t = data["totals"]
    assert t["n"] == 5
    assert t["n_local"] == 4
    assert t["n_cloud"] == 1
    assert t["n_unpriced"] == 1
    assert abs(t["local_share"] - 0.8) < 1e-9
    assert abs(t["actual_usd"] - 1.8) < 1e-6
    assert abs(t["counterfactual_usd"] - 9.0) < 1e-6
    assert abs(t["avoided_usd"] - 7.2) < 1e-6
    # Success-only excludes the failed local row (t2, cf=1.8)
    assert abs(t["avoided_success_usd"] - 5.4) < 1e-6
    assert abs(t["savings_pct"] - 80.0) < 1e-6
    assert abs(t["avoided_per_1k_tasks_usd"] - 1440.0) < 1e-6

    pb = data["per_bucket"]
    assert set(pb) == {"code", "plan"}
    assert pb["code"]["n"] == 4
    assert pb["code"]["n_local"] == 3
    assert pb["code"]["n_unpriced"] == 1
    assert abs(pb["code"]["avoided_usd"] - 5.1) < 1e-6
    assert abs(pb["plan"]["avoided_usd"] - 2.1) < 1e-6
    assert abs(pb["plan"]["local_share"] - 1.0) < 1e-9


def test_cost_reference_model_changes_counterfactual(tmp_path):
    """--reference-model reprices the counterfactual (opus = 5/25 per M)."""
    db = tmp_path / "metrics.db"
    _make_metrics_db(db, _cost_rows())

    result = runner.invoke(app, [
        "bench", "report", "cost", "--db", str(db), "--json",
        "--reference-model", "claude-opus-4-6",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    # 500k prompt + 500k generated priced rows: 0.5*5 + 0.5*25 = 15.0
    assert abs(data["totals"]["counterfactual_usd"] - 15.0) < 1e-6
    assert data["reference_model"] == "claude-opus-4-6"


def test_cost_unknown_reference_model_errors(tmp_path):
    """An unknown reference model fails fast instead of silently repricing."""
    db = tmp_path / "metrics.db"
    _make_metrics_db(db, _cost_rows())

    result = runner.invoke(app, [
        "bench", "report", "cost", "--db", str(db),
        "--reference-model", "not-a-model",
    ])
    assert result.exit_code == 1


def test_cost_csv_output(tmp_path):
    """--csv emits a header row followed by one row per bucket."""
    db = tmp_path / "metrics.db"
    _make_metrics_db(db, _cost_rows())

    result = runner.invoke(app, ["bench", "report", "cost", "--db", str(db), "--csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("bucket,n,n_local,n_unpriced,local_share,")
    assert len(lines) == 3
    code_line = next(ln for ln in lines[1:] if ln.startswith("code,"))
    assert code_line.startswith("code,4,3,1,0.7500,")
    assert "5.100000" in code_line


def test_cost_empty_db_no_error(tmp_path):
    """Empty DB returns exit 0 and 'No data' message."""
    db = tmp_path / "empty.db"
    _make_metrics_db(db, [])

    result = runner.invoke(app, ["bench", "report", "cost", "--db", str(db)])
    assert result.exit_code == 0
    assert "No data" in result.output
