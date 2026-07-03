"""
§6 / §9.1 pair #4 — OLS learner ↔ drift detector adversarial tests.

Fix B closes the false-positive where a hard OLS weight replacement would
trigger a DriftAlert on every arm in the bucket simultaneously.

Two directions required by spec §6.4:

  Positive (catches Fix B regression):
    OLS _fit() fires after 100 obs; smoothed transition keeps per-step
    reward shift ≤5% of OLS-shift → drift detector stays quiet for the
    subsequent 20-step transition window.

  Negative (catches drift detector regression):
    Genuine reward collapse (not OLS-induced) → DriftAlert fires and
    the arm enters quarantine.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from backend.orchestrator.routing.drift_detector import DriftAlert, DriftDetector
from backend.orchestrator.routing.quarantine import QuarantineManager
from backend.orchestrator.routing.reward import BUCKET_WEIGHTS, RewardCalculator, TaskOutcome
from backend.orchestrator.routing.reward_learner import (
    MIN_SAMPLES,
    OLS_TRANSITION_STEPS,
    RewardWeightLearner,
    _project_simplex,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _phi_speed(latency_s: float, ref: float = 5.0) -> float:
    return math.exp(-latency_s / ref)


def _phi_cost(cost_usd: float, ref: float = 0.05) -> float:
    return 1.0 - math.tanh(cost_usd / ref)


def _outcome(
    quality: float = 0.7,
    latency_s: float = 2.0,
    cost_usd: float = 0.0,
    agent: str = "ollama:qwen3.5",
    bucket: str = "code",
    success: bool = True,
) -> TaskOutcome:
    return TaskOutcome(
        success=success,
        latency_s=latency_s,
        cost_usd=cost_usd,
        quality_score=quality,
        agent_name=agent,
        bucket=bucket,
    )


# ── Fix B unit test (pure math) ───────────────────────────────────────────────

def test_fix_b_per_step_shift_bounded_at_one_over_k():
    """_fit() blends toward OLS target: |effective - prior| == (1/K) * |ols - prior|."""
    learner = RewardWeightLearner(state_path=None)
    bucket = "code"
    prior = np.array(BUCKET_WEIGHTS[bucket], dtype=float)

    # Seed buffer: observations that drive OLS far from the prior.
    # Vary quality so OLS has feature rank; target reward skews toward quality.
    rng = np.random.default_rng(42)
    for _ in range(MIN_SAMPLES):
        quality = float(rng.uniform(0.0, 1.0))
        phi_sp = _phi_speed(8.0)   # slow → low speed score
        phi_c = _phi_cost(0.0)
        # Ground truth: quality-dominated weights (0.20, 0.60, 0.15, 0.05)
        target_reward = 0.20 + 0.60 * quality + 0.15 * phi_sp + 0.05 * phi_c
        target_reward = float(np.clip(target_reward + rng.normal(0, 0.01), 0.0, 1.0))
        learner._buffer.setdefault(bucket, []).append(
            (1.0, quality, phi_sp, phi_c, target_reward)
        )

    # Compute what raw OLS gives (without Fix B) for the assertion.
    data = np.array(learner._buffer[bucket], dtype=float)
    x_mat, y_vec = data[:, :4], data[:, 4]
    w_raw, *_ = np.linalg.lstsq(x_mat, y_vec, rcond=None)
    ols_proj = _project_simplex(w_raw)

    learner._fit(bucket)
    assert learner.has_learned(bucket), "OLS should have produced learned weights"

    effective = np.array(learner.get_weights(bucket), dtype=float)
    step_actual = float(np.linalg.norm(effective - prior))
    step_expected = float(np.linalg.norm(ols_proj - prior)) / OLS_TRANSITION_STEPS

    assert step_actual <= step_expected + 1e-5, (
        f"Fix B: step={step_actual:.5f} exceeds (1/K)*OLS-shift={step_expected:.5f} "
        f"(K={OLS_TRANSITION_STEPS})"
    )


# ── Positive direction (Fix B prevents drift on OLS transition) ───────────────

def test_ols_transition_does_not_trigger_drift():
    """OLS fires at obs 100; Fix B keeps per-step reward shift below drift threshold.

    Setup:  100 stable observations anchor the drift detector's historical mean;
            the same observations feed the OLS buffer so _fit() fires at the end.
    Trigger: the 100th learner.observe() call triggers _fit() (Fix B smoothed).
    Assert: no DriftAlert raised during the OLS fit + 20-step smoothed window.
    """
    bucket = "code"
    agent = "ollama:qwen3.5"

    # Aggressive thresholds so the detector is active early.
    detector = DriftDetector(
        window_size=20,
        sigma_threshold=2.0,
        min_observations=20,
        check_interval=1,
    )

    # RewardCalculator uses the learner — weights update after OLS fires.
    learner = RewardWeightLearner(state_path=None)
    calc = RewardCalculator(learner=learner)

    alerts: list[DriftAlert] = []

    # Phase 1: 100 observations — stable rewards, OLS buffer fills up.
    # quality=0.7, latency=2s, cost=free → consistent prior-based reward.
    rng = np.random.default_rng(7)
    for _ in range(MIN_SAMPLES):
        quality = float(rng.uniform(0.6, 0.8))  # narrow band for stable drift history
        outcome = _outcome(quality=quality, latency_s=2.0, bucket=bucket, agent=agent)
        reward = calc.compute(outcome)
        alert = detector.check(bucket, agent, reward)
        if alert:
            alerts.append(alert)
        # Feed learner with varied quality so OLS has feature rank.
        learner.observe(bucket, latency_s=2.0, cost_usd=0.0, quality=quality, reward=reward)

    # OLS should have fired at obs 100.
    assert learner.has_learned(bucket), "OLS must have converged after MIN_SAMPLES obs"
    assert not alerts, f"Unexpected DriftAlert before OLS fired: {alerts}"

    # Phase 2: 20 steps post-OLS — rewards computed with blended weights.
    for _ in range(OLS_TRANSITION_STEPS):
        quality = float(rng.uniform(0.6, 0.8))
        outcome = _outcome(quality=quality, latency_s=2.0, bucket=bucket, agent=agent)
        reward = calc.compute(outcome)
        alert = detector.check(bucket, agent, reward)
        if alert:
            alerts.append(alert)
        learner.observe(bucket, latency_s=2.0, cost_usd=0.0, quality=quality, reward=reward)

    assert not alerts, (
        f"Fix B failed: DriftAlert raised during {OLS_TRANSITION_STEPS}-step "
        f"smoothed OLS transition: {[a.to_dict() for a in alerts]}"
    )


# ── Negative direction (genuine drift IS detected) ────────────────────────────

def test_genuine_reward_collapse_triggers_drift_and_quarantine(tmp_path, monkeypatch):
    """Real reward crash (not OLS-induced) must trigger DriftAlert + quarantine.

    This test verifies the drift detector is still sensitive — Fix B must not
    dampen genuine drift signals, only OLS-transition artifacts.
    """
    bucket = "code"
    agent = "ollama:qwen3.5"

    # Aggressive params: detect fast so the test is short.
    monkeypatch.setenv("MAHORAGA_DRIFT_MIN_OBS", "20")
    monkeypatch.setenv("MAHORAGA_DRIFT_CHECK_INTERVAL", "1")
    monkeypatch.setenv("MAHORAGA_DRIFT_WINDOW", "10")
    monkeypatch.setenv("MAHORAGA_DRIFT_SIGMA", "2.0")

    from backend.orchestrator.routing import quarantine as _q
    monkeypatch.setattr(_q, "QUARANTINE_STATE_PATH", tmp_path / "q.json")

    detector = DriftDetector(
        window_size=10,
        sigma_threshold=2.0,
        min_observations=20,
        check_interval=1,
    )
    quarantine = QuarantineManager.load()

    # Anchor: 200 high-reward observations.  Needs to be large so the
    # historical std stays tight (~0.02) even after a handful of crashes
    # are added to the running stats — otherwise std inflates and the
    # lower_bound widens past the window_mean, masking the signal.
    rng = np.random.default_rng(0)
    for _ in range(200):
        detector.check(bucket, agent, float(rng.normal(0.85, 0.02)))

    # Crash: 20 zero-reward observations (genuine agent failure).
    alerts: list[DriftAlert] = []
    for _ in range(20):
        alert = detector.check(bucket, agent, 0.0)
        if alert:
            alerts.append(alert)
            quarantine.quarantine(alert, kind="drift_auto")

    assert alerts, "Drift detector must fire on genuine reward collapse"
    assert quarantine.is_quarantined(bucket, agent), (
        f"Arm must enter quarantine after DriftAlert; got entries: {quarantine.all_entries()}"
    )
