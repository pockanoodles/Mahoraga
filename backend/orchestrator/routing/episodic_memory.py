"""
Episodic memory for the retrieval-augmented bandit.

Stores task context vectors alongside their observed (agent, reward) outcomes in an
HNSW approximate-nearest-neighbour index.  At routing time, the k most similar past
episodes are retrieved and their rewards are used to bias the bandit's prior.

Design:
  - Index space : L2 on the 9-dimensional TaskContext feature vector.
  - Storage     : hnswlib in-memory index + parallel numpy arrays for metadata.
  - Persistence : index saved to <state_dir>/episodic_memory.bin,
                  metadata saved to <state_dir>/episodic_memory.meta.json.
  - Capacity    : capped at MAX_EPISODES (default 10 000); FIFO eviction via
                  index rebuild (cheap: rebuild takes < 50 ms for 10 k points).

Reward shaping formula
    bias(a) = mean(reward_i for episode_i where agent_i == a, weighted by sim_i)
    adjusted_prior(a) = (1 - α) * linucb_exploit(a) + α * bias(a)

where α = MEMORY_ALPHA (default 0.20) — a small nudge, not a full override.
The bias is only applied when k ≥ MIN_EPISODES_FOR_BIAS retrieved episodes contain
at least one outcome for agent a.
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import numpy as np

try:
    import hnswlib
    _HNSWLIB_AVAILABLE = True
except ImportError:
    _HNSWLIB_AVAILABLE = False


DIM = 9                     # TaskContext feature vector dimensionality
MAX_EPISODES = 10_000       # FIFO cap
MEMORY_ALPHA: float = 0.20  # Blend weight for memory bias (0 = disabled)
MIN_EPISODES_FOR_BIAS = 3   # Minimum retrieved neighbours to trust the bias
_EF_CONSTRUCTION = 200      # HNSW build-time connectivity parameter
_M = 16                     # HNSW max connections per node (affects recall/speed)
_EF_SEARCH = 50             # HNSW search-time beam width


@dataclass
class Episode:
    """One stored experience: (context_vector, agent, reward)."""
    vector: np.ndarray    # shape (DIM,)
    agent: str
    reward: float


class EpisodicMemory:
    """HNSW-backed episodic memory for similarity-weighted reward shaping.

    Usage:
        mem = EpisodicMemory(state_dir=Path("~/.mahoraga-v2"))

        # After each task:
        mem.add(context.to_vector(), agent="aider", reward=0.82)

        # At routing time (before final agent selection):
        biases = mem.query_biases(context.to_vector(), available_agents=["aider", "ollama"])
        # biases = {"aider": 0.81, "ollama": 0.70}  — blend into UCB scores
    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._vectors: list[np.ndarray] = []   # parallel list for metadata
        self._agents: list[str] = []
        self._rewards: list[float] = []
        self._index: Any = None                 # hnswlib.Index | None
        self._dirty: bool = False               # index needs rebuild

        self._state_dir = Path(state_dir) if state_dir else None
        if self._state_dir and _HNSWLIB_AVAILABLE:
            self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._vectors)

    def add(self, vector: np.ndarray, agent: str, reward: float) -> None:
        """Store one episode.  Triggers FIFO eviction when MAX_EPISODES is reached."""
        if len(self._vectors) >= MAX_EPISODES:
            self._evict(MAX_EPISODES // 10)  # drop oldest 10 %

        self._vectors.append(vector.astype(np.float32))
        self._agents.append(agent)
        self._rewards.append(reward)
        self._dirty = True

        if self._state_dir and _HNSWLIB_AVAILABLE:
            self._persist()

    def query_biases(
        self,
        vector: np.ndarray,
        available_agents: list[str],
        k: int = 10,
    ) -> dict[str, float]:
        """Return similarity-weighted mean reward per agent over the k nearest episodes.

        Returns an empty dict when the index is not yet built or hnswlib is unavailable.
        Only agents with ≥ MIN_EPISODES_FOR_BIAS neighbours contribute a bias.
        """
        if not _HNSWLIB_AVAILABLE or len(self._vectors) < MIN_EPISODES_FOR_BIAS:
            return {}

        self._maybe_rebuild()

        if self._index is None:
            return {}

        k_actual = min(k, len(self._vectors))
        labels, distances = self._index.knn_query(
            vector.astype(np.float32).reshape(1, -1), k=k_actual
        )
        labels = labels[0]
        distances = distances[0]

        # Convert L2 distances to similarity weights: sim = exp(-d)
        sims = np.exp(-distances)

        # Accumulate weighted rewards per agent
        weighted_sum: dict[str, float] = {}
        weight_total: dict[str, float] = {}
        counts: dict[str, int] = {}

        for idx, sim in zip(labels, sims):
            a = self._agents[idx]
            if a in available_agents:
                weighted_sum[a] = weighted_sum.get(a, 0.0) + sim * self._rewards[idx]
                weight_total[a] = weight_total.get(a, 0.0) + sim
                counts[a] = counts.get(a, 0) + 1

        biases: dict[str, float] = {}
        for a in available_agents:
            if counts.get(a, 0) >= MIN_EPISODES_FOR_BIAS and weight_total.get(a, 0) > 0:
                biases[a] = round(weighted_sum[a] / weight_total[a], 4)

        return biases

    # ── Persistence ────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save index + metadata to state_dir.  Called after every add()."""
        if self._state_dir is None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._maybe_rebuild()
        if self._index is not None:
            index_path = str(self._state_dir / "episodic_memory.bin")
            self._index.save_index(index_path)
        meta = {
            "dim": DIM,
            "agents": self._agents,
            "rewards": self._rewards,
            "size": len(self._vectors),
        }
        meta_tmp = self._state_dir / "episodic_memory.meta.json.tmp"
        meta_tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(str(meta_tmp), str(self._state_dir / "episodic_memory.meta.json"))

    def _load(self) -> None:
        """Load persisted index + metadata on startup.

        If the index is corrupted or was saved with a different DIM, logs a warning
        and starts fresh rather than crashing.
        """
        if self._state_dir is None:
            return
        meta_path = self._state_dir / "episodic_memory.meta.json"
        index_path = self._state_dir / "episodic_memory.bin"
        if not meta_path.exists() or not index_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            agents = meta["agents"]
            rewards = meta["rewards"]
            n = meta["size"]

            # Dimension guard: if persisted dim doesn't match current DIM, reinit.
            saved_dim = meta.get("dim")
            if saved_dim is not None and saved_dim != DIM:
                logger.warning(
                    "episodic_memory: persisted index has dim=%d but current DIM=%d. "
                    "Reinitialising memory (old episodes discarded).",
                    saved_dim, DIM,
                )
                return

            if n != len(agents) or n != len(rewards):
                logger.warning(
                    "episodic_memory: metadata size mismatch (size=%d, agents=%d, rewards=%d). "
                    "Reinitialising memory.",
                    n, len(agents), len(rewards),
                )
                return

            idx = hnswlib.Index(space="l2", dim=DIM)
            idx.load_index(str(index_path), max_elements=MAX_EPISODES)
            idx.set_ef(_EF_SEARCH)

            # Reconstruct vectors from the index (hnswlib stores them)
            all_ids = list(range(n))
            vectors = [idx.get_items([i])[0] for i in all_ids]

            self._index = idx
            self._vectors = [np.array(v, dtype=np.float32) for v in vectors]
            self._agents = agents
            self._rewards = rewards
            self._dirty = False
        except Exception as exc:
            logger.warning(
                "episodic_memory: failed to load persisted state (%s: %s). "
                "Reinitialising memory.",
                type(exc).__name__, exc,
            )

    # ── Internal ───────────────────────────────────────────────────────────────

    def _maybe_rebuild(self) -> None:
        """Rebuild the HNSW index if vectors have been added since the last build."""
        if not self._dirty or not self._vectors or not _HNSWLIB_AVAILABLE:
            return
        n = len(self._vectors)
        idx = hnswlib.Index(space="l2", dim=DIM)
        idx.init_index(
            max_elements=max(n, 64),
            ef_construction=_EF_CONSTRUCTION,
            M=_M,
        )
        idx.set_ef(_EF_SEARCH)
        data = np.stack(self._vectors, axis=0)
        idx.add_items(data, list(range(n)))
        self._index = idx
        self._dirty = False

    def _evict(self, n_drop: int) -> None:
        """Drop the oldest n_drop episodes (FIFO).  Forces index rebuild."""
        self._vectors = self._vectors[n_drop:]
        self._agents = self._agents[n_drop:]
        self._rewards = self._rewards[n_drop:]
        self._dirty = True
