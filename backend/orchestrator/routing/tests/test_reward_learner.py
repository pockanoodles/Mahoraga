"""Tests for RewardWeightLearner — OLS per-bucket weight adaptation."""
import math
import pytest
from backend.orchestrator.routing.reward import BUCKET_WEIGHTS
from backend.orchestrator.routing.reward_learner import (
    RewardWeightLearner,
    MIN_SAMPLES,
    WEIGHT_FLOOR,
    _phi_speed,
    _phi_cost,
    _project_simplex,
)
import numpy as np


# ── _project_simplex ──────────────────────────────────────────────────────────

def test_project_simplex_positive_weights():
    w = np.array([0.6, 0.2, 0.1, 0.1])
    result = _project_simplex(w)
    assert abs(result.sum() - 1.0) < 1e-9
    assert all(result >= WEIGHT_FLOOR)


def test_project_simplex_negative_weights_floored():
    """Negative weights should be clipped to WEIGHT_FLOOR, then renormalised."""
    w = np.array([-0.5, 1.5, -0.1, 0.1])
    result = _project_simplex(w)
    assert abs(result.sum() - 1.0) < 1e-9
    assert all(result >= WEIGHT_FLOOR)


def test_project_simplex_uniform_stays_uniform():
    w = np.array([0.25, 0.25, 0.25, 0.25])
    result = _project_simplex(w)
    assert np.allclose(result, 0.25)


# ── Helper functions ──────────────────────────────────────────────────────────

def test_phi_speed_at_reference_time():
    """phi_speed(T_REF) should equal exp(-1) ≈ 0.368."""
    from backend.orchestrator.routing.reward_learner import _phi_speed
    from backend.orchestrator.routing.reward import _SPEED_T_REF
    result = _phi_speed(_SPEED_T_REF)
    assert abs(result - math.exp(-1.0)) < 1e-9


def test_phi_speed_zero_latency():
    assert _phi_speed(0.0) == 1.0


def test_phi_cost_zero_cost():
    assert _phi_cost(0.0) == pytest.approx(1.0 - math.tanh(0.0))


# ── RewardWeightLearner ───────────────────────────────────────────────────────

def test_get_weights_returns_default_before_convergence():
    """Before MIN_SAMPLES observations, fall back to BUCKET_WEIGHTS priors."""
    learner = RewardWeightLearner()
    w = learner.get_weights("code")
    assert w == BUCKET_WEIGHTS["code"]


def test_get_weights_unknown_bucket_falls_back_to_general():
    learner = RewardWeightLearner()
    w = learner.get_weights("unknown_bucket_xyz")
    assert w == BUCKET_WEIGHTS["general"]


def test_has_learned_false_before_convergence():
    learner = RewardWeightLearner()
    learner.observe("code", latency_s=2.0, cost_usd=0.0, quality=0.8, reward=0.7)
    assert not learner.has_learned("code")


def test_sample_counts_tracks_observations():
    learner = RewardWeightLearner()
    for _ in range(5):
        learner.observe("research", latency_s=4.0, cost_usd=0.0, quality=0.75, reward=0.72)
    assert learner.sample_counts()["research"] == 5


def test_converges_after_min_samples():
    """After MIN_SAMPLES observations, has_learned should be True."""
    learner = RewardWeightLearner()
    import random
    rng = random.Random(123)
    for i in range(MIN_SAMPLES):
        lat = rng.uniform(0.5, 8.0)
        qual = rng.uniform(0.5, 1.0)
        cost = rng.uniform(0.0, 0.02)
        # reward computed with default code weights
        from backend.orchestrator.routing.reward import (
            BUCKET_WEIGHTS, _SPEED_LAMBDA, _SPEED_T_REF, _COST_REF,
        )
        w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS["code"]
        phi_sp = math.exp(-_SPEED_LAMBDA * lat / _SPEED_T_REF)
        phi_c = 1.0 - math.tanh(cost / _COST_REF)
        reward = w_s + w_q * qual + w_sp * phi_sp + w_c * phi_c
        learner.observe("code", latency_s=lat, cost_usd=cost, quality=qual, reward=reward)

    assert learner.has_learned("code")


def test_learned_weights_sum_to_one():
    """Learned weights must always lie on the probability simplex."""
    learner = RewardWeightLearner()
    import random
    rng = random.Random(42)
    for _ in range(MIN_SAMPLES):
        lat = rng.uniform(1.0, 10.0)
        qual = rng.uniform(0.4, 1.0)
        # Deliberately skewed reward signal: quality dominates
        reward = 0.1 + 0.7 * qual + 0.1 * _phi_speed(lat) + 0.1 * _phi_cost(0.0)
        learner.observe("research", latency_s=lat, cost_usd=0.0, quality=qual, reward=reward)

    w = learner.get_weights("research")
    assert abs(sum(w) - 1.0) < 1e-6
    assert all(wi >= WEIGHT_FLOOR for wi in w)


def test_learned_weights_shift_toward_signal():
    """If reward correlates strongly with quality, w_quality should increase above prior."""
    learner = RewardWeightLearner()
    import random
    rng = random.Random(7)
    for _ in range(MIN_SAMPLES + 50):
        qual = rng.uniform(0.3, 1.0)
        lat = rng.uniform(1.0, 3.0)
        # reward is almost entirely quality-driven
        reward = 0.05 + 0.85 * qual + 0.05 * _phi_speed(lat) + 0.05 * _phi_cost(0.0)
        learner.observe("review", latency_s=lat, cost_usd=0.0, quality=qual, reward=reward)

    w_s, w_q, w_sp, w_c = learner.get_weights("review")
    prior_w_q = BUCKET_WEIGHTS["review"][1]
    # Learned quality weight should be strictly higher than the prior
    assert w_q > prior_w_q, f"Expected w_q ({w_q:.4f}) > prior ({prior_w_q:.4f})"


def test_convergence_status_reports_all_buckets():
    learner = RewardWeightLearner()
    learner.observe("code", latency_s=2.0, cost_usd=0.0, quality=0.8, reward=0.7)
    learner.observe("debug", latency_s=1.5, cost_usd=0.0, quality=0.9, reward=0.8)
    status = learner.convergence_status()
    assert "code" in status
    assert "debug" in status
    assert status["code"]["samples"] == 1
    assert not status["code"]["converged"]


def test_buffer_capped_at_max_buffer():
    from backend.orchestrator.routing.reward_learner import MAX_BUFFER
    learner = RewardWeightLearner()
    import random
    rng = random.Random(0)
    for _ in range(MAX_BUFFER + 50):
        learner.observe("plan", latency_s=rng.uniform(1, 5), cost_usd=0.0,
                        quality=rng.uniform(0.5, 1.0), reward=rng.uniform(0.5, 1.0))
    assert len(learner._buffer["plan"]) == MAX_BUFFER


def test_save_load_roundtrip(tmp_path):
    """Learned weights survive a save/load cycle."""
    state_path = tmp_path / "bandit_state.json"
    learner = RewardWeightLearner(state_path=state_path)

    import random
    rng = random.Random(99)
    for _ in range(MIN_SAMPLES):
        lat = rng.uniform(0.5, 6.0)
        qual = rng.uniform(0.4, 1.0)
        reward = 0.15 + 0.65 * qual + 0.1 * _phi_speed(lat) + 0.1 * _phi_cost(0.0)
        learner.observe("test", latency_s=lat, cost_usd=0.0, quality=qual, reward=reward)

    assert learner.has_learned("test")
    original_weights = learner.get_weights("test")

    # Load into a fresh instance
    learner2 = RewardWeightLearner(state_path=state_path)
    assert learner2.has_learned("test")
    assert learner2.get_weights("test") == original_weights


def test_observe_default_correctness_keeps_old_call_sites_working():
    """Old call sites omit `correctness`; the buffered row's first column is 1.0."""
    learner = RewardWeightLearner()
    learner.observe("code", latency_s=2.0, cost_usd=0.0, quality=0.8, reward=0.7)
    assert learner._buffer["code"][0][0] == 1.0


def test_observe_records_correctness_column():
    learner = RewardWeightLearner()
    learner.observe("code", latency_s=2.0, cost_usd=0.0, quality=0.8, reward=0.3,
                    correctness=0.0)
    learner.observe("code", latency_s=2.0, cost_usd=0.0, quality=0.8, reward=0.9,
                    correctness=1.0)
    assert [row[0] for row in learner._buffer["code"]] == [0.0, 1.0]


def test_learned_weights_on_simplex_with_varying_correctness():
    """OLS over a varying correctness column still projects onto the simplex."""
    learner = RewardWeightLearner()
    import random
    rng = random.Random(21)
    w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS["code"]
    for _ in range(MIN_SAMPLES + 20):
        lat = rng.uniform(0.5, 8.0)
        qual = rng.uniform(0.4, 1.0)
        corr = rng.choice([0.0, 1.0])
        reward = w_s * corr + w_q * qual + w_sp * _phi_speed(lat) + w_c * _phi_cost(0.0)
        learner.observe("code", latency_s=lat, cost_usd=0.0, quality=qual,
                        reward=reward, correctness=corr)

    assert learner.has_learned("code")
    w = learner.get_weights("code")
    assert abs(sum(w) - 1.0) < 1e-6
    assert all(wi >= WEIGHT_FLOOR for wi in w)


def test_reward_calculator_uses_learned_weights():
    """RewardCalculator should use learned weights once converged."""
    from backend.orchestrator.routing.reward import RewardCalculator, TaskOutcome

    learner = RewardWeightLearner()
    calc_with_learner = RewardCalculator(learner=learner)
    calc_default = RewardCalculator()

    # Before convergence — both should return the same reward
    outcome = TaskOutcome(
        success=True, latency_s=3.0, cost_usd=0.0, quality_score=0.8,
        agent_name="aider", bucket="code",
    )
    assert calc_with_learner.compute(outcome) == calc_default.compute(outcome)

    # Feed quality-biased signal until convergence
    import random
    rng = random.Random(55)
    for _ in range(MIN_SAMPLES):
        lat = rng.uniform(0.5, 6.0)
        qual = rng.uniform(0.4, 1.0)
        reward = 0.05 + 0.85 * qual + 0.05 * _phi_speed(lat) + 0.05 * _phi_cost(0.0)
        learner.observe("code", latency_s=lat, cost_usd=0.0, quality=qual, reward=reward)

    # After convergence — rewards should differ (learned weights override defaults)
    learned_r = calc_with_learner.compute(outcome)
    default_r = calc_default.compute(outcome)
    assert learned_r != default_r
