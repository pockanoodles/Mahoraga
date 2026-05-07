"""Tests for A3 — learned quality scoring (routing/quality_predictor.py)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.quality_predictor import (
    HANDCRAFT_DIM,
    QualityModel,
    TrainingRow,
    _binary_auc,
    evaluate,
    featurise,
    fit,
    load_training_rows,
)


def _row(handcraft: list[float], agent: str, label: int, q: float | None = None) -> TrainingRow:
    return TrainingRow(
        handcraft=np.array(handcraft, dtype=np.float32),
        agent=agent,
        label=label,
        raw_quality=q,
        raw_success=label,
    )


# ── feature engineering ───────────────────────────────────────────────────────


def test_featurise_shape():
    h = np.zeros(HANDCRAFT_DIM, dtype=np.float32)
    f = featurise(h, "ollama", ["ollama", "codex-cli", "aider"])
    assert f.shape == (HANDCRAFT_DIM + 3,)
    assert f[HANDCRAFT_DIM + 0] == 1.0  # ollama hot
    assert f[HANDCRAFT_DIM + 1] == 0.0
    assert f[HANDCRAFT_DIM + 2] == 0.0


def test_featurise_unknown_agent_zero_onehot():
    h = np.zeros(HANDCRAFT_DIM, dtype=np.float32)
    f = featurise(h, "nope", ["ollama", "codex-cli"])
    assert f[HANDCRAFT_DIM:].sum() == 0.0


# ── AUC helper ────────────────────────────────────────────────────────────────


def test_binary_auc_perfect():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.8, 0.9])
    assert _binary_auc(y, p) == 1.0


def test_binary_auc_inverse():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.9, 0.8, 0.2, 0.1])
    assert _binary_auc(y, p) == 0.0


def test_binary_auc_random():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=200)
    p = rng.random(size=200)
    auc = _binary_auc(y, p)
    assert 0.4 < auc < 0.6  # near 0.5 for random


def test_binary_auc_degenerate_returns_half():
    assert _binary_auc(np.array([1, 1, 1]), np.array([0.5, 0.5, 0.5])) == 0.5


# ── fit ───────────────────────────────────────────────────────────────────────


def test_fit_learns_agent_main_effect():
    """Additive model should learn 'agent A is generally better than B' (AUC > 0.85)."""
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(80):
        ctx = rng.normal(size=HANDCRAFT_DIM).astype(np.float32)
        rows.append(_row(ctx.tolist(), "codex-cli", 1))  # codex always succeeds
        rows.append(_row(ctx.tolist(), "aider", 0))      # aider always fails
    model = fit(rows)
    assert model.train_auc > 0.85
    assert model.n_train == len(rows)
    assert set(model.agents) == {"codex-cli", "aider"}


def test_fit_learns_context_main_effect():
    """Additive model should learn 'high ctx[0] predicts success' regardless of agent."""
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(120):
        ctx = rng.normal(size=HANDCRAFT_DIM).astype(np.float32)
        agent = rng.choice(["codex-cli", "aider"])
        # Success is purely a function of context, independent of agent.
        label = 1 if ctx[0] > 0 else 0
        rows.append(_row(ctx.tolist(), str(agent), label))
    model = fit(rows)
    assert model.train_auc > 0.80


def test_fit_does_not_learn_pure_interaction():
    """Documents the model's known limitation: pure agent×context interactions
    are NOT learnable by an additive logistic. AUC stays near 0.5.

    This is intentional for v1: interactions blow up feature count and overfit
    on the typical ~100-row decisions DB. Future extension would add
    agent × bucket cross features once we have more data.
    """
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(80):
        ctx = rng.normal(size=HANDCRAFT_DIM).astype(np.float32)
        if ctx[0] > 0:
            rows.append(_row(ctx.tolist(), "codex-cli", 1))
            rows.append(_row(ctx.tolist(), "aider", 0))
        else:
            rows.append(_row(ctx.tolist(), "codex-cli", 0))
            rows.append(_row(ctx.tolist(), "aider", 1))
    model = fit(rows)
    assert 0.4 < model.train_auc < 0.7  # near chance, as expected


def test_fit_rejects_empty():
    with pytest.raises(ValueError, match="No training rows"):
        fit([])


def test_fit_rejects_one_class():
    rows = [_row([0.0] * HANDCRAFT_DIM, "ollama", 1) for _ in range(10)]
    with pytest.raises(ValueError, match="all-one-class"):
        fit(rows)


def test_predict_proba_in_unit_interval():
    rows = [
        _row([0.0] * HANDCRAFT_DIM, "ollama", 1),
        _row([0.0] * HANDCRAFT_DIM, "aider", 0),
        _row([1.0] * HANDCRAFT_DIM, "ollama", 1),
        _row([1.0] * HANDCRAFT_DIM, "aider", 0),
    ]
    m = fit(rows, iters=200)
    p = m.predict_proba(np.zeros(HANDCRAFT_DIM), "ollama")
    assert 0.0 <= p <= 1.0
    p2 = m.predict_proba(np.zeros(HANDCRAFT_DIM), "aider")
    assert 0.0 <= p2 <= 1.0


# ── persistence ───────────────────────────────────────────────────────────────


def test_model_save_load_roundtrip(tmp_path: Path):
    rng = np.random.default_rng(0)
    rows = [
        _row(rng.normal(size=HANDCRAFT_DIM).tolist(), "a", int(rng.random() > 0.5))
        for _ in range(10)
    ] + [
        _row(rng.normal(size=HANDCRAFT_DIM).tolist(), "b", int(rng.random() > 0.5))
        for _ in range(10)
    ]
    # Force at least one of each label class.
    rows[0].label = 1
    rows[1].label = 0
    m = fit(rows)
    p = tmp_path / "predictor.json"
    m.save(p)
    m2 = QualityModel.load(p)
    assert m2.weights == m.weights
    assert m2.bias == m.bias
    assert m2.agents == m.agents
    # Predictions match exactly post-roundtrip.
    h = np.zeros(HANDCRAFT_DIM)
    assert abs(m.predict_proba(h, "a") - m2.predict_proba(h, "a")) < 1e-9


# ── DB loader ─────────────────────────────────────────────────────────────────


def _build_db(tmp_path: Path, rows: list[tuple]) -> Path:
    """Create a minimal decisions DB for testing."""
    db = tmp_path / "decisions.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            task_id TEXT, task_goal TEXT,
            strategy TEXT, selected_agent TEXT,
            available_agents TEXT, context_vector TEXT, scores TEXT,
            success INTEGER, latency_s REAL, cost_usd REAL,
            quality_score REAL,
            quality_structural REAL, quality_novelty REAL,
            quality_not_plan REAL, quality_length REAL, quality_embed REAL,
            reward REAL, error_message TEXT, bench_run_id INTEGER
        );
    """)
    for ctx, agent, success, q in rows:
        conn.execute(
            "INSERT INTO decisions (timestamp, strategy, selected_agent, "
            "context_vector, success, quality_score) VALUES (?,?,?,?,?,?)",
            ("2026-01-01", "test", agent, json.dumps(ctx), success, q),
        )
    conn.commit()
    conn.close()
    return db


def test_load_training_rows_from_db(tmp_path: Path):
    rows = [
        ([0.1] * HANDCRAFT_DIM, "ollama", 1, 0.9),
        ([0.2] * HANDCRAFT_DIM, "ollama", 0, 0.3),
        ([0.3] * HANDCRAFT_DIM, "aider", 1, 0.8),
        ([0.4] * HANDCRAFT_DIM, "aider", None, 0.2),  # quality-only label
        ([0.5] * HANDCRAFT_DIM, "claude", 0, None),    # success-only label
    ]
    db = _build_db(tmp_path, rows)
    out = load_training_rows(db_path=db)
    assert len(out) == 5
    # First two are quality-based (0.9 ≥ 0.7 → 1, 0.3 < 0.7 → 0).
    assert out[0].label == 1
    assert out[1].label == 0
    # Quality-only row: quality 0.2 < 0.7 → 0.
    assert out[3].label == 0
    # Success-only row: success=0.
    assert out[4].label == 0


def test_load_training_rows_skips_malformed(tmp_path: Path):
    db = tmp_path / "decisions.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            timestamp TEXT, strategy TEXT, selected_agent TEXT,
            context_vector TEXT, success INTEGER, quality_score REAL
        );
    """)
    # Malformed JSON.
    conn.execute(
        "INSERT INTO decisions (strategy, selected_agent, context_vector, success) "
        "VALUES ('test', 'a', 'not_json', 1)"
    )
    # Wrong-length vector.
    conn.execute(
        "INSERT INTO decisions (strategy, selected_agent, context_vector, success) "
        "VALUES ('test', 'a', '[1,2,3]', 1)"
    )
    # Valid row.
    conn.execute(
        "INSERT INTO decisions (strategy, selected_agent, context_vector, success) "
        "VALUES ('test', 'a', ?, 1)",
        (json.dumps([0.0] * HANDCRAFT_DIM),),
    )
    conn.commit()
    conn.close()
    out = load_training_rows(db_path=db)
    assert len(out) == 1


def test_load_training_rows_missing_db(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_training_rows(db_path=tmp_path / "nope.db")


# ── evaluate ──────────────────────────────────────────────────────────────────


def test_evaluate_returns_holdout_metrics():
    """Evaluate on a separable scenario: codex-cli is reliably better than aider."""
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(60):
        ctx = rng.normal(size=HANDCRAFT_DIM)
        rows.append(_row(ctx.tolist(), "codex-cli", 1, q=0.9))
        rows.append(_row(ctx.tolist(), "aider", 0, q=0.3))
    _, report = evaluate(rows, seed=0, test_frac=0.25)
    assert report.n_total == len(rows)
    assert report.n_train + report.n_test == report.n_total
    assert 0.0 <= report.test_auc <= 1.0
    assert report.test_auc > 0.7  # marginal signal is strong


def test_evaluate_too_few_rows_raises():
    rows = [_row([0.0] * HANDCRAFT_DIM, "a", 1) for _ in range(2)]
    with pytest.raises(ValueError, match="at least 4"):
        evaluate(rows)
