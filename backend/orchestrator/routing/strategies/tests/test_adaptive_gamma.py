"""Unit tests for adaptive per-arm gamma (docs/specs/gamma-spec.md).

Covers the corrected semantics after adversarial review:
- warmup is per-(bucket, arm): cold-cell errors never reach the shared EMA
- the mapping is centered on the noise floor: normalized error E≤1 (a
  converged arm's equilibrium by construction) maps to gamma_max
- variance floor + outlier cap keep eps_norm bounded (constant-reward
  collapse, single-outlier whipsaw)
- recovery for idle arms decays the error EMA itself, so the gamma that
  is actually applied recovers (a nudged side-table would be inert)
- off-policy weight w scales all gamma bookkeeping
- persistence round-trip, pre-gamma and malformed state files
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.linucb_per_bucket import (
    LinUCBPerBucketRouter,
)
from backend.orchestrator.routing.strategies.static import classify_bucket


# ── Helpers ──────────────────────────────────────────────────────────────────


class _Task:
    def __init__(self, goal: str) -> None:
        self.goal = goal


def _ctx(goal: str) -> TaskContext:
    return TaskContext.from_task(_Task(goal))


_CODEGEN_GOAL = "write a function that computes Fibonacci numbers"
_DEBUG_GOAL = "fix the NullPointerException in auth.py line 42"


@pytest.fixture(autouse=True)
def _verify_test_buckets():
    assert classify_bucket(_ctx(_CODEGEN_GOAL)) == "code"
    assert classify_bucket(_ctx(_DEBUG_GOAL)) == "debug"


def _train(
    strategy: LinUCBPerBucketRouter,
    agent: str,
    reward: float,
    n: int,
    goal: str = _CODEGEN_GOAL,
) -> None:
    for _ in range(n):
        strategy.update(_ctx(goal), agent, reward)


def _expected_gamma(s: LinUCBPerBucketRouter, ema: float) -> float:
    excess = max(0.0, ema - 1.0)
    return s.gamma_min + (s.gamma_max - s.gamma_min) * math.exp(
        -excess / s.gamma_tau
    )


# ── Warmup guard (spec §Where-It-Breaks 1, per-bucket after review) ──────────


class TestWarmup:
    def test_gamma_stays_at_default_during_warmup(self) -> None:
        s = LinUCBPerBucketRouter(gamma_warmup=10)
        # Wildly inconsistent rewards — high prediction error — but the
        # cold (bucket, arm) cell must keep the global decay and must not
        # feed the error EMA.
        for i in range(9):
            s.update(_ctx(_CODEGEN_GOAL), "ollama", float(i % 2))
            assert s.gamma_per_arm["ollama"] == pytest.approx(s.decay)
        assert s.pred_error_ema == {}

    def test_gamma_adapts_after_warmup(self) -> None:
        s = LinUCBPerBucketRouter(gamma_warmup=10)
        _train(s, "ollama", 0.8, 10)
        assert "ollama" in s.pred_error_ema
        assert s.gamma_per_arm["ollama"] == pytest.approx(
            _expected_gamma(s, s.pred_error_ema["ollama"])
        )

    def test_pull_counts_tracked_per_arm_across_buckets(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 3, goal=_CODEGEN_GOAL)
        _train(s, "ollama", 0.8, 2, goal=_DEBUG_GOAL)
        _train(s, "aider", 0.8, 4, goal=_CODEGEN_GOAL)
        assert s.arm_pulls["ollama"] == 5
        assert s.arm_pulls["aider"] == 4

    def test_cold_bucket_pull_does_not_pollute_shared_ema(self) -> None:
        """Review finding: a converged arm's first pull in a NEW bucket
        scores a cold theta — that error must not slam the arm's gamma."""
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 20)  # converged in 'code'
        ema_before = s.pred_error_ema["ollama"]
        gamma_before = s.gamma_per_arm["ollama"]

        s.update(_ctx(_DEBUG_GOAL), "ollama", 0.8)  # first pull in 'debug'

        assert s.pred_error_ema["ollama"] == pytest.approx(ema_before)
        assert s.gamma_per_arm["ollama"] == pytest.approx(gamma_before)

    def test_cold_bucket_update_applies_global_decay(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 20)  # past warmup in 'code'
        # Prime the debug bucket so we can capture its matrices.
        s.update(_ctx(_DEBUG_GOAL), "ollama", 0.8)
        A_before = s.A["debug"]["ollama"].copy()
        ctx = _ctx(_DEBUG_GOAL)
        x = ctx.to_vector().reshape(-1, 1)
        s.update(ctx, "ollama", 0.8)  # still cold in 'debug' (2 < warmup)
        np.testing.assert_allclose(
            s.A["debug"]["ollama"], s.decay * A_before + x @ x.T
        )


# ── Adaptation dynamics ──────────────────────────────────────────────────────


class TestAdaptation:
    def test_stable_constant_arm_reaches_gamma_max_long_run(self) -> None:
        """Review finding: variance collapse drove stable arms to gamma_min
        around pull ~150. With the variance floor they must sit at gamma_max."""
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 200)
        assert s.gamma_per_arm["ollama"] == pytest.approx(s.gamma_max, abs=1e-3)

    def test_stable_noisy_arm_keeps_confidence(self) -> None:
        """Review finding: E equilibrates at ~1 for converged noisy arms —
        that must map to gamma_max (noise floor), not below the default."""
        s = LinUCBPerBucketRouter()
        rng = np.random.default_rng(0)
        for _ in range(150):
            s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.8 + rng.normal(0, 0.05))
        assert s.gamma_per_arm["ollama"] >= s.decay

    def test_gamma_drops_below_default_on_drift(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 40)
        _train(s, "ollama", 0.2, 3)  # sudden degradation
        assert s.gamma_per_arm["ollama"] < s.decay
        assert s.gamma_per_arm["ollama"] >= s.gamma_min

    def test_matrix_update_uses_per_arm_gamma(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 40)
        _train(s, "ollama", 0.2, 2)  # push gamma off the default
        bucket = classify_bucket(_ctx(_CODEGEN_GOAL))
        A_before = s.A[bucket]["ollama"].copy()
        b_before = s.b[bucket]["ollama"].copy()

        ctx = _ctx(_CODEGEN_GOAL)
        x = ctx.to_vector().reshape(-1, 1)
        s.update(ctx, "ollama", 0.2)

        g = s.gamma_per_arm["ollama"]
        assert g != pytest.approx(s.decay)
        np.testing.assert_allclose(
            s.A[bucket]["ollama"], g * A_before + x @ x.T
        )
        np.testing.assert_allclose(
            s.b[bucket]["ollama"], g * b_before + 0.2 * x
        )

    def test_single_outlier_cannot_slam_gamma_to_min(self) -> None:
        """eps cap: one unlucky task must not read as full drift."""
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 60)
        s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.0)  # one outlier
        assert s.gamma_per_arm["ollama"] > s.gamma_min + 0.01


# ── Recovery (spec §Where-It-Breaks 3, via EMA decay after review) ───────────


class TestRecovery:
    def test_idle_arm_error_ema_decays_and_gamma_recovers(self) -> None:
        s = LinUCBPerBucketRouter(gamma_recovery_rate=0.01)
        _train(s, "ollama", 0.8, 15)
        # Simulate a drifted idle arm.
        s.pred_error_ema["aider"] = 4.0
        s.gamma_per_arm["aider"] = 0.94
        s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.8)
        assert s.pred_error_ema["aider"] == pytest.approx(4.0 * 0.99)
        # The recovered gamma must be derived from the decayed EMA — the
        # value that will actually be APPLIED on the arm's next pull.
        assert s.gamma_per_arm["aider"] == pytest.approx(
            _expected_gamma(s, 4.0 * 0.99)
        )

    def test_pulled_arm_gets_adapted_value_not_recovery(self) -> None:
        s = LinUCBPerBucketRouter(gamma_recovery_rate=0.5)
        _train(s, "ollama", 0.8, 60)
        s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.8)
        assert s.gamma_per_arm["ollama"] == pytest.approx(
            _expected_gamma(s, s.pred_error_ema["ollama"])
        )


# ── Off-policy weight (review finding) ───────────────────────────────────────


class TestImportanceWeight:
    def test_near_zero_weight_barely_moves_gamma_state(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 60)
        ema_before = s.pred_error_ema["ollama"]
        pulls_before = s.arm_pulls["ollama"]
        # Quarantine-probe style observation: huge error, tiny weight.
        # The EMA may move by at most lam*cap = (1-beta)*w*eps_cap.
        s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.0, weight=1e-3)
        max_move = (1.0 - s.gamma_beta) * 1e-3 * 25.0
        assert abs(s.pred_error_ema["ollama"] - ema_before) <= max_move + 1e-12
        assert s.arm_pulls["ollama"] == pytest.approx(pulls_before + 1e-3)
        # Bounded EMA movement means gamma stays put at the stable value.
        assert s.gamma_per_arm["ollama"] == pytest.approx(s.gamma_max, abs=1e-3)

    def test_full_weight_moves_ema(self) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 60)
        ema_before = s.pred_error_ema["ollama"]
        s.update(_ctx(_CODEGEN_GOAL), "ollama", 0.0, weight=1.0)
        assert s.pred_error_ema["ollama"] > ema_before


# ── Baseline modes ───────────────────────────────────────────────────────────


class TestDisabledModes:
    def test_adaptive_gamma_off_uses_global_decay(self) -> None:
        s = LinUCBPerBucketRouter(adaptive_gamma=False)
        bucket = classify_bucket(_ctx(_CODEGEN_GOAL))
        _train(s, "ollama", 0.8, 20)
        A_before = s.A[bucket]["ollama"].copy()
        ctx = _ctx(_CODEGEN_GOAL)
        x = ctx.to_vector().reshape(-1, 1)
        s.update(ctx, "ollama", 0.8)
        np.testing.assert_allclose(
            s.A[bucket]["ollama"], s.decay * A_before + x @ x.T
        )
        assert s.gamma_per_arm == {}
        assert s.pred_error_ema == {}

    def test_undiscounted_mode_skips_adaptation(self) -> None:
        s = LinUCBPerBucketRouter(decay=1.0)
        _train(s, "ollama", 0.8, 20)
        assert s.gamma_per_arm == {}


# ── Warm-start isolation (spec Open Question 3) ──────────────────────────────


class TestWarmStartIsolation:
    def test_pseudo_obs_do_not_touch_gamma_state(self) -> None:
        s = LinUCBPerBucketRouter()
        x = np.ones(9)
        s.inject_pseudo_obs("ollama", x, 0.8, bucket="code")
        assert s.arm_pulls == {}
        assert s.pred_error_ema == {}
        assert s.gamma_per_arm == {}


# ── Persistence (spec §Where-It-Breaks 5) ────────────────────────────────────


class TestPersistence:
    def test_gamma_state_survives_save_load_roundtrip(
        self, tmp_path: Path
    ) -> None:
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 25)
        _train(s, "aider", 0.3, 12)
        _train(s, "ollama", 0.7, 5, goal=_DEBUG_GOAL)
        path = str(tmp_path / "state.json")
        s.save_state(path)

        fresh = LinUCBPerBucketRouter()
        fresh.load_state(path)
        assert fresh.gamma_per_arm == pytest.approx(s.gamma_per_arm)
        assert fresh.pred_error_ema == pytest.approx(s.pred_error_ema)
        assert fresh.arm_pulls == pytest.approx(s.arm_pulls)
        # Per-bucket pull counters must survive too, or every bucket
        # re-enters warmup after a daemon restart.
        assert set(fresh._bucket_pulls) == set(s._bucket_pulls)
        for bucket in s._bucket_pulls:
            assert fresh._bucket_pulls[bucket] == pytest.approx(
                s._bucket_pulls[bucket]
            )

    def test_load_pre_gamma_state_defaults_cleanly(
        self, tmp_path: Path
    ) -> None:
        """A v3 state file written before this feature has no gamma key."""
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 25)
        path = str(tmp_path / "state.json")
        s.save_state(path)

        raw = json.loads(Path(path).read_text())
        raw.pop("arm_gamma_state", None)
        for bdata in raw["buckets"].values():
            for mdata in bdata["agents"].values():
                mdata.pop("pulls", None)
        Path(path).write_text(json.dumps(raw))

        fresh = LinUCBPerBucketRouter()
        fresh.load_state(path)  # must not raise
        assert fresh.gamma_per_arm == {}
        assert fresh.pred_error_ema == {}
        assert fresh.arm_pulls == {}

    @pytest.mark.parametrize(
        "malformed", [[], "junk", {"ollama": 42}, {"ollama": None}]
    )
    def test_load_malformed_gamma_state_does_not_raise(
        self, tmp_path: Path, malformed
    ) -> None:
        """Review finding: junk in arm_gamma_state raised AttributeError,
        escaping BanditRouter's corrupted-state fallback."""
        s = LinUCBPerBucketRouter()
        _train(s, "ollama", 0.8, 25)
        path = str(tmp_path / "state.json")
        s.save_state(path)

        raw = json.loads(Path(path).read_text())
        raw["arm_gamma_state"] = malformed
        Path(path).write_text(json.dumps(raw))

        fresh = LinUCBPerBucketRouter()
        fresh.load_state(path)  # must not raise
        assert fresh.gamma_per_arm == {}
        # Matrices still loaded despite the junk gamma state.
        assert "code" in fresh.A
