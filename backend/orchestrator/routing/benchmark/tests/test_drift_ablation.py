"""Tests for the synthetic-drift ablation (gamma-spec.md §Ablation Plan).

Covers: the extracted Oracle.expected_reward helper, the _DriftOracle
wrapper (degrades exactly one agent inside a task-index window, and its
optimal_* reflect the degraded world), the sim-side adaptive-gamma
strategy, and the _exp6 entry point's metric contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestrator.routing.benchmark.oracle import AGENTS, Oracle
from backend.orchestrator.routing.benchmark.ablation_study import (
    _AdaptiveLinUCB,
    _DriftOracle,
    _exp6_adaptive_gamma,
    _run_drift,
)


def _fresh(seed: int = 7, n: int = 40) -> Oracle:
    o = Oracle(seed=seed, n_tasks=n)
    o.generate_tasks()
    return o


# ── Oracle.expected_reward extraction ────────────────────────────────────────


class TestExpectedReward:
    def test_optimal_reward_is_max_expected_reward(self) -> None:
        o = _fresh()
        for task in o.generate_tasks()[:10]:
            assert o.optimal_reward(task) == pytest.approx(
                max(o.expected_reward(task, a) for a in AGENTS)
            )

    def test_optimal_agent_is_argmax_expected_reward(self) -> None:
        o = _fresh()
        for task in o.generate_tasks()[:10]:
            best = max(AGENTS, key=lambda a: o.expected_reward(task, a))
            assert o.optimal_agent(task) == best


# ── _DriftOracle ─────────────────────────────────────────────────────────────


class TestDriftOracle:
    def test_degrades_target_only_inside_window(self) -> None:
        # Same seed → identical RNG streams, so the wrapped rewards must
        # equal base rewards except inside the window on the target arm.
        base, wrapped_base = _fresh(seed=7), _fresh(seed=7)
        drift = _DriftOracle(
            wrapped_base, target="claude", delta=0.15, start=2, end=4
        )
        task = base.generate_tasks()[0]
        for i in range(6):
            r_base = base.evaluate(task, "claude")["reward"]
            r_drift = drift.evaluate(task, "claude")["reward"]
            if 2 <= i < 4:
                assert r_drift == pytest.approx(max(0.0, r_base - 0.15))
            else:
                assert r_drift == pytest.approx(r_base)

    def test_non_target_agent_never_degraded(self) -> None:
        base, wrapped_base = _fresh(seed=7), _fresh(seed=7)
        drift = _DriftOracle(
            wrapped_base, target="claude", delta=0.15, start=0, end=100
        )
        task = base.generate_tasks()[0]
        for _ in range(5):
            r_base = base.evaluate(task, "ollama")["reward"]
            r_drift = drift.evaluate(task, "ollama")["reward"]
            assert r_drift == pytest.approx(r_base)

    def test_optimal_reflects_degraded_world(self) -> None:
        o = _fresh(seed=7)
        tasks = o.generate_tasks()
        # Degrade the arm that is optimal for the first task — the dynamic
        # oracle must switch arms or accept the reduced reward.
        task = tasks[0]
        target = o.optimal_agent(task)
        drift = _DriftOracle(o, target=target, delta=0.15, start=0, end=10)
        assert drift.optimal_reward(task) == pytest.approx(
            max(drift.expected_reward(task, a) for a in AGENTS)
        )
        assert drift.optimal_reward(task) <= o.optimal_reward(task) + 1e-9


# ── Sim-side adaptive gamma ──────────────────────────────────────────────────


class TestAdaptiveLinUCBSim:
    def test_gamma_bounded_and_pulls_tracked(self) -> None:
        o = _fresh(seed=11, n=60)
        tasks = o.generate_tasks()
        s = _AdaptiveLinUCB(alpha=1.0)
        drift = _DriftOracle(o, target="claude", delta=0.15, start=20, end=45)
        _run_drift(drift, s, tasks)
        assert sum(s.pulls.values()) == len(tasks)
        for g in s.gamma_per_arm.values():
            assert s.gamma_min - 1e-9 <= g <= max(s.gamma_max, s.gamma) + 1e-9


# ── Experiment entry point ───────────────────────────────────────────────────


class TestExp6:
    def test_returns_metric_contract_for_all_variants(
        self, tmp_path: Path
    ) -> None:
        o = Oracle(seed=42, n_tasks=60)
        tasks = o.generate_tasks()
        results = _exp6_adaptive_gamma(o, tasks, tmp_path, dpi=60, seed=42)
        assert len(results) == 3
        for name, v in results.items():
            assert "final_regret" in v
            assert "detection_latency" in v
            assert "regret_after_changepoint" in v
            assert "recovery_time" in v
            assert v["final_regret"] == pytest.approx(v["curve"][-1])
            # Pick-share of the degraded arm before/during/after the window
            # — the robust view of drift response (first-index metrics are
            # meaningless when the arm wasn't consistently picked pre-drift).
            for k in ("target_share_pre", "target_share_during", "target_share_post"):
                assert 0.0 <= v[k] <= 1.0
