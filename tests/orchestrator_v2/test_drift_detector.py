"""Tests for F5 drift detector — Welford stats + alert thresholding."""
from __future__ import annotations

import math

import pytest

from backend.orchestrator.routing.drift_detector import (
    DriftDetector,
    RunningStats,
    resolve_check_interval,
    resolve_enabled,
    resolve_min_obs,
    resolve_sigma,
    resolve_window,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MAHORAGA_DRIFT_ENABLED",
        "MAHORAGA_DRIFT_WINDOW",
        "MAHORAGA_DRIFT_SIGMA",
        "MAHORAGA_DRIFT_MIN_OBS",
        "MAHORAGA_DRIFT_CHECK_INTERVAL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_default_enabled():
    assert resolve_enabled() is True


def test_env_disable(monkeypatch):
    monkeypatch.setenv("MAHORAGA_DRIFT_ENABLED", "0")
    assert resolve_enabled() is False


def test_env_window_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_DRIFT_WINDOW", "100")
    assert resolve_window() == 100


def test_env_sigma_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_DRIFT_SIGMA", "1.5")
    assert resolve_sigma() == 1.5


def test_env_invalid_sigma_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_DRIFT_SIGMA", "not_a_number")
    assert resolve_sigma() == 2.0


def test_env_check_interval(monkeypatch):
    monkeypatch.setenv("MAHORAGA_DRIFT_CHECK_INTERVAL", "5")
    assert resolve_check_interval() == 5


def test_env_min_obs_default():
    assert resolve_min_obs() == 20


# ── RunningStats (Welford) ────────────────────────────────────────────────────


def test_welford_single_observation_inf_std():
    """count < 2 means we can't estimate variance — return +inf so the
    drift comparison never fires prematurely."""
    s = RunningStats()
    s.update(0.5)
    assert s.count == 1
    assert s.mean == 0.5
    assert math.isinf(s.std)


def test_welford_matches_naive_mean_variance():
    """Welford's online formula must produce the same result as naive
    two-pass mean/std on a finite sample."""
    values = [0.1, 0.4, 0.7, 0.9, 0.5, 0.6, 0.3, 0.8]
    s = RunningStats()
    for v in values:
        s.update(v)
    naive_mean = sum(values) / len(values)
    naive_var = sum((v - naive_mean) ** 2 for v in values) / (len(values) - 1)
    naive_std = math.sqrt(naive_var)
    assert s.mean == pytest.approx(naive_mean, abs=1e-9)
    assert s.std == pytest.approx(naive_std, abs=1e-9)
    assert s.count == len(values)


def test_welford_lower_bound_calculation():
    s = RunningStats()
    for _ in range(50):
        s.update(0.8)  # constant → std=0
    # std=0 means lower_bound == mean. Drift detector must not fire on
    # a constant-reward stream.
    assert s.lower_bound(2.0) == s.mean


# ── DriftDetector ─────────────────────────────────────────────────────────────


def test_no_alert_below_min_observations():
    """Acceptance criterion 3: <min_observations → no alert even if
    rewards drop hard. Without enough history we don't trust the mean."""
    d = DriftDetector(window_size=20, sigma_threshold=2.0, min_observations=20, check_interval=1)
    for i in range(15):
        result = d.check("code", "ollama", 0.0)
        assert result is None


def test_alert_on_clear_regression():
    """Acceptance criterion 1: rolling mean meaningfully below
    historical → fire. Welford's online std grows when later samples
    land far from the running mean, so the test uses a long burn-in
    (so the mean is anchored) and a small window (so the rolling
    crater dominates) to guarantee the alert fires within a reasonable
    number of bad samples."""
    d = DriftDetector(
        window_size=10, sigma_threshold=2.0,
        min_observations=20, check_interval=10,
    )
    # Long burn-in: 200 strong rewards anchors mean ≈ 0.85, std ≈ 0.02.
    import random
    rng = random.Random(0)
    for _ in range(200):
        d.check("code", "ollama", 0.85 + rng.gauss(0, 0.02))
    # Crater: 30 zero-rewards. The rolling-window mean (last 10) hits 0
    # within 10 zeros, while the historical mean is still ~0.85 and
    # std is still small. By the third check_interval boundary the
    # signal has to fire.
    alerts = []
    for _ in range(30):
        a = d.check("code", "ollama", 0.0)
        if a is not None:
            alerts.append(a)
    assert alerts, "expected at least one DriftAlert after a crater"
    a = alerts[-1]
    assert a.bucket == "code"
    assert a.agent == "ollama"
    assert a.deviation_sigmas > 2.0
    assert a.window_mean < a.historical_mean


def test_no_alert_under_normal_variation():
    """Stable agent, normal noise → no alert across hundreds of obs."""
    import random
    d = DriftDetector(
        window_size=20, sigma_threshold=2.0,
        min_observations=20, check_interval=5,
    )
    rng = random.Random(42)
    fired = []
    for _ in range(300):
        a = d.check("research", "gemini-cli", 0.7 + rng.gauss(0, 0.10))
        if a is not None:
            fired.append(a)
    # 2σ tail with 300 i.i.d. samples, checking every 5 steps = 60
    # checks. False-positive expectation is low but nonzero — accept
    # up to a small handful.
    assert len(fired) <= 3


def test_check_interval_rate_limits_alerts():
    """check_interval=10 means we evaluate the alert condition only
    every 10th observation, never on every step. We observe this by
    feeding a regression and counting alerts — at most one per
    check_interval boundary, not one per observation."""
    d = DriftDetector(
        window_size=10, sigma_threshold=2.0,
        min_observations=20, check_interval=10,
    )
    import random
    rng = random.Random(0)
    # Long burn-in so the mean is solidly anchored before the crater.
    for _ in range(200):
        d.check("code", "ollama", 0.8 + rng.gauss(0, 0.02))
    # 100 zero-reward observations → 10 check_interval boundaries
    # (counts 210, 220, ..., 300).
    n_alerts = 0
    for _ in range(100):
        if d.check("code", "ollama", 0.0) is not None:
            n_alerts += 1
    assert 1 <= n_alerts <= 10


def test_window_evicts_old_observations():
    """The rolling window respects maxlen — old entries fall out."""
    d = DriftDetector(
        window_size=5, sigma_threshold=2.0,
        min_observations=10, check_interval=1,
    )
    for _ in range(20):
        d.check("code", "ollama", 0.5)
    win = d._windows[("code", "ollama")]
    assert len(win) == 5  # capped


def test_per_cell_isolation():
    """Drift in (code, ollama) must not affect (research, ollama) or
    (code, aider)."""
    d = DriftDetector(window_size=10, sigma_threshold=2.0, min_observations=10, check_interval=1)
    for _ in range(30):
        d.check("code", "ollama", 0.0)
        d.check("research", "ollama", 0.85)
        d.check("code", "aider", 0.85)
    # ollama in code should have a low mean.
    code_ollama = d.stats_for("code", "ollama")
    research_ollama = d.stats_for("research", "ollama")
    code_aider = d.stats_for("code", "aider")
    assert code_ollama.mean < research_ollama.mean
    assert code_aider.mean > code_ollama.mean


def test_constant_reward_never_fires():
    """std=0 → lower_bound == mean → window_mean (also const) is not
    strictly below. No alert on a perfectly stable stream."""
    d = DriftDetector(
        window_size=10, sigma_threshold=2.0,
        min_observations=10, check_interval=1,
    )
    fired = False
    for _ in range(100):
        if d.check("code", "ollama", 0.7) is not None:
            fired = True
    assert not fired


def test_alert_fields_populated():
    """All DriftAlert fields should be set + serialisable."""
    import random
    d = DriftDetector(
        window_size=10, sigma_threshold=2.0,
        min_observations=10, check_interval=1,
    )
    rng = random.Random(7)
    # Long burn-in to anchor mean/std.
    for _ in range(200):
        d.check("code", "ollama", 0.85 + rng.gauss(0, 0.02))
    alert = None
    for _ in range(50):
        a = d.check("code", "ollama", 0.0)
        if a is not None:
            alert = a
            break
    assert alert is not None
    d_dict = alert.to_dict()
    assert d_dict["bucket"] == "code"
    assert d_dict["agent"] == "ollama"
    assert "deviation_sigmas" in d_dict
    assert "window_size" in d_dict
