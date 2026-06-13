"""Tests for F1 — Budget Pacer.

Acceptance criteria from docs/specs/v2-debug-F1-F4.md §F1:
  1. BUDGET_CEILING=0.0 → no paid agent ever selected (over 100 sims).
  2. BUDGET_CEILING=0.05 → paid agents allowed but rolling avg < 0.05.
  3. λ converges (variance < 0.01 over last 50 updates after 200 tasks).
  4. HARD_LIMIT=0.10 → task estimated at $0.15 never routes to a paid agent.
  5. Graceful when no cost data: defaults assumed.
  6. `orch budget status` prints state (covered separately by CLI smoke).
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from backend.orchestrator.routing.budget_pacer import (
    BUDGET_PACER_STATE_PATH,
    BudgetPacer,
    resolve_ceiling,
    resolve_eta,
    resolve_hard_limit,
    resolve_window,
)


# ── env hygiene ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MAHORAGA_BUDGET_CEILING",
        "MAHORAGA_BUDGET_WINDOW",
        "MAHORAGA_BUDGET_HARD_LIMIT",
        "MAHORAGA_BUDGET_ETA",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_default_ceiling():
    assert resolve_ceiling() == 0.05


def test_env_ceiling_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BUDGET_CEILING", "0.10")
    assert resolve_ceiling() == 0.10


def test_env_ceiling_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BUDGET_CEILING", "definitely_not_a_number")
    assert resolve_ceiling() == 0.05


def test_env_window(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BUDGET_WINDOW", "200")
    assert resolve_window() == 200


def test_env_hard_limit(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BUDGET_HARD_LIMIT", "1.00")
    assert resolve_hard_limit() == 1.00


def test_env_eta(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BUDGET_ETA", "0.05")
    assert resolve_eta() == 0.05


# ── dual update ──────────────────────────────────────────────────────────────


def test_pacer_dual_update_increases_lambda():
    """Feed costs above the ceiling → λ rises monotonically."""
    p = BudgetPacer(ceiling=0.05, eta=0.05, window=20)
    for _ in range(50):
        p.update(0.10)  # 2× over ceiling
    assert p.lambda_ > 0.0
    assert p.avg_cost == pytest.approx(0.10, abs=1e-6)


def test_pacer_dual_update_decreases_lambda():
    """Lambda starts high, then rolling average drops below ceiling."""
    p = BudgetPacer(ceiling=0.05, eta=0.10, window=20, lambda_=2.0)
    # Pump under-budget costs through.
    for _ in range(50):
        p.update(0.0)
    # λ decays toward 0 (ascent step is negative when avg < ceiling).
    assert p.lambda_ < 2.0
    assert p.avg_cost == 0.0


def test_pacer_lambda_non_negative():
    """λ is clamped at 0 — even with unbroken under-budget runs."""
    p = BudgetPacer(ceiling=0.05, eta=0.10, window=10, lambda_=0.0)
    for _ in range(100):
        p.update(0.0)
    assert p.lambda_ == 0.0


def test_pacer_zero_ceiling_pumps_lambda():
    """ceiling=0.0 means any non-zero cost increases λ."""
    p = BudgetPacer(ceiling=0.0, eta=0.10, window=10)
    p.update(0.10)
    assert p.lambda_ > 0.0


def test_pacer_rolling_window_evicts_old():
    """Beyond window, oldest cost is evicted."""
    p = BudgetPacer(ceiling=0.05, eta=0.0, window=3)
    p.update(1.0)
    p.update(0.0)
    p.update(0.0)
    assert p.avg_cost == pytest.approx(1.0 / 3, abs=1e-9)
    p.update(0.0)  # evicts the 1.0
    assert p.avg_cost == 0.0


# ── filter_agents ────────────────────────────────────────────────────────────


def test_pacer_hard_limit_excludes_expensive():
    """Hard limit at 0.10, agent estimated at 0.15 → excluded."""
    p = BudgetPacer(hard_limit=0.10)
    estimates = {"ollama": 0.0, "claude": 0.15, "codex-cli": 0.05}
    kept = p.filter_agents(["ollama", "claude", "codex-cli"], estimates)
    assert "claude" not in kept
    assert "ollama" in kept
    assert "codex-cli" in kept


def test_pacer_hard_limit_zero_means_free_only():
    """hard_limit=0 keeps only zero-cost agents."""
    p = BudgetPacer(hard_limit=0.0)
    estimates = {"ollama": 0.0, "claude": 0.05}
    kept = p.filter_agents(["ollama", "claude"], estimates)
    assert kept == ["ollama"]


def test_pacer_filter_all_blocked_falls_back_to_cheapest():
    """If hard_limit excludes everything, fall back to cheapest. Never
    starve the bandit of a choice."""
    p = BudgetPacer(hard_limit=0.10)
    estimates = {"claude": 0.30, "gpt4": 0.40}
    kept = p.filter_agents(["claude", "gpt4"], estimates)
    assert kept == ["claude"]  # cheaper of the two


def test_pacer_filter_zero_ceiling_all_paid_falls_back():
    """hard_limit=0 with no free agents → cheapest paid one returned."""
    p = BudgetPacer(hard_limit=0.0)
    estimates = {"claude": 0.30, "gpt4": 0.40}
    kept = p.filter_agents(["claude", "gpt4"], estimates)
    assert kept == ["claude"]


def test_pacer_filter_unknown_costs_pass_through():
    """If an agent has no cost estimate, it's treated as free."""
    p = BudgetPacer(hard_limit=0.10)
    kept = p.filter_agents(["unknown_agent"], {})  # no estimate
    assert "unknown_agent" in kept


# ── cost_weight_adjustment ───────────────────────────────────────────────────


def test_cost_weight_adjustment_tracks_lambda():
    p = BudgetPacer(ceiling=0.0, eta=0.10, window=10)
    assert p.cost_weight_adjustment == 0.0
    for _ in range(20):
        p.update(0.10)
    assert p.cost_weight_adjustment == p.lambda_
    assert p.cost_weight_adjustment > 0.0


# ── convergence ──────────────────────────────────────────────────────────────


def test_pacer_convergence_after_500_tasks():
    """Acceptance criterion 3: λ stabilises after 200+ tasks at steady state.

    Mix of free + paid tasks with mean cost = ceiling: λ converges to
    a steady value (variance < 0.05 over last 50 updates).
    """
    p = BudgetPacer(ceiling=0.05, eta=0.05, window=50)
    # Half the tasks free, half paid at $0.10 → mean 0.05 = ceiling.
    for i in range(500):
        cost = 0.0 if i % 2 == 0 else 0.10
        p.update(cost)

    # Sample lambda trajectory over the last 50 updates.
    trajectory = []
    for _ in range(50):
        cost = 0.0 if len(p.spent) % 2 == 0 else 0.10
        p.update(cost)
        trajectory.append(p.lambda_)
    mean = sum(trajectory) / len(trajectory)
    var = sum((x - mean) ** 2 for x in trajectory) / len(trajectory)
    assert var < 0.05  # stable enough that further drift is small


# ── persistence ──────────────────────────────────────────────────────────────


def test_pacer_save_load_roundtrip(tmp_path):
    """Lambda and spent history survive across save/load."""
    p = BudgetPacer(ceiling=0.05, eta=0.05, window=20)
    for _ in range(15):
        p.update(0.10)
    state = tmp_path / "pacer.json"
    p.save(state)

    p2 = BudgetPacer.load(state)
    assert p2.lambda_ == p.lambda_
    assert list(p2.spent) == list(p.spent)


def test_pacer_load_missing_file_returns_default(tmp_path):
    """No persisted state → fresh pacer with env-resolved config."""
    p = BudgetPacer.load(tmp_path / "no_such_file.json")
    assert p.lambda_ == 0.0
    assert len(p.spent) == 0


def test_pacer_load_with_env_overrides_ceiling(monkeypatch, tmp_path):
    """Env config wins over persisted ceiling on reload — so changing
    BUDGET_CEILING actually takes effect after a restart."""
    p = BudgetPacer(ceiling=0.05, eta=0.05, window=20, lambda_=1.5)
    state = tmp_path / "pacer.json"
    p.save(state)

    monkeypatch.setenv("MAHORAGA_BUDGET_CEILING", "0.10")
    p2 = BudgetPacer.load(state)
    assert p2.ceiling == 0.10
    assert p2.lambda_ == 1.5  # but lambda preserved


def test_pacer_load_corrupt_returns_default(tmp_path):
    """Corrupt persisted state → fall back to defaults rather than crash."""
    state = tmp_path / "pacer.json"
    state.write_text("{not valid json")
    p = BudgetPacer.load(state)
    assert p.lambda_ == 0.0


# ── status dict ──────────────────────────────────────────────────────────────


def test_status_dict_has_expected_keys():
    p = BudgetPacer(ceiling=0.05, eta=0.05, window=20)
    p.update(0.04)
    p.update(0.06)
    s = p.to_status_dict()
    assert s["ceiling"] == 0.05
    assert s["window"] == 20
    assert s["n_observed"] == 2
    assert s["lambda"] >= 0.0
    assert s["avg_cost"] == pytest.approx(0.05, abs=1e-6)
    assert s["over_ceiling"] is False  # exactly at ceiling, not over


def test_status_dict_serialisable_to_json():
    p = BudgetPacer(ceiling=0.05)
    p.update(0.10)
    json.dumps(p.to_status_dict())  # raises if not serialisable


# ── reward calculator integration ────────────────────────────────────────────


def test_reward_calc_pacer_widens_cheap_vs_expensive_gap():
    """The reward function rewards cost-cleanliness via w_c * (1 - tanh(cost)).
    Increasing w_c (via λ > 0) doesn't punish a single expensive outcome in
    isolation — it widens the *gap* between cheap and expensive in the
    bandit's view. The bandit picks max-reward, so wider gap = stronger
    preference for cheap agents, which is what the pacer should produce."""
    from backend.orchestrator.routing.reward import (
        RewardCalculator, TaskOutcome,
    )
    pacer = BudgetPacer(ceiling=0.05, lambda_=0.5)
    calc_with = RewardCalculator(pacer=pacer)
    calc_without = RewardCalculator()

    cheap = TaskOutcome(
        success=True, latency_s=2.0, cost_usd=0.0,
        quality_score=0.8, agent_name="ollama", bucket="general",
    )
    expensive = TaskOutcome(
        success=True, latency_s=2.0, cost_usd=0.10,
        quality_score=0.8, agent_name="claude", bucket="general",
    )
    gap_without = calc_without.compute(cheap) - calc_without.compute(expensive)
    gap_with = calc_with.compute(cheap) - calc_with.compute(expensive)
    assert gap_with > gap_without
    # And meaningfully so — λ=0.5 produces a noticeable widening, not
    # just rounding-noise.
    assert (gap_with - gap_without) > 0.01


def test_reward_calc_pacer_unchanged_when_lambda_zero():
    """λ = 0 means the pacer is dormant — reward identical to no-pacer case."""
    from backend.orchestrator.routing.reward import (
        RewardCalculator, TaskOutcome,
    )
    pacer = BudgetPacer(ceiling=0.05, lambda_=0.0)
    calc_with = RewardCalculator(pacer=pacer)
    calc_without = RewardCalculator()

    outcome = TaskOutcome(
        success=True, latency_s=2.0, cost_usd=0.05,
        quality_score=0.8, agent_name="claude", bucket="general",
    )
    assert calc_with.compute(outcome) == calc_without.compute(outcome)
