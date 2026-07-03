"""Tests for A3 — quality predictor retrain (staleness, hot-swap, safeguard)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.quality_predictor import (
    HANDCRAFT_DIM,
    MIN_AUC_FOR_SAVE,
    STALENESS_ABSOLUTE,
    STALENESS_RATIO,
    QualityModel,
    TrainingRow,
    fit,
    maybe_retrain,
    reset_loaded_model,
    retrain_and_swap,
    staleness_check,
    write_meta,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _build_db(path: Path, n_rows: int) -> None:
    """Build a decisions DB with the schema that load_training_rows expects."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            timestamp TEXT, task_id TEXT, task_goal TEXT,
            strategy TEXT, selected_agent TEXT,
            available_agents TEXT, context_vector TEXT, scores TEXT,
            success INTEGER, latency_s REAL, cost_usd REAL,
            quality_score REAL,
            quality_structural REAL, quality_novelty REAL,
            quality_not_plan REAL, quality_length REAL, quality_embed REAL,
            reward REAL, error_message TEXT, bench_run_id INTEGER
        );
    """)
    rng = np.random.default_rng(0)
    for i in range(n_rows):
        ctx = rng.normal(size=HANDCRAFT_DIM).tolist()
        agent = "ollama" if i % 2 == 0 else "aider"
        # Bias the data so AUC is high enough to clear the safeguard.
        success = 1 if (agent == "ollama") else 0
        quality = 0.9 if success == 1 else 0.3
        conn.execute(
            "INSERT INTO decisions (strategy, selected_agent, "
            "context_vector, success, quality_score) VALUES (?, ?, ?, ?, ?)",
            ("test", agent, json.dumps(ctx), success, quality),
        )
    conn.commit()
    conn.close()


def _toy_model() -> QualityModel:
    rng = np.random.default_rng(0)
    rows: list[TrainingRow] = []
    for _ in range(20):
        ctx = rng.normal(size=HANDCRAFT_DIM).astype(np.float32)
        rows.append(TrainingRow(ctx, "ollama", 1, 0.9, 1))
        rows.append(TrainingRow(ctx, "aider", 0, 0.3, 0))
    return fit(rows, iters=100)


# ── staleness_check ───────────────────────────────────────────────────────────


def test_staleness_no_meta_with_data_is_stale(tmp_path):
    db = tmp_path / "d.db"
    _build_db(db, 5)
    report = staleness_check(db_path=db, meta_path=tmp_path / "missing.json")
    assert report.is_stale is True
    assert report.reason == "no_meta"
    assert report.current_episode_count == 5


def test_staleness_no_meta_no_data_not_stale(tmp_path):
    db = tmp_path / "d.db"
    _build_db(db, 0)
    report = staleness_check(db_path=db, meta_path=tmp_path / "missing.json")
    assert report.is_stale is False


def test_staleness_ratio_triggers(tmp_path):
    """Trained at 100, current is 200 → ratio = 2.0 > 1.5 → stale."""
    db = tmp_path / "d.db"
    _build_db(db, 200)
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"trained_at_episode_count": 100}))
    report = staleness_check(db_path=db, meta_path=meta)
    assert report.is_stale is True
    assert report.reason == "ratio"


def test_staleness_absolute_triggers(tmp_path):
    """Trained at 10000, current is 10501 → ratio = 1.05 (no), diff = 501 (yes)."""
    db = tmp_path / "d.db"
    _build_db(db, 10501)
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"trained_at_episode_count": 10000}))
    report = staleness_check(db_path=db, meta_path=meta)
    assert report.is_stale is True
    assert report.reason == "absolute"


def test_staleness_fresh_when_close(tmp_path):
    db = tmp_path / "d.db"
    _build_db(db, 110)
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"trained_at_episode_count": 100}))
    report = staleness_check(db_path=db, meta_path=meta)
    assert report.is_stale is False
    assert report.reason == "fresh"


def test_staleness_handles_missing_db(tmp_path):
    """Missing DB → 0 current → not stale (no work to do yet)."""
    report = staleness_check(
        db_path=tmp_path / "nope.db",
        meta_path=tmp_path / "missing.json",
    )
    assert report.current_episode_count == 0
    assert report.is_stale is False


# ── write_meta + read_meta roundtrip ──────────────────────────────────────────


def test_write_meta_emits_expected_fields(tmp_path):
    model = _toy_model()
    meta_path = tmp_path / "meta.json"
    meta = write_meta(
        model, test_auc=0.85, spearman=0.6,
        episode_count=200, meta_path=meta_path,
    )
    assert meta["trained_at_episode_count"] == 200
    assert meta["test_auc"] == 0.85
    assert meta["spearman"] == 0.6
    assert meta["min_auc_for_save"] == MIN_AUC_FOR_SAVE
    assert "trained_at" in meta
    assert "feature_importances" in meta
    # Persisted JSON matches.
    on_disk = json.loads(meta_path.read_text())
    assert on_disk == meta


# ── retrain_and_swap ──────────────────────────────────────────────────────────


def test_retrain_and_swap_persists_when_auc_above_floor(tmp_path):
    reset_loaded_model()
    db = tmp_path / "d.db"
    _build_db(db, 80)
    model_path = tmp_path / "model.json"
    meta_path = tmp_path / "meta.json"
    out = retrain_and_swap(
        db_path=db, model_path=model_path, meta_path=meta_path,
    )
    assert out["accepted"] is True
    assert out["test_auc"] >= MIN_AUC_FOR_SAVE
    assert model_path.exists()
    assert meta_path.exists()


def test_retrain_rejects_when_too_few_rows(tmp_path):
    reset_loaded_model()
    db = tmp_path / "d.db"
    _build_db(db, 2)
    out = retrain_and_swap(
        db_path=db,
        model_path=tmp_path / "model.json",
        meta_path=tmp_path / "meta.json",
    )
    assert out["accepted"] is False
    assert out["reason"] == "insufficient_rows"


def test_retrain_rejects_below_safeguard_threshold(tmp_path, monkeypatch):
    """Force a degenerate retrain (low AUC) → must keep the old model."""
    reset_loaded_model()
    db = tmp_path / "d.db"
    _build_db(db, 60)
    model_path = tmp_path / "model.json"
    meta_path = tmp_path / "meta.json"
    # Pre-existing "good" model on disk.
    good_model = _toy_model()
    good_model.save(model_path)
    pre_bytes = model_path.read_bytes()

    # Force rejection by setting the safeguard above the achievable max
    # (AUC is bounded by 1.0). Real-world degenerate retrains would
    # produce sub-MIN_AUC_FOR_SAVE scores; this just simulates that.
    out = retrain_and_swap(
        db_path=db, model_path=model_path, meta_path=meta_path,
        min_auc=1.01,
    )
    assert out["accepted"] is False
    assert out["reason"].startswith("test_auc<")
    # Old model untouched.
    assert model_path.read_bytes() == pre_bytes


# ── maybe_retrain ─────────────────────────────────────────────────────────────


def test_maybe_retrain_skips_when_fresh(tmp_path):
    reset_loaded_model()
    db = tmp_path / "d.db"
    _build_db(db, 110)
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps({"trained_at_episode_count": 100}))
    out = maybe_retrain(
        db_path=db,
        model_path=tmp_path / "model.json",
        meta_path=meta_path,
    )
    assert out["retrained"] is False
    assert out["staleness"]["reason"] == "fresh"


def test_maybe_retrain_runs_when_stale(tmp_path):
    reset_loaded_model()
    db = tmp_path / "d.db"
    _build_db(db, 200)
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps({"trained_at_episode_count": 50}))
    out = maybe_retrain(
        db_path=db,
        model_path=tmp_path / "model.json",
        meta_path=meta_path,
    )
    assert out["staleness"]["is_stale"] is True
    assert out["retrained"] is True
    assert out["outcome"]["accepted"] is True
