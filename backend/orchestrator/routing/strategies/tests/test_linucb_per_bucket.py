"""Unit tests for LinUCBPerBucketRouter.

Covers: initialisation paths (cold, average, pooled), bucket isolation
(updates in one bucket do not contaminate another), persistence
round-trip, v2-state migration into the legacy bucket, warm-start
injection, and same-shape compute_scores output.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.linucb_per_bucket import (
    LinUCBPerBucketRouter,
    _LEGACY_BUCKET,
    _PERSISTENCE_VERSION,
)
from backend.orchestrator.routing.strategies.static import classify_bucket


# ── Helpers ──────────────────────────────────────────────────────────────────


class _Task:
    """Minimal task with a goal — what TaskContext.from_task expects."""

    def __init__(self, goal: str) -> None:
        self.goal = goal


def _ctx(goal: str) -> TaskContext:
    return TaskContext.from_task(_Task(goal))


# Goals chosen to land in distinct classifier buckets (verified by
# classify_bucket below).
_RESEARCH_GOAL = "explain how transformers attention works"
_DEBUG_GOAL = "fix the NullPointerException in auth.py line 42"
_CODEGEN_GOAL = "write a function that computes Fibonacci numbers"


@pytest.fixture(autouse=True)
def _verify_test_buckets():
    """Sanity guard — tests assume these prompts land in distinct buckets."""
    assert classify_bucket(_ctx(_RESEARCH_GOAL)) == "research"
    assert classify_bucket(_ctx(_DEBUG_GOAL)) == "debug"


# ── Construction ─────────────────────────────────────────────────────────────


class TestConstruction:
    def test_default_state_is_empty(self) -> None:
        s = LinUCBPerBucketRouter()
        assert s.A == {}
        assert s.b == {}
        assert s.t == {}
        assert s.d == 9
        assert s.alpha == 1.0
        assert s.decay == 0.98
        assert 0.0 <= s.bucket_pooling_weight <= 1.0

    def test_name_matches_registry(self) -> None:
        from backend.orchestrator.routing.bandit_router import STRATEGIES
        assert STRATEGIES["linucb_per_bucket"] is LinUCBPerBucketRouter
        assert LinUCBPerBucketRouter.name == "linucb_per_bucket"


# ── Initialisation paths ─────────────────────────────────────────────────────


class TestInitialisation:
    def test_first_arm_in_bucket_is_cold_start(self) -> None:
        s = LinUCBPerBucketRouter()
        s._init_agent("research", "ollama")
        # Cold start: A = I, b = prior · 1
        assert np.allclose(s.A["research"]["ollama"], np.identity(9))
        # Unknown agent falls back to default prior of 0.5.
        assert np.allclose(s.b["research"]["ollama"], 0.5 * np.ones((9, 1)))

    def test_independent_buckets_initialise_independently(self) -> None:
        s = LinUCBPerBucketRouter()
        s._init_agent("research", "ollama")
        s._init_agent("debug", "ollama")
        # Both fresh cold-start → identical matrices for the same agent.
        assert np.allclose(
            s.A["research"]["ollama"], s.A["debug"]["ollama"]
        )
        assert np.allclose(
            s.b["research"]["ollama"], s.b["debug"]["ollama"]
        )
        # But they're SEPARATE storage — modifying one shouldn't mutate
        # the other.
        s.A["research"]["ollama"] += 5.0
        assert not np.allclose(
            s.A["research"]["ollama"], s.A["debug"]["ollama"]
        )

    def test_cross_bucket_pooling_seeds_new_bucket(self) -> None:
        """When agent has been observed in bucket A and we initialise it
        in bucket B, with pooling > 0, the bucket-B init should reflect
        bucket-A's accumulated state."""
        s = LinUCBPerBucketRouter(bucket_pooling_weight=0.5)
        # Train agent "ollama" hard on the research bucket.
        for _ in range(5):
            s.update(_ctx(_RESEARCH_GOAL), "ollama", 0.95)

        before_pooling = s.A["research"]["ollama"].copy()

        # Now initialise ollama in debugging bucket — should pool from research.
        s._init_agent("debug", "ollama")
        debugging_A = s.A["debug"]["ollama"]

        # The new debugging A should not equal the cold-start identity:
        # half-cold-half-research-pool.
        assert not np.allclose(debugging_A, np.identity(9))
        # And it should reflect the magnitude of the pooled signal.
        cold = np.identity(9)
        expected = 0.5 * cold + 0.5 * before_pooling
        assert np.allclose(debugging_A, expected, atol=1e-8)

    def test_zero_pooling_weight_means_full_specialisation(self) -> None:
        s = LinUCBPerBucketRouter(bucket_pooling_weight=0.0)
        for _ in range(5):
            s.update(_ctx(_RESEARCH_GOAL), "ollama", 0.95)
        # New bucket init should NOT pull research signal.
        s._init_agent("debug", "ollama")
        assert np.allclose(s.A["debug"]["ollama"], np.identity(9))

    def test_init_idempotent(self) -> None:
        s = LinUCBPerBucketRouter()
        s._init_agent("research", "aider")
        snap = s.A["research"]["aider"].copy()
        s._init_agent("research", "aider")
        assert np.allclose(s.A["research"]["aider"], snap)


# ── Bucket isolation under updates ───────────────────────────────────────────


class TestBucketIsolation:
    def test_update_in_one_bucket_does_not_change_another(self) -> None:
        s = LinUCBPerBucketRouter(bucket_pooling_weight=0.0)
        # Pre-touch both buckets so they exist with cold-start matrices.
        s._init_agent("research", "ollama")
        s._init_agent("debug", "ollama")
        debug_A_before = s.A["debug"]["ollama"].copy()
        debug_b_before = s.b["debug"]["ollama"].copy()

        # Hammer research with updates.
        for _ in range(20):
            s.update(_ctx(_RESEARCH_GOAL), "ollama", 0.95)

        assert np.allclose(s.A["debug"]["ollama"], debug_A_before)
        assert np.allclose(s.b["debug"]["ollama"], debug_b_before)

    def test_select_agent_uses_bucket_specific_state(self) -> None:
        """Select on bucket A reflects bucket A's θ, not bucket B's.

        We train all three arms in both buckets so LinUCB's cold-start
        exploration bonus doesn't dominate — the test isolates the
        per-bucket θ specialisation, not the cold-start dynamics.
        """
        s = LinUCBPerBucketRouter(bucket_pooling_weight=0.0)
        # Train all three agents in research with ollama as the winner.
        for _ in range(15):
            s.update(_ctx(_RESEARCH_GOAL), "ollama", 0.95)
            s.update(_ctx(_RESEARCH_GOAL), "aider", 0.40)
            s.update(_ctx(_RESEARCH_GOAL), "claude", 0.40)
        # Train all three in debugging with aider as the winner.
        for _ in range(15):
            s.update(_ctx(_DEBUG_GOAL), "aider", 0.95)
            s.update(_ctx(_DEBUG_GOAL), "ollama", 0.40)
            s.update(_ctx(_DEBUG_GOAL), "claude", 0.40)

        agents = ["ollama", "aider", "claude"]
        research_picks = [
            s.select_agent(_ctx(_RESEARCH_GOAL), agents) for _ in range(20)
        ]
        debug_picks = [
            s.select_agent(_ctx(_DEBUG_GOAL), agents) for _ in range(20)
        ]

        from collections import Counter
        assert Counter(research_picks).most_common(1)[0][0] == "ollama"
        assert Counter(debug_picks).most_common(1)[0][0] == "aider"

    def test_t_counter_per_bucket(self) -> None:
        s = LinUCBPerBucketRouter()
        for _ in range(7):
            s.select_agent(_ctx(_RESEARCH_GOAL), ["ollama", "aider"])
        for _ in range(3):
            s.select_agent(_ctx(_DEBUG_GOAL), ["ollama", "aider"])
        assert s.t["research"] == 7
        assert s.t["debug"] == 3


# ── compute_scores shape ─────────────────────────────────────────────────────


class TestComputeScores:
    def test_returns_same_keys_as_v1(self) -> None:
        s = LinUCBPerBucketRouter()
        scores = s.compute_scores(_ctx(_RESEARCH_GOAL), ["ollama", "aider"])
        assert set(scores) == {"ollama", "aider"}
        for arm_data in scores.values():
            assert "ucb" in arm_data
            assert "exploit" in arm_data
            assert "explore" in arm_data
            # Plus a bucket field for telemetry.
            assert arm_data["bucket"] == "research"

    def test_compute_scores_does_not_tick_t(self) -> None:
        s = LinUCBPerBucketRouter()
        s.compute_scores(_ctx(_RESEARCH_GOAL), ["ollama"])
        # compute_scores must be read-only — no t increment, no _last_scores.
        assert s.t.get("research", 0) == 0
        assert getattr(s, "_last_scores", {}) == {}


# ── Persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_v3_roundtrip(self, tmp_path: Path) -> None:
        s1 = LinUCBPerBucketRouter()
        for _ in range(8):
            s1.update(_ctx(_RESEARCH_GOAL), "ollama", 0.9)
        for _ in range(5):
            s1.update(_ctx(_DEBUG_GOAL), "aider", 0.85)
        path = str(tmp_path / "state.json")
        s1.save_state(path)

        # Inspect the file format.
        raw = json.loads(Path(path).read_text())
        assert raw["version"] == _PERSISTENCE_VERSION
        assert "buckets" in raw
        assert "research" in raw["buckets"]
        assert "debug" in raw["buckets"]

        # Roundtrip via load_state.
        s2 = LinUCBPerBucketRouter()
        s2.load_state(path)
        for bucket in s1.A:
            for agent in s1.A[bucket]:
                assert np.allclose(s2.A[bucket][agent], s1.A[bucket][agent])
                assert np.allclose(s2.b[bucket][agent], s1.b[bucket][agent])

    def test_v2_state_migrates_to_legacy_bucket(self, tmp_path: Path) -> None:
        """Load a v1/v2 flat-format state file into the per-bucket strategy.
        The flat agents dict goes into the legacy bucket so user history
        is preserved."""
        v2_state = {
            "d": 9,
            "alpha": 1.0,
            "decay": 0.98,
            "t": 42,
            "agents": {
                "ollama": {
                    "A": np.identity(9).tolist(),
                    "b": (0.7 * np.ones((9, 1))).tolist(),
                },
                "aider": {
                    "A": (2 * np.identity(9)).tolist(),
                    "b": (np.ones((9, 1))).tolist(),
                },
            },
        }
        path = str(tmp_path / "v2.json")
        Path(path).write_text(json.dumps(v2_state))

        s = LinUCBPerBucketRouter()
        s.load_state(path)
        assert _LEGACY_BUCKET in s.A
        assert "ollama" in s.A[_LEGACY_BUCKET]
        assert "aider" in s.A[_LEGACY_BUCKET]
        assert s.t[_LEGACY_BUCKET] == 42

    def test_v2_state_migration_preserves_matrices(self, tmp_path: Path) -> None:
        original_A = (3 * np.identity(9)).tolist()
        original_b = (0.5 * np.ones((9, 1))).tolist()
        v2_state = {
            "d": 9,
            "alpha": 1.0,
            "decay": 0.98,
            "t": 1,
            "agents": {"aider": {"A": original_A, "b": original_b}},
        }
        path = str(tmp_path / "v2.json")
        Path(path).write_text(json.dumps(v2_state))

        s = LinUCBPerBucketRouter()
        s.load_state(path)
        assert np.allclose(s.A[_LEGACY_BUCKET]["aider"], np.array(original_A))
        assert np.allclose(s.b[_LEGACY_BUCKET]["aider"], np.array(original_b))

    def test_dim_mismatch_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path / "wrong.json")
        Path(path).write_text(json.dumps(
            {"version": 3, "d": 14, "alpha": 1.0, "decay": 0.98, "buckets": {}}
        ))
        s = LinUCBPerBucketRouter(d=9)
        with pytest.raises(ValueError, match="d="):
            s.load_state(path)


# ── Warm-start ───────────────────────────────────────────────────────────────


class TestWarmStart:
    def test_inject_with_explicit_bucket(self) -> None:
        s = LinUCBPerBucketRouter()
        x = np.ones(9, dtype=np.float64)
        s.inject_pseudo_obs("aider", x, reward=0.9, bucket="code_editing")
        assert "code_editing" in s.A
        assert "aider" in s.A["code_editing"]
        # debugging should be untouched.
        assert "debug" not in s.A or "aider" not in s.A.get("debug", {})

    def test_inject_without_bucket_broadcasts(self) -> None:
        """When called without bucket on an existing strategy with multiple
        buckets, the pseudo-obs is broadcast across all known buckets so
        no bucket is starved of warm-start signal."""
        s = LinUCBPerBucketRouter()
        # Touch two buckets first.
        s._init_agent("research", "aider")
        s._init_agent("code_editing", "aider")
        before = {
            b: s.A[b]["aider"].copy() for b in ("research", "code_editing")
        }

        x = np.ones(9, dtype=np.float64)
        s.inject_pseudo_obs("aider", x, reward=0.9, lambda_prior=2.0)

        for b in ("research", "code_editing"):
            assert not np.allclose(s.A[b]["aider"], before[b])

    def test_inject_on_empty_strategy_uses_legacy_bucket(self) -> None:
        s = LinUCBPerBucketRouter()
        x = np.ones(9, dtype=np.float64)
        s.inject_pseudo_obs("aider", x, reward=0.9)
        assert _LEGACY_BUCKET in s.A
        assert "aider" in s.A[_LEGACY_BUCKET]


# ── Diagnostics ──────────────────────────────────────────────────────────────


class TestDiagnostics:
    def test_per_bucket_summary(self) -> None:
        s = LinUCBPerBucketRouter()
        s.update(_ctx(_RESEARCH_GOAL), "ollama", 0.9)
        s.update(_ctx(_RESEARCH_GOAL), "aider", 0.6)
        s.update(_ctx(_DEBUG_GOAL), "aider", 0.85)
        summary = s.per_bucket_summary()
        assert "research" in summary
        assert "debug" in summary
        assert summary["research"]["n_arms"] == 2
        assert summary["debug"]["n_arms"] == 1


# ── Sanity: integrates with BanditRouter unchanged ────────────────────────────


class TestBanditRouterIntegration:
    def test_router_can_select_strategy_per_bucket(
        self, tmp_path: Path
    ) -> None:
        from backend.orchestrator.routing import BanditRouter, TaskOutcome
        from backend.orchestrator.routing.decision_log import DecisionLogger

        class _Reg:
            def all(self):
                return [type("A", (), {"name": n})() for n in
                        ["ollama", "aider", "claude"]]

        router = BanditRouter(
            strategy="linucb_per_bucket",
            registry=_Reg(),
            logger=DecisionLogger(db_path=tmp_path / "d.db"),
            state_path=tmp_path / "bandit_state.json",
        )
        # Smoke test: route + observe should not raise.
        task = _Task(_RESEARCH_GOAL)
        agent = router.route(task)
        assert agent in {"ollama", "aider", "claude"}
        router.observe(task, TaskOutcome(True, 1.0, 0.0, 0.9, agent))
        # State has at least the research bucket populated.
        assert "research" in router.strategy.A

    def test_get_stats_includes_per_bucket_summary(
        self, tmp_path: Path
    ) -> None:
        from backend.orchestrator.routing import BanditRouter, TaskOutcome
        from backend.orchestrator.routing.decision_log import DecisionLogger

        class _Reg:
            def all(self):
                return [type("A", (), {"name": n})() for n in ["ollama", "aider"]]

        router = BanditRouter(
            strategy="linucb_per_bucket",
            registry=_Reg(),
            logger=DecisionLogger(db_path=tmp_path / "d.db"),
            state_path=tmp_path / "bandit_state.json",
        )
        # Hit two buckets.
        for goal in (_RESEARCH_GOAL, _DEBUG_GOAL):
            task = _Task(goal)
            agent = router.route(task)
            router.observe(task, TaskOutcome(True, 1.0, 0.0, 0.8, agent))

        # Strategy itself exposes per_bucket_summary.
        summary = router.strategy.per_bucket_summary()
        assert set(summary) == {"research", "debug"}
