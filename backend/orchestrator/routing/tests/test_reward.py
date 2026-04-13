"""Tests for RewardCalculator."""
import pytest
from backend.orchestrator.routing.reward import RewardCalculator, TaskOutcome


def test_failed_task_zero_reward():
    calc = RewardCalculator()
    outcome = TaskOutcome(success=False, latency_s=1.0, cost_usd=0.0, quality_score=0.9, agent_name="aider")
    assert calc.compute(outcome) == 0.0


def test_perfect_outcome_near_one():
    """Fast, free, high quality → reward near 1.0."""
    calc = RewardCalculator()
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=0.0, quality_score=1.0, agent_name="aider")
    assert calc.compute(outcome) > 0.9


def test_slow_expensive_low_quality():
    """Slow, expensive, low quality → low reward."""
    calc = RewardCalculator()
    outcome = TaskOutcome(success=True, latency_s=60.0, cost_usd=0.10, quality_score=0.1, agent_name="claude")
    assert calc.compute(outcome) < 0.2


def test_weights_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        RewardCalculator(w_quality=0.5, w_speed=0.5, w_cost=0.5)


def test_different_weight_configs_produce_different_rewards():
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=0.09, quality_score=0.5, agent_name="a")
    r_quality = RewardCalculator(w_quality=0.8, w_speed=0.1, w_cost=0.1).compute(outcome)
    r_cost = RewardCalculator(w_quality=0.1, w_speed=0.1, w_cost=0.8).compute(outcome)
    # Cost-optimized config penalizes the expensive task more → lower reward
    assert r_cost < r_quality


def test_reward_clamped_to_01():
    calc = RewardCalculator()
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=0.0, quality_score=1.0, agent_name="a")
    r = calc.compute(outcome)
    assert 0.0 <= r <= 1.0


def test_latency_at_max_gives_zero_speed_component():
    """At MAX_LATENCY the speed score is 0; only quality + cost contribute."""
    calc = RewardCalculator(w_quality=0.4, w_speed=0.3, w_cost=0.3)
    outcome = TaskOutcome(success=True, latency_s=60.0, cost_usd=0.0, quality_score=1.0, agent_name="a")
    r = calc.compute(outcome)
    # speed_score = 0, cost_score = 1 → reward = 0.4*1 + 0.3*0 + 0.3*1 = 0.7
    assert r == pytest.approx(0.7, abs=1e-3)


def test_cost_beyond_max_clamped():
    """Cost above MAX_COST should be treated as MAX_COST (cost_score = 0)."""
    calc = RewardCalculator(w_quality=0.4, w_speed=0.3, w_cost=0.3)
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=999.0, quality_score=1.0, agent_name="a")
    r = calc.compute(outcome)
    # cost_score = 0 → reward = 0.4*1 + 0.3*1 + 0.3*0 = 0.7
    assert r == pytest.approx(0.7, abs=1e-3)


def test_error_message_does_not_affect_reward():
    """error_message is metadata; it should not change reward computation."""
    calc = RewardCalculator()
    base = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a")
    with_err = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a", error_message="some warning")
    assert calc.compute(base) == calc.compute(with_err)
