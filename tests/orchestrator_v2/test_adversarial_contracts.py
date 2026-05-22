"""
§9 — Adversarial integration tests for module-pair contracts.

Three of the six required pairs (§9.1):

  Pair 1 — (classifier, scorer):       security prompt → _score_security fires.
  Pair 2 — (scorer, reward):           security quality score → BUCKET_WEIGHTS["security"].
  Pair 3 — (reward, OLS learner):      100 obs → learned θ matches OLS optimum.

  Pair 4 — (OLS learner, drift):       test_ols_drift_interaction.py
  Pair 5 — (drift, quarantine):        test_drift_quarantine_integration.py
  Pair 6 — (quarantine, bandit):       test_drift_quarantine_integration.py
"""
from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np
import pytest

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.reward import BUCKET_WEIGHTS, RewardCalculator, TaskOutcome
from backend.orchestrator.routing.reward_learner import MIN_SAMPLES, RewardWeightLearner
from backend.orchestrator.routing.strategies.static import classify_bucket


# ── helpers ──────────────────────────────────────────────────────────────────

def _ctx(prompt: str) -> TaskContext:
    return TaskContext.from_task(type("T", (), {"goal": prompt})())


def _phi_speed(latency_s: float, ref: float = 5.0) -> float:
    return math.exp(-latency_s / ref)


def _phi_cost(cost_usd: float, ref: float = 0.05) -> float:
    return 1.0 - math.tanh(cost_usd / ref)


# ── Pair 1: classifier → scorer ───────────────────────────────────────────────

_SECURITY_PROMPT = "audit the login endpoint for SQL injection and XSS vulnerabilities"
_CODE_PROMPT     = "implement a REST endpoint that queries the database and returns paginated results"
_STUB_OUTPUT     = "This analysis identifies three injection vectors..."


def test_classifier_routes_security_prompt_to_security_scorer():
    """Classifier emits 'security'; score_heuristic then calls _score_security, not _score_code."""
    from backend.orchestrator.routing import quality

    ctx = _ctx(_SECURITY_PROMPT)
    bucket = classify_bucket(ctx)
    assert bucket == "security", (
        f"Classifier must emit 'security' for this prompt, got {bucket!r}"
    )

    # Mock both scorer paths to record which one fires.
    security_called = []
    code_called = []

    original_security = quality._score_security
    original_code     = quality._score_code

    with (
        patch.object(quality, "_score_security", side_effect=lambda o: (security_called.append(True), original_security(o))[-1]),
        patch.object(quality, "_score_code",     side_effect=lambda o: (code_called.append(True), original_code(o))[-1]),
    ):
        quality.score_heuristic(_SECURITY_PROMPT, _STUB_OUTPUT, bucket)

    assert security_called, "_score_security was not invoked for a security-bucket task"
    assert not code_called, "_score_code was incorrectly invoked for a security-bucket task"


def test_classifier_routes_code_prompt_to_code_scorer():
    """Classifier emits 'code'; score_heuristic then calls _score_code, not _score_security."""
    from backend.orchestrator.routing import quality

    ctx = _ctx(_CODE_PROMPT)
    bucket = classify_bucket(ctx)
    assert bucket == "code", (
        f"Classifier must emit 'code' for this prompt, got {bucket!r}"
    )

    security_called = []
    code_called = []

    original_security = quality._score_security
    original_code     = quality._score_code

    with (
        patch.object(quality, "_score_security", side_effect=lambda o: (security_called.append(True), original_security(o))[-1]),
        patch.object(quality, "_score_code",     side_effect=lambda o: (code_called.append(True), original_code(o))[-1]),
    ):
        quality.score_heuristic(_CODE_PROMPT, _STUB_OUTPUT, bucket)

    assert code_called, "_score_code was not invoked for a code-bucket task"
    assert not security_called, "_score_security was incorrectly invoked for a code-bucket task"


# ── Pair 2: scorer → reward ───────────────────────────────────────────────────

def test_reward_uses_security_weights_not_general():
    """RewardCalculator uses BUCKET_WEIGHTS['security'], not ['general'], for security tasks.

    Verify by computing expected reward with each weight vector and asserting
    the actual reward matches the security-specific computation.
    """
    # High-quality security task: success=True, quality=0.9, fast, free.
    outcome = TaskOutcome(
        success=True,
        latency_s=1.5,
        cost_usd=0.0,
        quality_score=0.9,
        agent_name="ollama:qwen3.5",
        bucket="security",
    )

    calc = RewardCalculator()
    actual = calc.compute(outcome)

    phi_speed = _phi_speed(outcome.latency_s)
    phi_cost  = _phi_cost(outcome.cost_usd)

    w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS["security"]
    expected_security = w_s * 1.0 + w_q * 0.9 + w_sp * phi_speed + w_c * phi_cost
    expected_security = round(min(1.0, max(0.0, expected_security)), 4)

    # Sanity check: 'security' and 'general' weights must differ.
    w_s2, w_q2, w_sp2, w_c2 = BUCKET_WEIGHTS["general"]
    expected_general = w_s2 * 1.0 + w_q2 * 0.9 + w_sp2 * phi_speed + w_c2 * phi_cost
    expected_general = round(min(1.0, max(0.0, expected_general)), 4)

    assert expected_security != expected_general, (
        "BUCKET_WEIGHTS['security'] and BUCKET_WEIGHTS['general'] produce identical rewards "
        "for this task — the test cannot distinguish the two paths"
    )
    assert actual == expected_security, (
        f"RewardCalculator used wrong weights for 'security' bucket: "
        f"got {actual}, expected {expected_security} (security weights); "
        f"general weights would give {expected_general}"
    )


def test_reward_failure_returns_zero_regardless_of_bucket():
    """Success=False → reward=0.0 for any bucket (failure short-circuits weight selection)."""
    for bucket in BUCKET_WEIGHTS:
        outcome = TaskOutcome(
            success=False,
            latency_s=1.0,
            cost_usd=0.0,
            quality_score=0.9,
            agent_name="ollama:qwen3.5",
            bucket=bucket,
        )
        assert RewardCalculator().compute(outcome) == 0.0, (
            f"Expected 0.0 for failed task in bucket {bucket!r}"
        )


# ── Pair 3: reward → OLS learner ─────────────────────────────────────────────

def test_ols_learned_weights_match_constant_reward_optimum():
    """100 observations with a known reward function → learned θ approximates OLS optimum.

    Ground-truth weights: (0.30, 0.40, 0.20, 0.10) for 'plan' bucket.
    Feed 100 observations with random features and rewards generated from that
    weight vector (plus small noise).  After _fit(), the stored weights must
    produce predictions close to the ground truth on held-out samples.
    """
    bucket = "plan"
    true_w = np.array([0.30, 0.40, 0.20, 0.10])

    rng = np.random.default_rng(99)
    learner = RewardWeightLearner(state_path=None)

    # Build buffer directly so test doesn't depend on MIN_SAMPLES being exactly 100.
    for _ in range(MIN_SAMPLES):
        quality  = float(rng.uniform(0.3, 0.9))
        phi_sp   = float(_phi_speed(rng.uniform(1.0, 8.0)))
        phi_c    = float(_phi_cost(rng.uniform(0.0, 0.05)))
        features = np.array([1.0, quality, phi_sp, phi_c])
        noise    = float(rng.normal(0, 0.01))
        reward   = float(np.clip(true_w @ features + noise, 0.0, 1.0))
        learner._buffer.setdefault(bucket, []).append(
            (1.0, quality, phi_sp, phi_c, reward)
        )

    # Fix B: each _fit() call blends only 1/K of the way toward the OLS solution.
    # Call _fit() K times so the effective weights converge close to the OLS optimum.
    from backend.orchestrator.routing.reward_learner import OLS_TRANSITION_STEPS
    for _ in range(OLS_TRANSITION_STEPS):
        learner._fit(bucket)
    assert learner.has_learned(bucket), "OLS must converge after MIN_SAMPLES observations"

    learned = np.array(learner.get_weights(bucket))

    # Evaluate predictive accuracy on 50 fresh held-out samples.
    errors = []
    for _ in range(50):
        quality = float(rng.uniform(0.3, 0.9))
        phi_sp  = float(_phi_speed(rng.uniform(1.0, 8.0)))
        phi_c   = float(_phi_cost(rng.uniform(0.0, 0.05)))
        features = np.array([1.0, quality, phi_sp, phi_c])
        true_r    = float(np.clip(true_w @ features, 0.0, 1.0))
        pred_r    = float(np.clip(learned @ features, 0.0, 1.0))
        errors.append(abs(pred_r - true_r))

    mae = float(np.mean(errors))
    assert mae < 0.05, (
        f"OLS learned weights do not approximate the constant-reward ground truth "
        f"after {OLS_TRANSITION_STEPS} Fix-B blend steps: "
        f"MAE={mae:.4f} on held-out set (threshold=0.05). "
        f"True weights: {true_w}, learned: {learned}"
    )
