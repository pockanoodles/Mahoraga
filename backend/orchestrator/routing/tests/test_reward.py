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
    """Slow, expensive, low quality → lower reward than a perfect outcome.

    Note: with the per-bucket weight structure the success component dominates,
    so even a slow/expensive/low-quality task that succeeded scores above 0.4.
    The key invariant is that it scores *lower* than a fast/free/high-quality one.
    """
    calc = RewardCalculator()
    bad = TaskOutcome(success=True, latency_s=60.0, cost_usd=0.10, quality_score=0.1, agent_name="claude")
    good = TaskOutcome(success=True, latency_s=0.5, cost_usd=0.0, quality_score=0.9, agent_name="ollama")
    assert calc.compute(bad) < calc.compute(good)


def test_reward_clamped_to_01():
    calc = RewardCalculator()
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=0.0, quality_score=1.0, agent_name="a")
    r = calc.compute(outcome)
    assert 0.0 <= r <= 1.0


def test_high_latency_penalises_speed():
    """Very slow task (60 s) should score lower on speed than a fast task (0.5 s)."""
    calc = RewardCalculator()
    fast = TaskOutcome(success=True, latency_s=0.5, cost_usd=0.0, quality_score=0.8, agent_name="a")
    slow = TaskOutcome(success=True, latency_s=60.0, cost_usd=0.0, quality_score=0.8, agent_name="a")
    assert calc.compute(fast) > calc.compute(slow)


def test_high_cost_penalises_reward():
    """Expensive task should score lower than an equivalent free task."""
    calc = RewardCalculator()
    free = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.0, quality_score=0.8, agent_name="a")
    expensive = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.50, quality_score=0.8, agent_name="a")
    assert calc.compute(free) > calc.compute(expensive)


def test_error_message_does_not_affect_reward():
    """error_message is metadata; it should not change reward computation."""
    calc = RewardCalculator()
    base = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a")
    with_err = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a", error_message="some warning")
    assert calc.compute(base) == calc.compute(with_err)
