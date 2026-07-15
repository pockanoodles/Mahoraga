"""Tests for the offline quality-scorer discriminability experiment
(quality_replay.py + `orch bench report quality-replay`)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from backend.orchestrator.cli.main import app
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.quality import score_heuristic
from backend.orchestrator.routing.quality_replay import (
    BASELINE,
    ScorerConfig,
    load_rows,
    score_variant,
    summarize,
)

runner = CliRunner()


# ── score_variant() parity with production quality.py under BASELINE ───────


def test_baseline_variant_matches_production_prose():
    prompt = "What is a hash table?"
    output = (
        "A hash table maps keys to values using a hash function to compute "
        "an index into an array of buckets. Average-case lookup, insert, and "
        "delete are O(1), though worst-case degrades to O(n) under heavy "
        "collisions. Load factor and resizing strategy govern this tradeoff."
    )
    assert score_variant(prompt, output, "research", BASELINE) == score_heuristic(
        prompt, output, "research"
    )


def test_baseline_variant_matches_production_code():
    output = "def square(n):\n    return n ** 2\n"
    assert score_variant("square a number", output, "code", BASELINE) == score_heuristic(
        "square a number", output, "code"
    )


def test_baseline_variant_matches_production_security():
    output = (
        "This is vulnerable to CWE-89 SQL injection. Mitigate by using "
        "parameterized queries and input validation; also apply least "
        "privilege to the DB account."
    )
    assert score_variant("how to prevent sqli", output, "security", BASELINE) == score_heuristic(
        "how to prevent sqli", output, "security"
    )


# ── Variant knobs actually change the score in the expected direction ──────


def test_higher_length_plateau_widens_gap_between_medium_and_long_prose():
    """Baseline's 300-word plateau means a 200-word and 600-word answer score
    almost identically on the length component (both near/at the cap). A
    variant with an 800-word plateau makes saturation harder, so the same
    two real outputs should spread further apart — this is the mechanism
    the diagnostic suspects is compressing agent differences."""
    prompt = "explain something"
    short_output = " ".join(["substantive"] * 200) + ". This is a real sentence here."
    long_output = " ".join(["substantive"] * 600) + ". This is a real sentence here."
    cfg = ScorerConfig(name="higher_plateau", prose_length_plateau_words=800.0)

    baseline_gap = score_variant(prompt, long_output, "research", BASELINE) - score_variant(
        prompt, short_output, "research", BASELINE
    )
    variant_gap = score_variant(prompt, long_output, "research", cfg) - score_variant(
        prompt, short_output, "research", cfg
    )
    assert variant_gap > baseline_gap


def test_uncapped_security_keywords_scores_higher_for_keyword_rich_text():
    output = (
        "Mitigations: sanitize, validate, escape, parameterize, least "
        "privilege, defense in depth, rate limit, encrypt, tls, hash, salt, "
        "csrf, xss protections, input validation, output encoding, auth, "
        "rbac, acl, permission checks, audit log, secrets management, "
        "key rotation, patch management, remediation, mitigation, hardening."
    )
    capped = score_variant("secure this app", output, "security", BASELINE)
    uncapped_cfg = ScorerConfig(
        name="uncapped",
        security_mitigation_cap=10.0,
        security_threat_cap=10.0,
        clamp_to_one=False,
    )
    uncapped = score_variant("secure this app", output, "security", uncapped_cfg)
    assert uncapped > capped


def test_continuous_not_plan_grades_a_gradient_not_a_cliff():
    """A numbered-list answer with 17 words/line total (just under the
    binary cutoff of 18) scores 0.0 on not_plan under baseline, but should
    score partial credit under the continuous variant since it's right at
    the boundary, not a real short-stub plan."""
    lines = [f"{i}. " + " ".join(["word"] * 16) for i in range(1, 6)]
    output = "\n".join(lines)
    baseline_score = score_variant("explain a process", output, "research", BASELINE)
    cont_cfg = ScorerConfig(name="continuous", not_plan_continuous=True)
    cont_score = score_variant("explain a process", output, "research", cont_cfg)
    assert cont_score > baseline_score


# ── load_rows() ──────────────────────────────────────────────────────────────


def test_load_rows_skips_rows_missing_full_text_fields(tmp_path):
    path = tmp_path / "bench.jsonl"
    path.write_text(
        json.dumps({"prompt": "hi", "output_preview": "hi back", "bucket": "general",
                    "actual_agent": "a", "success": True}) + "\n"
    )
    assert load_rows(path) == []


def test_load_rows_skips_failed_tasks(tmp_path):
    path = tmp_path / "bench.jsonl"
    path.write_text(
        json.dumps({
            "prompt_full": "hi", "output_full": "hi back", "bucket": "general",
            "actual_agent": "a", "success": False,
        }) + "\n"
    )
    assert load_rows(path) == []


def test_load_rows_extracts_expected_fields(tmp_path):
    path = tmp_path / "bench.jsonl"
    path.write_text(
        json.dumps({
            "prompt_full": "what is a firewall", "output_full": "a firewall filters traffic",
            "bucket": "security", "actual_agent": "ollama:qwen3.5", "success": True,
            "tier": "easy", "tokens": 12,
        }) + "\n"
    )
    rows = load_rows(path)
    assert len(rows) == 1
    r = rows[0]
    assert r["agent"] == "ollama:qwen3.5"
    assert r["bucket"] == "security"
    assert r["prompt"] == "what is a firewall"
    assert r["output"] == "a firewall filters traffic"
    assert r["tier"] == "easy"
    assert r["tokens"] == 12


def test_load_rows_missing_file_returns_empty(tmp_path):
    assert load_rows(tmp_path / "nope.jsonl") == []


# ── summarize() ──────────────────────────────────────────────────────────────


def test_summarize_reports_gap_per_config():
    rows = [
        {"agent": "a", "bucket": "code", "prompt": "square a number",
         "output": "def square(n):\n    return n ** 2\n", "tokens": 10},
        {"agent": "b", "bucket": "code", "prompt": "square a number",
         "output": "n ** 2", "tokens": 3},
    ]
    result = summarize(rows, [BASELINE])
    assert "baseline" in result
    assert "code" in result["baseline"]["per_bucket"]
    cell = result["baseline"]["per_bucket"]["code"]
    assert cell["n"] == 2
    assert set(cell["avg_by_agent"]) == {"a", "b"}


def test_summarize_multiple_configs_each_reported():
    rows = [
        {"agent": "a", "bucket": "research", "prompt": "what is x",
         "output": "x is a thing that does y and z in the system overall", "tokens": 40},
    ]
    cfg2 = ScorerConfig(name="other", prose_length_plateau_words=1000.0)
    result = summarize(rows, [BASELINE, cfg2])
    assert set(result.keys()) == {"baseline", "other"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decisions.db"
    DecisionLogger(db_path=db)
    return db


def _bench_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "bench.jsonl"
    rows = [
        {"prompt_full": "square a number", "output_full": "def square(n):\n    return n ** 2\n",
         "bucket": "code", "actual_agent": "ollama:qwen3.5", "success": True, "tokens": 12},
        {"prompt_full": "square a number", "output_full": "n**2",
         "bucket": "code", "actual_agent": "ollama:granite4.1-8b", "success": True, "tokens": 3},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_cli_quality_replay_runs_end_to_end(tmp_path):
    db = _fresh_db(tmp_path)
    bench_path = _bench_jsonl(tmp_path)

    result = runner.invoke(app, [
        "bench", "report", "quality-replay",
        "--input", str(bench_path),
        "--decisions-db", str(db),
        "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "baseline" in payload
    assert "code" in payload["baseline"]["per_bucket"]


def test_cli_quality_replay_no_data(tmp_path):
    db = _fresh_db(tmp_path)
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("")

    result = runner.invoke(app, [
        "bench", "report", "quality-replay",
        "--input", str(empty_path),
        "--decisions-db", str(db),
    ])
    assert result.exit_code == 0
    assert "No usable rows" in result.output


def test_cli_quality_replay_logs_itself_to_bench_runs(tmp_path):
    db = _fresh_db(tmp_path)
    bench_path = _bench_jsonl(tmp_path)

    result = runner.invoke(app, [
        "bench", "report", "quality-replay",
        "--input", str(bench_path),
        "--decisions-db", str(db),
        "--notes", "testing scorer discriminability",
    ])
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT mode, notes, task_count_planned FROM bench_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    mode, notes, task_count = row
    assert mode == "quality-replay"
    assert task_count == 2
    assert "testing scorer discriminability" in notes
    assert "baseline_gap=" in notes
