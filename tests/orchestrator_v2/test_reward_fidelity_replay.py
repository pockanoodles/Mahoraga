"""Unit tests for the reward-fidelity replay (reward_fidelity_replay).

Covers the matrix builder over the tiny fixture cross (real sandbox grading of
trivial code), the variant-scoring guarantees — legacy MUST be bit-exact
against RewardCalculator with no correctness field — seeded synthetic-judge
sampling, bandit-sim determinism, and the criteria evaluation. No live DB, no
LLM inference.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from backend.orchestrator.routing.reward_fidelity_replay import (
    QUALITY_CONST,
    ArmOutcome,
    Environment,
    Variant,
    baselines,
    build_environment,
    default_variants,
    evaluate_criteria,
    score_outcome,
    simulate_variant,
)
from backend.orchestrator.routing.reward import RewardCalculator, TaskOutcome

FIXTURE_CASES = Path(__file__).parent / "fixtures" / "reward_replay_cases.jsonl"

A = "ollama:arm-a"
B = "ollama:arm-b"


def _write_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "bank.jsonl"
    bank.write_text(
        json.dumps({
            "prompt": "Write a function f that returns 42",
            "bucket": "code", "entrypoint": "f", "tests": "assert f() == 42",
        }) + "\n"
        + json.dumps({
            "prompt": "Write a function g that returns 7",
            "bucket": "code", "entrypoint": "g", "tests": "assert g() == 7",
        }) + "\n"
    )
    return bank


# ── Matrix builder ────────────────────────────────────────────────────────────


def test_build_environment_grades_backfills_and_drops(tmp_path: Path):
    env = build_environment(_write_bank(tmp_path), [FIXTURE_CASES])

    assert env.arms == [A, B]
    # g-prompt has no arm-b row -> dropped from the bandit environment
    assert env.prompts == ["Write a function f that returns 42"]
    assert env.n_dropped_incomplete == 1

    f_prompt = env.prompts[0]
    assert env.outcomes[(f_prompt, A)].passed is True
    assert env.outcomes[(f_prompt, B)].passed is False
    assert env.discriminating == [f_prompt]

    # the g row carries no elapsed_s -> backfilled with arm-a's mean (2.0)
    g_key = ("Write a function g that returns 7", A)
    assert env.n_latency_backfilled == 1
    assert env.outcomes[g_key].latency_recorded is False
    assert env.outcomes[g_key].latency_s == 2.0


def test_merge_prefers_row_with_recorded_timing(tmp_path: Path):
    bank = _write_bank(tmp_path)
    results = tmp_path / "results.jsonl"
    # duplicate (prompt, arm): the timing-carrying row must win wholesale,
    # even though it arrives second and its output differs (fails the tests).
    results.write_text(
        json.dumps({
            "prompt_full": "Write a function f that returns 42",
            "actual_agent": A, "success": True,
            "output_full": "```python\ndef f():\n    return 42\n```",
        }) + "\n"
        + json.dumps({
            "prompt_full": "Write a function f that returns 42",
            "actual_agent": A, "success": True, "elapsed_s": 3.5,
            "output_full": "```python\ndef f():\n    return 0\n```",
        }) + "\n"
    )
    env = build_environment(bank, [results])
    o = env.outcomes[("Write a function f that returns 42", A)]
    assert o.latency_recorded is True
    assert o.latency_s == 3.5
    assert o.passed is False  # graded from the same execution's output


# ── Variant scoring ───────────────────────────────────────────────────────────


def test_legacy_variant_reproduces_legacy_reward_exactly(tmp_path: Path):
    """Regression guard: correctness=None must be bit-exact against the
    RewardCalculator called the pre-fix way (no correctness field at all)."""
    env = build_environment(_write_bank(tmp_path), [FIXTURE_CASES])
    legacy = default_variants()[0]
    assert legacy.kind == "legacy"
    calc = RewardCalculator()
    rng = random.Random(0)
    for key, o in env.outcomes.items():
        replayed = score_outcome(calc, o, legacy.correctness(o.passed, rng))
        direct = calc.compute(TaskOutcome(
            success=o.gate_success,
            latency_s=o.latency_s,
            cost_usd=o.cost_usd,
            quality_score=QUALITY_CONST,
            agent_name=o.agent,
            bucket=o.bucket,
        ))
        assert replayed == direct, key


def test_oracle_and_judge_correctness_mapping():
    _, oracle, plain, code = default_variants()
    rng = random.Random(1)
    assert oracle.correctness(True, rng) == 1.0
    assert oracle.correctness(False, rng) == 0.0
    assert oracle.expected_correctness(True) == 1.0
    # judge expectation = accept probability at the measured operating point
    assert plain.expected_correctness(True) == 1.0 - 0.114
    assert plain.expected_correctness(False) == 1.0 - 0.688
    assert code.expected_correctness(True) == 1.0 - 0.144
    assert code.expected_correctness(False) == 1.0 - 0.781


def test_oracle_zero_correctness_still_gated_by_success():
    """A gate failure is 0.0 in every variant — the judge never resurrects it."""
    calc = RewardCalculator()
    crashed = ArmOutcome(agent=A, bucket="code", gate_success=False,
                         passed=False, latency_s=2.0, latency_recorded=True)
    for variant in default_variants():
        c = variant.expected_correctness(crashed.passed)
        assert score_outcome(calc, crashed, c) == 0.0


def test_synthetic_judge_sampling_seeded_and_calibrated():
    judge = Variant("judge-plain", "judge", recall=0.688, fpr=0.114)
    draws_a = [judge.correctness(True, random.Random(42)) for _ in range(1)]
    draws_b = [judge.correctness(True, random.Random(42)) for _ in range(1)]
    assert draws_a == draws_b  # same seed, same verdict

    rng = random.Random(7)
    seq1 = [judge.correctness(i % 2 == 0, rng) for i in range(50)]
    rng = random.Random(7)
    seq2 = [judge.correctness(i % 2 == 0, rng) for i in range(50)]
    assert seq1 == seq2  # deterministic sequence under a fixed seed

    rng = random.Random(3)
    accepts_pass = sum(judge.correctness(True, rng) for _ in range(5000)) / 5000
    accepts_fail = sum(judge.correctness(False, rng) for _ in range(5000)) / 5000
    assert abs(accepts_pass - (1.0 - 0.114)) < 0.03
    assert abs(accepts_fail - (1.0 - 0.688)) < 0.03


# ── Bandit simulation ─────────────────────────────────────────────────────────


def _synthetic_env(n_prompts: int = 12) -> Environment:
    """arm-a passes everything, arm-b nothing; identical latency, so the ONLY
    separating signal is the correctness coefficient — legacy sees two
    identical arms, oracle sees a 0/1 quality gap."""
    prompts = [f"task {i}: write a function please" for i in range(n_prompts)]
    outcomes = {}
    for p in prompts:
        outcomes[(p, A)] = ArmOutcome(agent=A, bucket="code", gate_success=True,
                                      passed=True, latency_s=3.0, latency_recorded=True)
        outcomes[(p, B)] = ArmOutcome(agent=B, bucket="code", gate_success=True,
                                      passed=False, latency_s=3.0, latency_recorded=True)
    return Environment(
        prompts=prompts, arms=[A, B], outcomes=outcomes,
        discriminating=list(prompts), n_latency_backfilled=0, n_dropped_incomplete=0,
    )


def test_simulate_variant_deterministic_under_fixed_seed():
    env = _synthetic_env()
    oracle = default_variants()[1]
    r1 = simulate_variant(env, oracle, n_orderings=5, seed=42)
    r2 = simulate_variant(env, oracle, n_orderings=5, seed=42)
    assert r1 == r2


def test_oracle_reward_separates_arms_where_legacy_cannot():
    env = _synthetic_env()
    legacy, oracle = default_variants()[:2]
    sim_legacy = simulate_variant(env, legacy, n_orderings=20, seed=42)
    sim_oracle = simulate_variant(env, oracle, n_orderings=20, seed=42)

    # legacy: identical rewards for both arms -> no gradient at all
    assert sim_legacy.reward_gap == 0.0
    assert 0.2 <= sim_legacy.disc_accuracy <= 0.8  # coin-flip territory

    # oracle: the correctness coefficient opens the success-term gap and the
    # bandit converges on the truly-passing arm
    assert sim_oracle.reward_gap > 0.3
    assert sim_oracle.reward_leader == A
    assert sim_oracle.pick_share[A] > 0.65
    assert sim_oracle.pass_at_1 > sim_legacy.pass_at_1 + 0.15
    assert sim_oracle.reward_pass_corr > 0.9


def test_baselines_derived_exactly():
    env = _synthetic_env(10)
    base = baselines(env)
    assert base["static"][A] == 1.0
    assert base["static"][B] == 0.0
    assert base["round_robin"] == 0.5
    assert base["best_static_arm"] == A
    assert base["oracle_router"] == 1.0


# ── Criteria evaluation ───────────────────────────────────────────────────────


def _sim(name: str, pass_at_1: float, disc: float) -> "object":
    env = _synthetic_env(2)
    base = simulate_variant(env, default_variants()[0], n_orderings=1, seed=0)
    return type(base)(
        variant=name, n_orderings=1, pass_at_1=pass_at_1, pass_at_1_std=0.0,
        pick_share={A: 0.5, B: 0.5}, disc_accuracy=disc, n_discriminating=10,
        reward_gap=0.0, reward_leader=A, arm_mean_reward={}, reward_pass_corr=None,
    )


def test_evaluate_criteria_pass_fail_and_na():
    base = {"round_robin": 0.75, "best_static": 0.78, "best_static_arm": A,
            "static": {A: 0.78, B: 0.72}, "oracle_router": 0.89}

    # healthy outcome: legacy stuck at RR/coin-flip, oracle wins, judge transmits
    sims = {
        "legacy": _sim("legacy", 0.74, 0.48),
        "oracle": _sim("oracle", 0.78, 0.70),
        "judge-plain": _sim("judge-plain", 0.77, 0.60),
    }
    verdicts = {c["criterion"]: c["verdict"] for c in evaluate_criteria(sims, base)}
    assert verdicts["legacy reproduces Era 20 (disc-acc ~ coin flip and/or pass@1 <= round-robin)"] == "PASS"
    assert verdicts["oracle beats round-robin and approaches best-static or better"] == "PASS"
    assert verdicts["judge-plain retains a majority of the oracle improvement"] == "PASS"

    # judge keeps < half the oracle gain -> FAIL
    sims["judge-plain"] = _sim("judge-plain", 0.75, 0.55)
    verdicts = {c["criterion"]: c["verdict"] for c in evaluate_criteria(sims, base)}
    assert verdicts["judge-plain retains a majority of the oracle improvement"] == "FAIL"

    # degenerate oracle (no gain) -> transmission is N/A, not a fake verdict
    sims["oracle"] = _sim("oracle", 0.74, 0.50)
    verdicts = {c["criterion"]: c["verdict"] for c in evaluate_criteria(sims, base)}
    assert verdicts["judge-plain retains a majority of the oracle improvement"] == "N/A"

    # legacy that beats RR with above-band disc accuracy fails reproduction
    sims["legacy"] = _sim("legacy", 0.80, 0.75)
    verdicts = {c["criterion"]: c["verdict"] for c in evaluate_criteria(sims, base)}
    assert verdicts["legacy reproduces Era 20 (disc-acc ~ coin flip and/or pass@1 <= round-robin)"] == "FAIL"
