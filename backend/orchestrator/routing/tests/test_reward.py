"""Tests for RewardCalculator — updated for per-bucket weights + exp speed decay."""
import math
from backend.orchestrator.routing.reward import (
    RewardCalculator, TaskOutcome, BUCKET_WEIGHTS,
    _SPEED_LAMBDA, _SPEED_T_REF, _COST_REF,
)


def test_failed_task_zero_reward():
    calc = RewardCalculator()
    outcome = TaskOutcome(success=False, latency_s=1.0, cost_usd=0.0, quality_score=0.9, agent_name="aider")
    assert calc.compute(outcome) == 0.0


def test_perfect_outcome_near_one():
    """Instant, free, perfect quality → reward near 1.0."""
    calc = RewardCalculator()
    outcome = TaskOutcome(success=True, latency_s=0.0, cost_usd=0.0, quality_score=1.0, agent_name="aider")
    assert calc.compute(outcome) > 0.9


def test_slow_expensive_low_quality():
    """Slow, expensive, low quality → lower reward than a perfect outcome."""
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


def test_bucket_weights_used():
    """Bucket weights produce different rewards for the same outcome."""
    calc = RewardCalculator()
    kw = dict(success=True, latency_s=30.0, cost_usd=0.0, quality_score=1.0, agent_name="a")
    r_code     = calc.compute(TaskOutcome(**kw, bucket="code"))
    r_research = calc.compute(TaskOutcome(**kw, bucket="research"))
    assert r_code != r_research


def test_exp_speed_decay():
    """Speed score at reference time should be exp(-1) ≈ 0.37."""
    phi = math.exp(-_SPEED_LAMBDA * _SPEED_T_REF / _SPEED_T_REF)
    assert abs(phi - math.exp(-1.0)) < 1e-9


def test_swap_penalty_applied_for_slow_spawn():
    """A task with high spawn_time_ms should score lower than same task without it."""
    calc = RewardCalculator()
    base = TaskOutcome(success=True, latency_s=2.0, cost_usd=0.0, quality_score=0.8, agent_name="a")
    slow = TaskOutcome(success=True, latency_s=2.0, cost_usd=0.0, quality_score=0.8, agent_name="a", spawn_time_ms=6000.0)
    assert calc.compute(slow) < calc.compute(base)


def test_swap_penalty_not_applied_for_trivial_spawn():
    """Spawn time below threshold (500 ms) should not trigger swap penalty."""
    calc = RewardCalculator()
    base = TaskOutcome(success=True, latency_s=2.0, cost_usd=0.0, quality_score=0.8, agent_name="a")
    fast_spawn = TaskOutcome(success=True, latency_s=2.0, cost_usd=0.0, quality_score=0.8, agent_name="a", spawn_time_ms=200.0)
    assert calc.compute(base) == calc.compute(fast_spawn)


def test_error_message_does_not_affect_reward():
    calc = RewardCalculator()
    base     = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a")
    with_err = TaskOutcome(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8, agent_name="a", error_message="warn")
    assert calc.compute(base) == calc.compute(with_err)


def test_all_bucket_weights_sum_to_one():
    for bucket, (ws, wq, wsp, wc) in BUCKET_WEIGHTS.items():
        total = ws + wq + wsp + wc
        assert abs(total - 1.0) < 1e-9, f"Bucket {bucket!r} weights sum to {total}, not 1.0"


# ── Correctness coefficient (Era 20 reward-fidelity judge) ────────────────────

def test_correctness_none_reproduces_legacy_reward():
    """No judge (correctness=None) must be bit-for-bit the legacy formula."""
    calc = RewardCalculator()
    kw = dict(success=True, latency_s=5.0, cost_usd=0.01, quality_score=0.5,
              agent_name="a", bucket="code")
    w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS["code"]
    phi_sp = math.exp(-_SPEED_LAMBDA * 5.0 / _SPEED_T_REF)
    phi_c = 1.0 - math.tanh(0.01 / _COST_REF)
    legacy = round(w_s * 1.0 + w_q * 0.5 + w_sp * phi_sp + w_c * phi_c, 4)
    assert calc.compute(TaskOutcome(**kw)) == legacy
    assert calc.compute(TaskOutcome(**kw, correctness=None)) == legacy
    assert calc.compute(TaskOutcome(**kw, correctness=1.0)) == legacy


def test_correctness_zero_removes_exactly_w_success():
    """A judge reject removes exactly the success term, nothing else."""
    calc = RewardCalculator()
    kw = dict(success=True, latency_s=5.0, cost_usd=0.0, quality_score=0.5,
              agent_name="a", bucket="code")
    accepted = calc.compute(TaskOutcome(**kw, correctness=1.0))
    rejected = calc.compute(TaskOutcome(**kw, correctness=0.0))
    w_s = BUCKET_WEIGHTS["code"][0]
    assert abs((accepted - rejected) - w_s) < 1e-9


def test_failure_floor_beats_judge_accept():
    """success=False is the hard floor — a judge True never resurrects a crash."""
    calc = RewardCalculator()
    outcome = TaskOutcome(success=False, latency_s=1.0, cost_usd=0.0,
                          quality_score=0.9, agent_name="a", correctness=1.0)
    assert calc.compute(outcome) == 0.0


def test_correctness_clamped_to_01():
    """Out-of-range correctness clamps to [0, 1] rather than distorting reward."""
    calc = RewardCalculator()
    kw = dict(success=True, latency_s=2.0, cost_usd=0.0, quality_score=0.8,
              agent_name="a", bucket="code")
    assert calc.compute(TaskOutcome(**kw, correctness=2.0)) == \
        calc.compute(TaskOutcome(**kw, correctness=1.0))
    assert calc.compute(TaskOutcome(**kw, correctness=-1.0)) == \
        calc.compute(TaskOutcome(**kw, correctness=0.0))


def test_judge_cost_and_detail_do_not_affect_reward():
    calc = RewardCalculator()
    kw = dict(success=True, latency_s=1.0, cost_usd=0.01, quality_score=0.8,
              agent_name="a", correctness=1.0)
    base = calc.compute(TaskOutcome(**kw))
    with_meta = calc.compute(TaskOutcome(**kw, judge_cost=0.02, judge_detail="x"))
    assert base == with_meta
