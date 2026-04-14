"""Tests for EpisodicMemory — HNSW-backed retrieval-augmented bandit."""
import numpy as np
from backend.orchestrator.routing.episodic_memory import (
    EpisodicMemory, DIM, MIN_EPISODES_FOR_BIAS, MEMORY_ALPHA,
)


def _vec(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.random(DIM).astype(np.float32)


def test_empty_memory_returns_no_biases():
    mem = EpisodicMemory()
    biases = mem.query_biases(_vec(), available_agents=["aider", "ollama"])
    assert biases == {}


def test_size_increments_on_add():
    mem = EpisodicMemory()
    assert mem.size == 0
    mem.add(_vec(0), "aider", 0.8)
    assert mem.size == 1
    mem.add(_vec(1), "ollama", 0.5)
    assert mem.size == 2


def test_below_min_episodes_returns_no_biases():
    """query_biases returns {} when fewer than MIN_EPISODES_FOR_BIAS entries exist."""
    mem = EpisodicMemory()
    for i in range(MIN_EPISODES_FOR_BIAS - 1):
        mem.add(_vec(i), "aider", 0.7)
    biases = mem.query_biases(_vec(99), available_agents=["aider"])
    assert biases == {}


def test_biases_returned_after_enough_episodes():
    mem = EpisodicMemory()
    for i in range(10):
        mem.add(_vec(i), "aider", 0.85)
        mem.add(_vec(i + 100), "ollama", 0.40)
    biases = mem.query_biases(_vec(0), available_agents=["aider", "ollama"])
    # Both agents should have biases
    assert "aider" in biases
    assert "ollama" in biases


def test_high_reward_agent_gets_higher_bias():
    """Agent consistently rewarded higher should receive a higher memory bias."""
    mem = EpisodicMemory()
    rng = np.random.default_rng(7)
    # Use similar vectors so all episodes are "similar" to the query
    base = rng.random(DIM).astype(np.float32)
    for _ in range(20):
        noise = rng.normal(0, 0.05, DIM).astype(np.float32)
        mem.add(base + noise, "aider", 0.90)
        mem.add(base + noise, "ollama", 0.30)

    biases = mem.query_biases(base, available_agents=["aider", "ollama"])
    assert biases["aider"] > biases["ollama"]


def test_only_available_agents_appear_in_biases():
    mem = EpisodicMemory()
    for i in range(10):
        mem.add(_vec(i), "aider", 0.8)
        mem.add(_vec(i + 50), "ollama", 0.6)
    # Only ask for ollama
    biases = mem.query_biases(_vec(0), available_agents=["ollama"])
    assert "aider" not in biases


def test_biases_in_zero_one_range():
    mem = EpisodicMemory()
    for i in range(15):
        mem.add(_vec(i), "aider", float(i % 10) / 10.0)
    biases = mem.query_biases(_vec(0), available_agents=["aider"])
    if "aider" in biases:
        assert 0.0 <= biases["aider"] <= 1.0


def test_fifo_eviction_at_capacity():
    """Memory stays at MAX_EPISODES after overflow."""
    import backend.orchestrator.routing.episodic_memory as em_mod
    original_max = em_mod.MAX_EPISODES
    em_mod.MAX_EPISODES = 20
    try:
        mem = EpisodicMemory()
        for i in range(25):
            mem.add(_vec(i), "aider", 0.5)
        assert mem.size <= 20
    finally:
        em_mod.MAX_EPISODES = original_max


def test_save_load_roundtrip(tmp_path):
    """Episodes survive a save/load cycle."""
    mem = EpisodicMemory(state_dir=tmp_path)
    for i in range(10):
        mem.add(_vec(i), "aider" if i % 2 == 0 else "ollama", float(i) / 10)

    mem2 = EpisodicMemory(state_dir=tmp_path)
    assert mem2.size == 10
    assert mem2._agents == mem._agents
    assert mem2._rewards == mem._rewards


def test_memory_alpha_constant_reasonable():
    """MEMORY_ALPHA should be in (0, 0.5] — a nudge, not a takeover."""
    assert 0.0 < MEMORY_ALPHA <= 0.5


def test_dim_mismatch_reinitialises(tmp_path, caplog):
    """If persisted index has wrong dim, memory reinitialises with a warning."""
    import json, logging

    meta = {"dim": 10, "agents": ["aider"] * 5, "rewards": [0.5] * 5, "size": 5}
    (tmp_path / "episodic_memory.meta.json").write_text(json.dumps(meta))
    (tmp_path / "episodic_memory.bin").write_bytes(b"not-a-real-index")

    with caplog.at_level(logging.WARNING, logger="backend.orchestrator.routing.episodic_memory"):
        mem = EpisodicMemory(state_dir=tmp_path)

    assert mem.size == 0, "Memory should reinitialise on dim mismatch"
    assert any("dim=" in r.message for r in caplog.records), "Should log a warning about dim mismatch"


def test_corrupt_index_reinitialises(tmp_path, caplog):
    """If the index file is corrupt, memory reinitialises with a warning."""
    import json, logging

    meta = {"dim": DIM, "agents": ["aider"] * 5, "rewards": [0.5] * 5, "size": 5}
    (tmp_path / "episodic_memory.meta.json").write_text(json.dumps(meta))
    (tmp_path / "episodic_memory.bin").write_bytes(b"corrupt-garbage")

    with caplog.at_level(logging.WARNING, logger="backend.orchestrator.routing.episodic_memory"):
        mem = EpisodicMemory(state_dir=tmp_path)

    assert mem.size == 0, "Memory should reinitialise on corrupt index"
    assert any("Reinitialising" in r.message for r in caplog.records)


def test_dim_written_to_metadata(tmp_path):
    """Persisted metadata includes 'dim' so future loads can detect mismatches."""
    import json

    mem = EpisodicMemory(state_dir=tmp_path)
    for i in range(5):
        mem.add(_vec(i), "aider", 0.7)

    meta = json.loads((tmp_path / "episodic_memory.meta.json").read_text())
    assert meta.get("dim") == DIM


def test_bandit_router_integrates_memory(tmp_path):
    """BanditRouter.observe() stores episodes; get_stats() reports memory size."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.reward import TaskOutcome

    state_path = tmp_path / "bandit_state.json"
    router = BanditRouter(strategy="linucb", state_path=state_path)
    assert router.get_stats()["episodic_memory"]["size"] == 0

    task = type("T", (), {"goal": "write a test", "id": "t1"})()
    router.observe(task, TaskOutcome(
        success=True, latency_s=2.0, cost_usd=0.0,
        quality_score=0.8, agent_name="ollama", bucket="test",
    ))
    assert router.get_stats()["episodic_memory"]["size"] == 1
