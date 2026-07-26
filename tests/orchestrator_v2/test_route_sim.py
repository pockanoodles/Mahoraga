"""Unit tests for routing-policy simulation (route_sim).

Covers the pure simulation logic on a synthetic {prompt x arm} matrix — no DB,
no subprocess — plus one grade_matrix happy-path over temp files. The interplay
that matters: the routed cascade's cost/quality under both the default oracle
gate and an injected fallible gate (the verification-tax model 5b builds on).
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.orchestrator.routing.route_sim import (
    grade_matrix,
    infer_arms,
    simulate,
    PolicyResult,
)

CLOUD = "claude-cli"
G = "ollama:granite4.1-8b"
Q = "ollama:qwen3.5"


def _matrix():
    # 4 prompts. granite: p1,p2,p3 pass / p4 fail. qwen: p1,p4 pass / p2,p3 fail.
    # cloud: all pass. p4 is granite's only miss (qwen saves it).
    return {
        "p1": {G: True, Q: True, CLOUD: True},
        "p2": {G: True, Q: False, CLOUD: True},
        "p3": {G: True, Q: False, CLOUD: True},
        "p4": {G: False, Q: True, CLOUD: True},
    }, ["p1", "p2", "p3", "p4"]


def _costs():
    return {"p1": 0.01, "p2": 0.02, "p3": 0.03, "p4": 0.10}


def _get(policies: list[PolicyResult], prefix: str) -> PolicyResult:
    return next(p for p in policies if p.name.startswith(prefix))


def test_baselines_measured_directly():
    matrix, prompts = _matrix()
    pol = simulate(matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G])
    cloud = _get(pol, "always-cloud")
    assert (cloud.passed, cloud.n) == (4, 4)
    assert cloud.cost_per_task == (0.01 + 0.02 + 0.03 + 0.10) / 4
    gran = _get(pol, "always-local:granite")
    assert (gran.passed, gran.n) == (3, 4) and gran.cost_per_task == 0.0
    bol = _get(pol, "best-of-local")
    assert bol.passed == 4  # every prompt solved by some local arm


def test_single_arm_cascade_oracle_escalates_only_true_misses():
    matrix, prompts = _matrix()
    pol = simulate(matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G])
    routed = _get(pol, "routed:")
    # granite misses only p4 -> 1 escalation, cloud recovers it -> 4/4
    assert routed.escalations == 1
    assert (routed.passed, routed.n) == (4, 4)
    assert routed.cost_per_task == 0.10 / 4  # only p4's cloud cost, spread over 4


def test_two_stage_cascade_recovers_miss_for_free():
    matrix, prompts = _matrix()
    pol = simulate(matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G, Q])
    routed = _get(pol, "routed:")
    # qwen saves p4 locally -> zero escalations, zero cost, still 4/4
    assert routed.escalations == 0
    assert (routed.passed, routed.n) == (4, 4)
    assert routed.cost_per_task == 0.0


def test_denominator_is_attempted_not_total():
    # qwen didn't attempt p3 (infra drop): its rate is over 3 attempts, not 4.
    matrix, prompts = _matrix()
    del matrix["p3"][Q]
    pol = simulate(matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G])
    qwen = _get(pol, "always-local:qwen3.5")
    assert qwen.n == 3  # p1,p2,p4 attempted


def test_fallible_gate_wrong_accept_costs_quality():
    # Gate wrongly accepts p4 (granite failed it) -> no escalation, quality lost.
    matrix, prompts = _matrix()
    pol = simulate(
        matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G],
        local_solved=lambda p: True,  # accept everything, never escalate
    )
    routed = _get(pol, "routed:")
    assert routed.escalations == 0
    assert routed.cost_per_task == 0.0
    assert routed.passed == 3  # p4 kept as a (wrong) local answer -> quality tax


def test_fallible_gate_wrong_escalate_costs_money():
    # Gate wrongly escalates everything -> full quality but full cloud cost.
    matrix, prompts = _matrix()
    pol = simulate(
        matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD, cascade=[G],
        local_solved=lambda p: False,  # escalate everything
    )
    routed = _get(pol, "routed:")
    assert routed.escalations == 4
    assert routed.passed == 4
    assert routed.cost_per_task == (0.01 + 0.02 + 0.03 + 0.10) / 4  # == always-cloud


def test_gate_cost_charged_on_every_task():
    # A judge gate costs a call per task, even ones it accepts. With a perfect
    # gate (oracle) escalating only p4, cost = gate_cost*4 + p4 cloud cost.
    matrix, prompts = _matrix()
    gate_cost = 0.005
    pol = simulate(matrix, prompts, _costs(), local_arms=[G, Q], cloud_arm=CLOUD,
                   cascade=[G], gate_cost_per_task=gate_cost)
    routed = _get(pol, "routed:")
    expected = (gate_cost * 4 + 0.10) / 4  # 4 tasks judged, p4 escalated
    assert abs(routed.cost_per_task - expected) < 1e-9
    # baselines are unaffected by the gate cost
    assert _get(pol, "always-cloud").cost_per_task == (0.01 + 0.02 + 0.03 + 0.10) / 4


def test_infer_arms_picks_best_local():
    matrix, _ = _matrix()
    local_arms, best = infer_arms(matrix, CLOUD)
    assert set(local_arms) == {G, Q}
    assert best == G  # 3/4 beats qwen's 2/4


def test_grade_matrix_happy_path(tmp_path: Path):
    bank = tmp_path / "bank.jsonl"
    bank.write_text(
        json.dumps({
            "prompt": "Return 42",
            "bucket": "code",
            "entrypoint": "f",
            "tests": "assert f() == 42",
        }) + "\n"
    )
    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps({
            "prompt_full": "Return 42",
            "actual_agent": G,
            "output_full": "```python\ndef f():\n    return 42\n```",
        }) + "\n"
        + json.dumps({
            "prompt_full": "Return 42",
            "actual_agent": Q,
            "output_full": "```python\ndef f():\n    return 0\n```",
        }) + "\n"
    )
    matrix, prompts = grade_matrix(bank, results)
    assert prompts == ["Return 42"]
    assert matrix["Return 42"][G] is True
    assert matrix["Return 42"][Q] is False
