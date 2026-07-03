"""Tests for the Phase-2 semantic-augmented EpisodicMemory.

These tests cover the new `add_episode()` / `query_semantic()` paths and the
v2 persistence layout. The handcraft-only path is tested by the existing
`test_episodic_memory.py`.

These tests construct embeddings by hand (deterministic unit vectors). They
do NOT load the real MiniLM model — that's covered by the Phase-1 embedding
service tests.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pytest

from backend.orchestrator.routing.episodic_memory import (
    DIM_HANDCRAFT,
    DIM_SEMANTIC,
    INDEX_VERSION,
    MIN_EPISODES_FOR_BIAS,
    SEMANTIC_MODEL_ID,
    EpisodicMemory,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hc(seed: int) -> np.ndarray:
    """Random 9-dim handcraft vector."""
    rng = np.random.default_rng(seed)
    return rng.random(DIM_HANDCRAFT).astype(np.float32)


def _emb(seed: int, *, base: np.ndarray | None = None, noise: float = 0.0) -> np.ndarray:
    """Deterministic 384-dim L2-normalised embedding.

    If `base` is provided, returns `base + noise * random` then re-normalises —
    use this to generate a cluster of mutually-similar vectors.
    """
    rng = np.random.default_rng(seed)
    if base is None:
        v = rng.standard_normal(DIM_SEMANTIC).astype(np.float32)
    else:
        v = base + noise * rng.standard_normal(DIM_SEMANTIC).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


# ── add_episode + size accounting ─────────────────────────────────────────────


class TestAddEpisode:
    def test_with_embedding_increments_both_sizes(self) -> None:
        mem = EpisodicMemory()
        assert mem.size == 0
        assert mem.semantic_size == 0

        mem.add_episode(
            handcraft_vector=_hc(0),
            embedding=_emb(0),
            agent="aider",
            reward=0.8,
            task_hash="t0",
        )
        assert mem.size == 1
        assert mem.semantic_size == 1

    def test_without_embedding_skips_semantic(self) -> None:
        mem = EpisodicMemory()
        mem.add_episode(
            handcraft_vector=_hc(0),
            embedding=None,
            agent="aider",
            reward=0.8,
        )
        assert mem.size == 1
        assert mem.semantic_size == 0

    def test_legacy_add_does_not_create_semantic(self) -> None:
        mem = EpisodicMemory()
        mem.add(_hc(0), "aider", 0.8)
        assert mem.size == 1
        assert mem.semantic_size == 0

    def test_bad_embedding_shape_skipped_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mem = EpisodicMemory()
        bad_emb = np.zeros(100, dtype=np.float32)
        with caplog.at_level(
            logging.WARNING,
            logger="backend.orchestrator.routing.episodic_memory",
        ):
            mem.add_episode(
                handcraft_vector=_hc(0),
                embedding=bad_emb,
                agent="aider",
                reward=0.8,
            )
        assert mem.size == 1
        assert mem.semantic_size == 0
        assert any("bad shape" in r.message for r in caplog.records)

    def test_non_finite_embedding_rejected(self) -> None:
        mem = EpisodicMemory()
        bad = np.zeros(DIM_SEMANTIC, dtype=np.float32)
        bad[0] = np.nan
        mem.add_episode(
            handcraft_vector=_hc(0),
            embedding=bad,
            agent="aider",
            reward=0.5,
        )
        assert mem.size == 1
        assert mem.semantic_size == 0

    def test_bad_handcraft_shape_episode_dropped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mem = EpisodicMemory()
        with caplog.at_level(
            logging.WARNING,
            logger="backend.orchestrator.routing.episodic_memory",
        ):
            mem.add_episode(
                handcraft_vector=np.zeros(20, dtype=np.float32),
                embedding=_emb(0),
                agent="aider",
                reward=0.5,
            )
        assert mem.size == 0


# ── query_semantic — guards ───────────────────────────────────────────────────


class TestQuerySemanticGuards:
    def test_empty_memory_returns_empty(self) -> None:
        mem = EpisodicMemory()
        assert mem.query_semantic(_emb(0), available_agents=["aider"]) == {}

    def test_none_embedding_returns_empty(self) -> None:
        mem = EpisodicMemory()
        for i in range(10):
            mem.add_episode(
                handcraft_vector=_hc(i),
                embedding=_emb(i),
                agent="aider",
                reward=0.8,
            )
        # Defensive: caller passing a None embedding shouldn't crash.
        assert mem.query_semantic(None, available_agents=["aider"]) == {}  # type: ignore[arg-type]

    def test_wrong_dim_embedding_returns_empty(self) -> None:
        mem = EpisodicMemory()
        for i in range(10):
            mem.add_episode(
                handcraft_vector=_hc(i),
                embedding=_emb(i),
                agent="aider",
                reward=0.8,
            )
        bad = np.ones(123, dtype=np.float32)
        assert mem.query_semantic(bad, available_agents=["aider"]) == {}

    def test_below_min_embedded_episodes_returns_empty(self) -> None:
        mem = EpisodicMemory()
        # Add MIN-1 embedded episodes; semantic retrieval should refuse.
        for i in range(MIN_EPISODES_FOR_BIAS - 1):
            mem.add_episode(
                handcraft_vector=_hc(i),
                embedding=_emb(i),
                agent="aider",
                reward=0.8,
            )
        # And add several handcraft-only episodes — should not satisfy
        # the *embedded* threshold.
        for i in range(20):
            mem.add(_hc(i + 100), "aider", 0.8)

        assert mem.query_semantic(_emb(99), available_agents=["aider"]) == {}


# ── query_semantic — discrimination ───────────────────────────────────────────


class TestQuerySemanticDiscrimination:
    def test_high_reward_agent_gets_higher_bias(self) -> None:
        """Two agents with the *same* embedding cluster but different rewards
        should produce different biases — higher reward → higher bias."""
        mem = EpisodicMemory()
        rng_seed = 7
        base = _emb(rng_seed)
        for i in range(20):
            # Tightly clustered: small noise around the same direction.
            e_a = _emb(rng_seed * 100 + i, base=base, noise=0.05)
            e_b = _emb(rng_seed * 200 + i, base=base, noise=0.05)
            mem.add_episode(
                handcraft_vector=_hc(i), embedding=e_a,
                agent="aider", reward=0.90,
            )
            mem.add_episode(
                handcraft_vector=_hc(i + 100), embedding=e_b,
                agent="ollama", reward=0.30,
            )

        biases = mem.query_semantic(base, available_agents=["aider", "ollama"])
        assert "aider" in biases
        assert "ollama" in biases
        assert biases["aider"] > biases["ollama"]

    def test_only_available_agents_returned(self) -> None:
        mem = EpisodicMemory()
        for i in range(10):
            mem.add_episode(
                handcraft_vector=_hc(i), embedding=_emb(i),
                agent="aider", reward=0.8,
            )
            mem.add_episode(
                handcraft_vector=_hc(i + 50), embedding=_emb(i + 50),
                agent="ollama", reward=0.6,
            )
        biases = mem.query_semantic(_emb(0), available_agents=["ollama"])
        assert "aider" not in biases

    def test_distant_clusters_pull_correct_neighbours(self) -> None:
        """A query close to cluster A should retrieve A's episodes; agent A's
        bias should reflect cluster A's rewards."""
        mem = EpisodicMemory()
        # Two well-separated clusters in embedding space.
        cluster_a_seed = 1
        cluster_b_seed = 9999
        base_a = _emb(cluster_a_seed)
        base_b = _emb(cluster_b_seed)

        for i in range(15):
            mem.add_episode(
                handcraft_vector=_hc(i),
                embedding=_emb(cluster_a_seed + i + 1, base=base_a, noise=0.02),
                agent="aider", reward=0.95,
            )
            mem.add_episode(
                handcraft_vector=_hc(i + 100),
                embedding=_emb(cluster_b_seed + i + 1, base=base_b, noise=0.02),
                agent="aider", reward=0.10,
            )

        # Query near cluster A — should pull cluster A's episodes (high reward).
        biases_a = mem.query_semantic(base_a, available_agents=["aider"])
        # Query near cluster B — should pull cluster B's episodes (low reward).
        biases_b = mem.query_semantic(base_b, available_agents=["aider"])

        assert "aider" in biases_a and "aider" in biases_b
        assert biases_a["aider"] > biases_b["aider"]
        # Effect size: 15-NN heavily one-cluster, so the gap should be large.
        assert biases_a["aider"] - biases_b["aider"] > 0.3


# ── Independence of the two paths ─────────────────────────────────────────────


class TestPathIndependence:
    def test_handcraft_works_when_no_embeddings(self) -> None:
        mem = EpisodicMemory()
        for i in range(10):
            mem.add(_hc(i), "aider", 0.7)
        biases = mem.query_biases(_hc(0), available_agents=["aider"])
        # Handcraft path should be unaffected by absence of embeddings.
        assert "aider" in biases

    def test_semantic_works_when_some_episodes_lack_embeddings(self) -> None:
        mem = EpisodicMemory()
        # Mix: 5 handcraft-only episodes + 10 with embeddings (same agent).
        base = _emb(0)
        for i in range(5):
            mem.add(_hc(i), "aider", 0.4)
        for i in range(10):
            mem.add_episode(
                handcraft_vector=_hc(i + 100),
                embedding=_emb(i + 1, base=base, noise=0.05),
                agent="aider", reward=0.85,
            )
        biases = mem.query_semantic(base, available_agents=["aider"])
        # Should retrieve only the embedded episodes (high reward).
        assert "aider" in biases
        # All the embedded episodes have reward 0.85, so the bias should be
        # close to 0.85 — proves the handcraft-only episodes were not pulled
        # in by accident.
        assert biases["aider"] > 0.75


# ── Persistence: v2 round-trip ────────────────────────────────────────────────


class TestPersistenceV2:
    def test_v2_roundtrip_preserves_both_paths(self, tmp_path: Path) -> None:
        mem = EpisodicMemory(state_dir=tmp_path)
        for i in range(8):
            mem.add_episode(
                handcraft_vector=_hc(i),
                embedding=_emb(i),
                agent="aider" if i % 2 == 0 else "ollama",
                reward=float(i) / 10,
                task_hash=f"hash-{i}",
                timestamp=1700000000.0 + i,
            )

        mem2 = EpisodicMemory(state_dir=tmp_path)
        assert mem2.size == 8
        assert mem2.semantic_size == 8
        assert mem2._agents == mem._agents
        assert mem2._rewards == mem._rewards
        assert mem2._task_hashes == mem._task_hashes
        assert mem2._timestamps == mem._timestamps

        # Verify embeddings round-tripped to within float32 precision.
        for orig, loaded in zip(mem._embeddings, mem2._embeddings):
            assert orig is not None and loaded is not None
            np.testing.assert_allclose(orig, loaded, atol=1e-6)

    def test_v2_metadata_schema(self, tmp_path: Path) -> None:
        mem = EpisodicMemory(state_dir=tmp_path)
        mem.add_episode(
            handcraft_vector=_hc(0),
            embedding=_emb(0),
            agent="aider",
            reward=0.8,
            task_hash="abc",
        )
        meta = json.loads(
            (tmp_path / "episodic_memory.meta.json").read_text()
        )
        assert meta["version"] == INDEX_VERSION
        assert meta["dim_handcraft"] == DIM_HANDCRAFT
        assert meta["dim_semantic"] == DIM_SEMANTIC
        assert meta["model_id"] == SEMANTIC_MODEL_ID
        assert meta["has_embeddings"] == [True]
        assert meta["task_hashes"] == ["abc"]
        # Backwards-compat alias preserved:
        assert meta["dim"] == DIM_HANDCRAFT

    def test_mixed_episodes_persist_correctly(self, tmp_path: Path) -> None:
        mem = EpisodicMemory(state_dir=tmp_path)
        # Interleave: episode 0 has embedding, 1 doesn't, 2 does, 3 doesn't…
        for i in range(10):
            if i % 2 == 0:
                mem.add_episode(
                    handcraft_vector=_hc(i), embedding=_emb(i),
                    agent="aider", reward=0.8,
                )
            else:
                mem.add(_hc(i), "ollama", 0.5)

        mem2 = EpisodicMemory(state_dir=tmp_path)
        assert mem2.size == 10
        assert mem2.semantic_size == 5
        # Verify the bitmap survives.
        for i, e in enumerate(mem2._embeddings):
            if i % 2 == 0:
                assert e is not None
            else:
                assert e is None

    def test_semantic_index_file_created(self, tmp_path: Path) -> None:
        mem = EpisodicMemory(state_dir=tmp_path)
        mem.add_episode(
            handcraft_vector=_hc(0), embedding=_emb(0),
            agent="aider", reward=0.7,
        )
        assert (tmp_path / "episodic_memory.bin").exists()
        assert (tmp_path / "episodic_memory_v2.bin").exists()


# ── Persistence: legacy v1 compatibility ──────────────────────────────────────


class TestLegacyV1Compat:
    def test_v1_metadata_loads_as_handcraft_only(
        self, tmp_path: Path
    ) -> None:
        """A pre-v2 install upgrading to the new code should keep its
        episodes accessible through the handcraft path."""
        # Simulate a v1 install: write some episodes via legacy add().
        mem_v1 = EpisodicMemory(state_dir=tmp_path)
        for i in range(8):
            mem_v1.add(_hc(i), "aider", 0.7)

        # Manually rewrite the metadata to look like v1 (no version field,
        # no v2 extras). The .bin file is already valid handcraft.
        meta_path = tmp_path / "episodic_memory.meta.json"
        v1_meta = {
            "dim": DIM_HANDCRAFT,
            "agents": ["aider"] * 8,
            "rewards": [0.7] * 8,
            "size": 8,
        }
        meta_path.write_text(json.dumps(v1_meta))

        # Reload — should detect legacy format and load handcraft only.
        mem_v2 = EpisodicMemory(state_dir=tmp_path)
        assert mem_v2.size == 8
        assert mem_v2.semantic_size == 0

        # Handcraft retrieval still works.
        biases = mem_v2.query_biases(_hc(0), available_agents=["aider"])
        assert "aider" in biases
        # Semantic retrieval correctly returns empty.
        assert mem_v2.query_semantic(
            _emb(0), available_agents=["aider"]
        ) == {}

    def test_model_id_mismatch_drops_embeddings(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Save with the current model id.
        mem = EpisodicMemory(state_dir=tmp_path)
        for i in range(5):
            mem.add_episode(
                handcraft_vector=_hc(i), embedding=_emb(i),
                agent="aider", reward=0.7,
            )

        # Tamper: overwrite metadata with a fake model id.
        meta_path = tmp_path / "episodic_memory.meta.json"
        meta = json.loads(meta_path.read_text())
        meta["model_id"] = "some-other-model"
        meta_path.write_text(json.dumps(meta))

        with caplog.at_level(
            logging.WARNING,
            logger="backend.orchestrator.routing.episodic_memory",
        ):
            mem2 = EpisodicMemory(state_dir=tmp_path)

        assert mem2.size == 5  # episodes themselves preserved
        assert mem2.semantic_size == 0  # but semantic retrieval offline
        assert any("model_id" in r.message for r in caplog.records)


# ── FIFO eviction ─────────────────────────────────────────────────────────────


class TestEviction:
    def test_eviction_purges_both_lists(self) -> None:
        import backend.orchestrator.routing.episodic_memory as em_mod

        original_max = em_mod.MAX_EPISODES
        em_mod.MAX_EPISODES = 20
        try:
            mem = EpisodicMemory()
            for i in range(25):
                mem.add_episode(
                    handcraft_vector=_hc(i), embedding=_emb(i),
                    agent="aider", reward=0.5,
                )
            # Both indices stay in step.
            assert mem.size <= 20
            assert mem.semantic_size <= 20
            assert mem.size == mem.semantic_size  # all had embeddings
        finally:
            em_mod.MAX_EPISODES = original_max

    def test_semantic_file_removed_when_no_embedded_episodes_remain(
        self, tmp_path: Path
    ) -> None:
        """If eviction leaves zero embedded episodes, the v2 file should be
        cleaned up so a stale file doesn't get loaded later."""
        mem = EpisodicMemory(state_dir=tmp_path)
        # Add one embedded, then force eviction by replacing with handcraft-only.
        mem.add_episode(
            handcraft_vector=_hc(0), embedding=_emb(0),
            agent="aider", reward=0.7,
        )
        sem_path = tmp_path / "episodic_memory_v2.bin"
        assert sem_path.exists()

        # Evict that episode and add a handcraft-only one.
        mem._evict(1)
        mem.add(_hc(1), "aider", 0.7)
        # _persist runs inside add(); semantic index is now empty.
        assert mem.semantic_size == 0
        assert not sem_path.exists()
