"""Phase-3 router integration tests — memory-mode dispatch and embedding wiring.

These tests verify that the `MAHORAGA_MEMORY_MODE` flag (locked design
decision #8) correctly gates retrieval and ingest in BanditRouter, and that
the embedding service is invoked only on the semantic path.

We inject a deterministic FakeEncoder via the EmbeddingService model= kwarg
so the tests don't depend on sentence-transformers being installed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

import numpy as np
import pytest

from backend.orchestrator.routing import BanditRouter, TaskOutcome
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.embeddings import DIM as EMB_DIM
from backend.orchestrator.routing.embeddings import EmbeddingService
from backend.orchestrator.routing import bandit_router as br_mod


# ── Test doubles ──────────────────────────────────────────────────────────────


class FakeEncoder:
    """Deterministic encoder. Each unique normalized text gets a stable
    unit vector. Tracks call count for cache assertions."""

    def __init__(self) -> None:
        self.call_count = 0

    def encode(
        self,
        texts: Sequence[str],
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        self.call_count += 1
        out = np.zeros((len(texts), EMB_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(
                hashlib.sha256(t.encode("utf-8")).digest()[:8], "big"
            )
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(EMB_DIM).astype(np.float32)
            if normalize_embeddings:
                v = v / np.linalg.norm(v)
            out[i] = v
        return out


class MockTask:
    def __init__(self, goal: str = "write a python function", task_id: str = "t1") -> None:
        self.goal = goal
        self.id = task_id


class MockRegistry:
    def __init__(self, agents: list[str]) -> None:
        self._agents = agents

    def all(self):
        return [type("A", (), {"name": a})() for a in self._agents]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def make_router(tmp_path: Path, fake_encoder: FakeEncoder):
    """Factory: build a BanditRouter wired to a temp state dir and a
    pre-seeded EmbeddingService that uses a deterministic fake model."""

    def _factory(memory_mode: str = "semantic", agents: list[str] | None = None):
        if agents is None:
            agents = ["aider", "ollama", "claude"]
        state_path = tmp_path / "bandit_state.json"
        logger = DecisionLogger(db_path=tmp_path / "decisions.db")
        router = BanditRouter(
            strategy="linucb",
            registry=MockRegistry(agents),
            logger=logger,
            state_path=state_path,
        )
        # Inject fake embedding service so tests don't load the real model.
        router._embedding_service = EmbeddingService(
            cache_path=tmp_path / "emb.sqlite",
            model=fake_encoder,
        )
        router._embedding_init_attempted = True

        # Set the env var
        import os
        os.environ["MAHORAGA_MEMORY_MODE"] = memory_mode
        return router, fake_encoder

    yield _factory
    # Tear down env var
    import os
    os.environ.pop("MAHORAGA_MEMORY_MODE", None)


# ── _resolve_memory_mode ──────────────────────────────────────────────────────


class TestResolveMode:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAHORAGA_MEMORY_MODE", raising=False)
        # MahoragaConfig may or may not have memory_mode; the function should
        # return DEFAULT_MEMORY_MODE either way (catches KeyError).
        mode = br_mod._resolve_memory_mode()
        assert mode == br_mod.DEFAULT_MEMORY_MODE

    @pytest.mark.parametrize("value,expected", [
        ("semantic", "semantic"),
        ("keyword", "keyword"),
        ("off", "off"),
        ("SEMANTIC", "semantic"),  # case-insensitive
        ("  keyword  ", "keyword"),  # whitespace tolerated
    ])
    def test_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        expected: str,
    ) -> None:
        monkeypatch.setenv("MAHORAGA_MEMORY_MODE", value)
        assert br_mod._resolve_memory_mode() == expected

    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAHORAGA_MEMORY_MODE", "garbage-mode")
        assert br_mod._resolve_memory_mode() == br_mod.DEFAULT_MEMORY_MODE


# ── Mode dispatch in route() ──────────────────────────────────────────────────


class TestRouteDispatch:
    def test_off_mode_skips_memory_entirely(self, make_router) -> None:
        router, encoder = make_router(memory_mode="off")
        # Seed memory with episodes that would normally bias selection.
        for _ in range(20):
            outcome = TaskOutcome(
                success=True, latency_s=1.0, cost_usd=0.0,
                quality_score=0.9, agent_name="aider",
            )
            router.observe(MockTask(goal="seeded task"), outcome)

        encoder.call_count = 0  # reset
        # Routing should not call the encoder.
        agent = router.route(MockTask(goal="new task"))
        assert agent in ["aider", "ollama", "claude"]
        assert encoder.call_count == 0

    def test_keyword_mode_does_not_call_encoder(self, make_router) -> None:
        router, encoder = make_router(memory_mode="keyword")
        for _ in range(15):
            router.observe(
                MockTask(goal="seeded"),
                TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
            )
        encoder.call_count = 0
        router.route(MockTask(goal="new"))
        assert encoder.call_count == 0

    def test_semantic_mode_invokes_encoder_on_route(self, make_router) -> None:
        router, encoder = make_router(memory_mode="semantic")
        encoder.call_count = 0
        router.route(MockTask(goal="some task"))
        # The encoder must be hit at least once for the query embedding.
        assert encoder.call_count >= 1

    def test_semantic_mode_falls_back_to_handcraft_below_threshold(
        self, make_router
    ) -> None:
        """If fewer than MIN_EPISODES_FOR_BIAS embedded episodes exist,
        query_semantic returns {} and the router should fall through to
        handcraft retrieval (not crash, not return zero biases)."""
        router, _ = make_router(memory_mode="semantic")
        # Add only 1 embedded episode (MIN is 3) — semantic returns {}
        router.observe(
            MockTask(goal="single seed"),
            TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
        )
        # Routing should not raise.
        agent = router.route(MockTask(goal="new task"))
        assert agent in ["aider", "ollama", "claude"]

    def test_semantic_unavailable_service_falls_back(
        self, make_router, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the embedding service is unavailable, semantic mode should
        fall back to handcraft retrieval transparently."""
        router, _ = make_router(memory_mode="semantic")
        # Force the service to look unavailable.
        router._embedding_service = None
        router._embedding_init_attempted = True

        # Should not crash; handcraft path takes over.
        agent = router.route(MockTask(goal="new task"))
        assert agent in ["aider", "ollama", "claude"]


# ── Mode dispatch in observe() ────────────────────────────────────────────────


class TestObserveStorage:
    def test_off_mode_still_stores_handcraft(self, make_router) -> None:
        router, encoder = make_router(memory_mode="off")
        encoder.call_count = 0
        router.observe(
            MockTask(goal="off-mode task"),
            TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
        )
        # Handcraft history accumulates; no encoder call.
        assert router._memory.size == 1
        assert router._memory.semantic_size == 0
        assert encoder.call_count == 0

    def test_keyword_mode_stores_handcraft_only(self, make_router) -> None:
        router, encoder = make_router(memory_mode="keyword")
        encoder.call_count = 0
        router.observe(
            MockTask(goal="kw task"),
            TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
        )
        assert router._memory.size == 1
        assert router._memory.semantic_size == 0
        assert encoder.call_count == 0

    def test_semantic_mode_stores_both(self, make_router) -> None:
        router, encoder = make_router(memory_mode="semantic")
        encoder.call_count = 0
        router.observe(
            MockTask(goal="sem task"),
            TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
        )
        assert router._memory.size == 1
        assert router._memory.semantic_size == 1
        assert encoder.call_count >= 1
        # The episode's task_hash should be the sha256 of normalized goal.
        expected_hash = hashlib.sha256(b"sem task").hexdigest()
        assert router._memory._task_hashes[0] == expected_hash

    def test_semantic_mode_handles_unavailable_service(self, make_router) -> None:
        """When the embedding service is unavailable, observe should still
        succeed (stores handcraft, embedding=None)."""
        router, _ = make_router(memory_mode="semantic")
        router._embedding_service = None
        router._embedding_init_attempted = True

        router.observe(
            MockTask(goal="x"),
            TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
        )
        assert router._memory.size == 1
        assert router._memory.semantic_size == 0


# ── End-to-end round-trip ─────────────────────────────────────────────────────


class TestSemanticRoundTrip:
    def test_seeded_history_biases_subsequent_routing(
        self, make_router
    ) -> None:
        """End-to-end: in semantic mode, observing N high-reward outcomes for
        agent A on a stable task description should bias subsequent routing
        of similar tasks toward A."""
        router, _ = make_router(memory_mode="semantic")

        # Seed: same task, agent "aider" gets reward 0.95 ten times.
        for _ in range(10):
            router.observe(
                MockTask(goal="implement a binary search in python"),
                TaskOutcome(True, 1.0, 0.0, 0.95, "aider"),
            )

        # Same task again — should pick aider preferentially.
        agent = router.route(
            MockTask(goal="implement a binary search in python")
        )
        # The bandit might still explore, but with 10 strong same-task
        # observations + memory bias, aider should be the most-likely pick.
        # We accept any valid agent but verify the memory_biases were
        # consulted.
        assert agent in ["aider", "ollama", "claude"]
        biases = router._memory.query_semantic(
            router._encode_query("implement a binary search in python"),
            available_agents=["aider", "ollama", "claude"],
        )
        assert "aider" in biases
        assert biases["aider"] > 0.8  # high reward should propagate


# ── get_stats reports new fields ──────────────────────────────────────────────


class TestStatsReporting:
    def test_get_stats_includes_memory_mode_and_semantic_size(
        self, make_router
    ) -> None:
        router, _ = make_router(memory_mode="semantic")
        for _ in range(3):
            router.observe(
                MockTask(goal="t"),
                TaskOutcome(True, 1.0, 0.0, 0.9, "aider"),
            )
        stats = router.get_stats()
        em = stats["episodic_memory"]
        assert em["size"] == 3
        assert em["semantic_size"] == 3
        assert em["memory_mode"] == "semantic"
