"""Tests for execution-based ("verifiable") reward scoring (verify_replay.py
+ `orch bench report verify`)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from backend.orchestrator.cli.main import app
from backend.orchestrator.routing.verify_replay import (
    CaseResult,
    _rank_comparison,
    _spearman,
    evaluate,
    load_bank,
    load_results,
    run_case,
    summarize,
)

runner = CliRunner()


# ── run_case() — the sandbox primitive ──────────────────────────────────────

def test_run_case_correct_solution_passes():
    output = "def add(a, b):\n    return a + b"
    passed, err = run_case(output, "assert add(2, 3) == 5")
    assert passed is True
    assert err == ""


def test_run_case_wrong_solution_fails():
    output = "def add(a, b):\n    return a - b"
    passed, err = run_case(output, "assert add(2, 3) == 5")
    assert passed is False
    assert "AssertionError" in err or err  # some stderr captured


def test_run_case_extracts_fenced_code():
    output = "Here's the solution:\n```python\ndef sq(x):\n    return x * x\n```\nHope that helps!"
    passed, _ = run_case(output, "assert sq(4) == 16")
    assert passed is True


def test_run_case_empty_output_fails():
    passed, _ = run_case("", "assert add(1, 1) == 2")
    assert passed is False


def test_run_case_runtime_error_fails():
    output = "def boom():\n    raise ValueError('x')"
    passed, err = run_case(output, "boom()")
    assert passed is False
    assert "ValueError" in err


# ── load_bank() / load_results() ────────────────────────────────────────────

def test_load_bank_parses_and_skips_comments(tmp_path: Path):
    bank_file = tmp_path / "bank.jsonl"
    bank_file.write_text(
        "# a comment\n"
        + json.dumps({"prompt": "P1", "bucket": "code", "tier": "easy",
                      "entrypoint": "f", "tests": "assert f() == 1"}) + "\n"
        + "\n"  # blank line
        + json.dumps({"prompt": "P2", "bucket": "debug", "tests": "assert g() == 2"}) + "\n"
        # missing tests -> skipped
        + json.dumps({"prompt": "P3", "bucket": "code"}) + "\n"
    )
    bank = load_bank(bank_file)
    assert set(bank.keys()) == {"P1", "P2"}
    assert bank["P1"]["entrypoint"] == "f"
    assert bank["P2"]["bucket"] == "debug"


def test_load_results_uses_full_fields_and_falls_back(tmp_path: Path):
    res = tmp_path / "results.jsonl"
    res.write_text(
        json.dumps({"prompt_full": "P1", "output_full": "code1",
                    "actual_agent": "ollama:a", "bucket": "code"}) + "\n"
        + json.dumps({"prompt": "P2", "agent": "ollama:b", "bucket": "debug"}) + "\n"  # fallbacks
        + "not json\n"
        + json.dumps({"output_full": "orphan"}) + "\n"  # no prompt/agent -> skipped
    )
    rows = load_results(res)
    assert len(rows) == 2
    assert rows[0] == {"prompt": "P1", "agent": "ollama:a", "output": "code1", "bucket": "code"}
    assert rows[1]["output"] == ""  # no output_full -> empty string


# ── evaluate() ──────────────────────────────────────────────────────────────

def test_evaluate_matches_bank_and_counts_unmatched():
    bank = {
        "P1": {"bucket": "code", "tier": "easy", "entrypoint": "f",
               "tests": "assert f() == 1"},
    }
    results = [
        {"prompt": "P1", "agent": "ollama:good", "output": "def f():\n    return 1", "bucket": "code"},
        {"prompt": "P1", "agent": "ollama:bad", "output": "def f():\n    return 9", "bucket": "code"},
        {"prompt": "UNKNOWN", "agent": "ollama:good", "output": "x", "bucket": "code"},
    ]
    cases, unmatched = evaluate(bank, results)
    assert unmatched == 1
    assert len(cases) == 2
    good = next(c for c in cases if c.agent == "ollama:good")
    bad = next(c for c in cases if c.agent == "ollama:bad")
    assert good.passed is True
    assert bad.passed is False


# ── summarize() — aggregation + rank disagreement ───────────────────────────

def test_summarize_pass_rate_and_overall():
    cases = [
        CaseResult("ollama:good", "code", "f", True, False, ""),
        CaseResult("ollama:good", "code", "g", True, False, ""),
        CaseResult("ollama:weak", "code", "f", False, False, "err"),
        CaseResult("ollama:weak", "code", "g", False, True, "err"),
    ]
    result = summarize(cases, results=[])  # empty results -> heuristic column None
    assert result["overall"]["ollama:good"]["pass_rate"] == 1.0
    assert result["overall"]["ollama:weak"]["pass_rate"] == 0.0
    assert result["by_bucket"]["code"]["ollama:good"]["passed"] == 2
    assert result["by_bucket"]["code"]["ollama:weak"]["empty"] == 1
    # no results rows -> heuristic quality unavailable
    assert result["overall"]["ollama:good"]["heuristic_quality"] is None


def test_summarize_computes_heuristic_column_from_results():
    cases = [CaseResult("ollama:a", "code", "f", True, False, "")]
    results = [{"prompt": "write f", "agent": "ollama:a",
                "output": "def f():\n    return 1", "bucket": "code"}]
    result = summarize(cases, results)
    # score_heuristic is real + sync; a code output should get a numeric score
    assert result["overall"]["ollama:a"]["heuristic_quality"] is not None
    assert 0.0 <= result["overall"]["ollama:a"]["heuristic_quality"] <= 1.0


# ── _spearman() / _rank_comparison() ────────────────────────────────────────

def test_spearman_perfect_and_inverse():
    assert _spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert _spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0


def test_spearman_none_below_three_points():
    assert _spearman([1, 2], [2, 1]) is None


def test_rank_comparison_flags_top_inversion():
    # execution says "a" is most-correct, but heuristic ranks it last.
    overall = {
        "a": {"n": 5, "passed": 5, "pass_rate": 1.0, "heuristic_quality": 0.60},
        "b": {"n": 5, "passed": 3, "pass_rate": 0.6, "heuristic_quality": 0.90},
        "c": {"n": 5, "passed": 2, "pass_rate": 0.4, "heuristic_quality": 0.80},
    }
    rc = _rank_comparison(overall)
    assert rc["best_by_exec"] == "a"
    assert rc["best_by_heuristic"] == "b"
    assert rc["inverted"] is True
    assert rc["best_by_exec_heuristic_rank"] == 3  # last of 3


def test_rank_comparison_no_inversion_when_aligned():
    overall = {
        "a": {"n": 5, "passed": 5, "pass_rate": 1.0, "heuristic_quality": 0.90},
        "b": {"n": 5, "passed": 3, "pass_rate": 0.6, "heuristic_quality": 0.70},
    }
    rc = _rank_comparison(overall)
    assert rc["inverted"] is False
    assert rc["best_by_exec"] == "a"


# ── CLI: orch bench report verify ───────────────────────────────────────────

def test_verify_cmd_end_to_end(tmp_path: Path):
    bank_file = tmp_path / "bank.jsonl"
    bank_file.write_text(
        json.dumps({"prompt": "Write f() returning 1", "bucket": "code",
                    "tier": "easy", "entrypoint": "f", "tests": "assert f() == 1"}) + "\n"
        + json.dumps({"prompt": "Write g() returning 2", "bucket": "code",
                      "tier": "easy", "entrypoint": "g", "tests": "assert g() == 2"}) + "\n"
    )
    results_file = tmp_path / "results.jsonl"
    results_file.write_text(
        # strong arm: both correct
        json.dumps({"prompt_full": "Write f() returning 1", "output_full": "def f():\n    return 1",
                    "actual_agent": "ollama:strong", "bucket": "code"}) + "\n"
        + json.dumps({"prompt_full": "Write g() returning 2", "output_full": "def g():\n    return 2",
                      "actual_agent": "ollama:strong", "bucket": "code"}) + "\n"
        # canary arm: both wrong
        + json.dumps({"prompt_full": "Write f() returning 1", "output_full": "def f():\n    return 0",
                      "actual_agent": "ollama:canary", "bucket": "code"}) + "\n"
        + json.dumps({"prompt_full": "Write g() returning 2", "output_full": "not code at all",
                      "actual_agent": "ollama:canary", "bucket": "code"}) + "\n"
    )
    decisions_db = tmp_path / "routing_decisions.db"
    conn = sqlite3.connect(decisions_db)
    conn.execute(
        "CREATE TABLE bench_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, "
        "ended_at TEXT, mode TEXT, task_count_planned INT, task_count_completed INT, notes TEXT)"
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, [
        "bench", "report", "verify",
        "-i", str(results_file),
        "--bank", str(bank_file),
        "--decisions-db", str(decisions_db),
    ])
    assert result.exit_code == 0, result.output
    # strong arm passes all, canary passes none
    assert "pass@1=1.000" in result.output
    assert "pass@1=0.000" in result.output
    # the offline run was logged to the ledger
    conn = sqlite3.connect(decisions_db)
    row = conn.execute("SELECT mode, task_count_completed, notes FROM bench_runs").fetchone()
    conn.close()
    assert row[0] == "verify"
    assert row[1] == 4
    assert "pass@1_by_agent" in row[2]


def test_verify_cmd_json_output(tmp_path: Path):
    bank_file = tmp_path / "bank.jsonl"
    bank_file.write_text(
        json.dumps({"prompt": "Write f", "bucket": "code", "entrypoint": "f",
                    "tests": "assert f() == 1"}) + "\n"
    )
    results_file = tmp_path / "results.jsonl"
    results_file.write_text(
        json.dumps({"prompt_full": "Write f", "output_full": "def f():\n    return 1",
                    "actual_agent": "ollama:a", "bucket": "code"}) + "\n"
    )
    decisions_db = tmp_path / "d.db"
    conn = sqlite3.connect(decisions_db)
    conn.execute(
        "CREATE TABLE bench_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, "
        "ended_at TEXT, mode TEXT, task_count_planned INT, task_count_completed INT, notes TEXT)"
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, [
        "bench", "report", "verify",
        "-i", str(results_file), "--bank", str(bank_file),
        "--decisions-db", str(decisions_db), "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["overall"]["ollama:a"]["pass_rate"] == 1.0
