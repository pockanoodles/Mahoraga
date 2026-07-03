"""Edge-case tests for A2/A3/A4 boundaries.

These cover non-obvious failure modes I noticed during the grind pass:
- Stale quality_predictor cache after retraining
- Brain-index lazy init under concurrency
- A2 hint with missing variance field (UCB1/Thompson strategies)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing import brain_retrieval as br
from backend.orchestrator.routing import quality_predictor as qp
from backend.orchestrator.routing.brain_retrieval import BrainIndex
from backend.orchestrator.routing.quality_predictor import (
    QualityModel,
    fit,
    get_model,
    reset_loaded_model,
    TrainingRow,
    HANDCRAFT_DIM,
)
from backend.orchestrator.routing.uncertainty import compute_hint


# ── A3 stale cache ────────────────────────────────────────────────────────────


def _toy_model(weights_bias: float = 0.0) -> QualityModel:
    rows = [
        TrainingRow(np.zeros(HANDCRAFT_DIM, dtype=np.float32), "ollama", 1, 0.9, 1),
        TrainingRow(np.zeros(HANDCRAFT_DIM, dtype=np.float32), "ollama", 0, 0.3, 0),
        TrainingRow(np.zeros(HANDCRAFT_DIM, dtype=np.float32), "aider", 1, 0.9, 1),
        TrainingRow(np.zeros(HANDCRAFT_DIM, dtype=np.float32), "aider", 0, 0.3, 0),
    ]
    m = fit(rows, iters=50)
    m.bias += weights_bias
    return m


def test_get_model_loads_first_call(tmp_path: Path):
    reset_loaded_model()
    m = _toy_model()
    p = tmp_path / "model.json"
    m.save(p)
    out = get_model(p)
    assert out is not None
    assert out.bias == m.bias


def test_get_model_invalidates_on_mtime_change(tmp_path: Path):
    reset_loaded_model()
    m1 = _toy_model(weights_bias=0.0)
    p = tmp_path / "model.json"
    m1.save(p)
    out1 = get_model(p)
    assert out1.bias == m1.bias

    # Mutate weights, write again. Force mtime tick.
    time.sleep(0.01)
    m2 = _toy_model(weights_bias=5.0)
    m2.save(p)
    # Filesystem mtime resolution can be 1s on some systems — bump explicitly.
    new_mtime = m1.bias + 1000  # arbitrary
    Path(p).write_text(json.dumps(m2.to_dict()))
    import os
    os.utime(p, (time.time() + 1, time.time() + 1))

    out2 = get_model(p)
    assert out2.bias == m2.bias
    assert out2.bias != out1.bias


def test_get_model_returns_none_when_file_deleted(tmp_path: Path):
    reset_loaded_model()
    m = _toy_model()
    p = tmp_path / "model.json"
    m.save(p)
    assert get_model(p) is not None
    p.unlink()
    assert get_model(p) is None


def test_get_model_handles_corrupt_file(tmp_path: Path):
    reset_loaded_model()
    p = tmp_path / "model.json"
    p.write_text("not valid json {{")
    assert get_model(p) is None


# ── A4 brain-index concurrency ────────────────────────────────────────────────


def test_get_default_index_serialises_concurrent_builds(monkeypatch, tmp_path):
    """Two threads calling get_default_index() simultaneously must not
    each invoke build() — the lock should serialise."""
    br.reset_default_index()

    build_calls = {"n": 0}

    def fake_build(self):
        build_calls["n"] += 1
        # Simulate the slow work without doing it.
        time.sleep(0.05)
        return 0

    monkeypatch.setattr(BrainIndex, "build", fake_build)

    results: list[BrainIndex] = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()  # release all four together
        results.append(br.get_default_index())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one build should have run; all four threads should see the
    # same index instance.
    assert build_calls["n"] == 1
    assert len(results) == 4
    assert all(r is results[0] for r in results)


def test_force_rebuild_does_one_extra_build(monkeypatch):
    br.reset_default_index()
    build_calls = {"n": 0}

    def fake_build(self):
        build_calls["n"] += 1
        return 0

    monkeypatch.setattr(BrainIndex, "build", fake_build)

    a = br.get_default_index()
    b = br.get_default_index()
    assert a is b
    assert build_calls["n"] == 1
    c = br.get_default_index(force_rebuild=True)
    assert c is not a
    assert build_calls["n"] == 2


# ── A2 missing-variance graceful degradation ──────────────────────────────────


def test_uncertainty_hint_with_missing_variance_field():
    """UCB1/Thompson strategies don't expose 'variance'. The hint must
    not crash; selected_variance should be 0.0, escalation only triggered
    via decision_gap if at all."""
    scores = {
        "a": {"ucb": 1.0, "exploit": 0.7, "explore": 0.3},  # no 'variance' key
        "b": {"ucb": 0.9, "exploit": 0.6, "explore": 0.3},
    }
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy="claude",
    )
    assert h.selected_variance == 0.0
    assert h.decision_gap == pytest.approx(0.1)
    assert h.should_escalate is False  # neither variance nor gap fires


def test_uncertainty_hint_with_empty_scores():
    """If scores dict is empty entirely (degenerate strategy), don't crash."""
    h = compute_hint(
        "a", {},
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy="claude",
    )
    assert h.selected_variance == 0.0
    assert h.decision_gap == float("inf")
    assert h.should_escalate is False


def test_uncertainty_hint_selected_agent_not_in_scores():
    """If selected agent isn't in the scores dict (e.g. memory blending
    forced a pick outside the bandit's view), fall back gracefully."""
    scores = {"x": {"ucb": 1.0, "variance": 0.05}, "y": {"ucb": 0.9, "variance": 0.05}}
    h = compute_hint("z", scores, enabled=True)
    assert h.selected_variance == 0.0
    assert h.selected_agent == "z"


# ── A4 query-without-build is safe ────────────────────────────────────────────


def test_brain_query_before_build_returns_empty(tmp_path):
    idx = BrainIndex(brain_dir=tmp_path)
    # No build() call.
    assert idx.query("anything") == []
    assert idx.size == 0
    assert idx.available is False
