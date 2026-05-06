"""
Episodic memory for the retrieval-augmented bandit (v2 — two-tower).

Each episode now carries TWO retrieval keys:

  1. The 9-dim handcrafted TaskContext vector (legacy path, dim=9, L2 distance).
  2. The 384-dim semantic embedding from MiniLM-L6-v2 (new path, dim=384,
     inner-product on L2-normalised vectors = cosine).

Two parallel HNSW indices serve them. The bandit consumes the *richer* of the
two through the same α=MEMORY_ALPHA reward-shaping pathway it already uses.
LinUCB's covariance dimensionality is unchanged.

Persistence layout (under <state_dir>/):
  episodic_memory.bin           — handcraft (dim=9, space=l2). Legacy filename.
  episodic_memory_v2.bin        — semantic (dim=384, space=ip). Phase-2 addition.
  episodic_memory.meta.json     — single sidecar; schema versioned.

Metadata schema versions:
  v1 (legacy): {dim: 9, agents, rewards, size}
               loaded as handcraft-only; new episodes go into v2 layout on
               next persist.
  v2 (new):    {version: 2, dim_handcraft, dim_semantic, model_id,
                agents, rewards, task_hashes, timestamps, has_embeddings, size}

Reward-shaping formula (unchanged from v1 — the only thing that changed is the
quality of the retrieved neighbours):

    bias(a) = Σᵢ sim(query, episodeᵢ) · rewardᵢ   /  Σᵢ sim(query, episodeᵢ)
              (over episodes where agentᵢ == a in the top-k retrieval)

    sim is `exp(-distance)` for both spaces — for L2 this is a Gaussian decay,
    for inner-product on unit vectors `distance = 1 - cos`, so
    sim = exp(cos - 1) ∈ [exp(-2), 1]. Both spaces produce a positive monotone
    weight that downweights distant neighbours — consistent semantics.

    adjusted_score(a) = (1 - α) · linucb_exploit(a) + α · bias(a)

The bias is only applied for agents with ≥ MIN_EPISODES_FOR_BIAS retrieved
neighbours. Other agents fall through to the bandit's exploit score.
"""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

import numpy as np

try:
    import hnswlib
    _HNSWLIB_AVAILABLE = True
except ImportError:
    _HNSWLIB_AVAILABLE = False


DIM_HANDCRAFT = 9                  # TaskContext feature vector dimensionality
DIM_SEMANTIC = 384                 # MiniLM-L6-v2 embedding dimensionality
SEMANTIC_MODEL_ID = "all-MiniLM-L6-v2"
MAX_EPISODES = 10_000              # FIFO cap (per spec §5.7)
MEMORY_ALPHA: float = 0.20         # Blend weight for memory bias (0 = disabled)
MIN_EPISODES_FOR_BIAS = 3          # Minimum retrieved neighbours per agent
INDEX_VERSION = 2                  # Bumped when persistence schema changes
_EF_CONSTRUCTION = 200             # HNSW build-time connectivity
_M = 16                            # HNSW max connections per node
_EF_SEARCH = 50                    # HNSW search-time beam width

# Backwards-compat alias — the v1 module exposed `DIM` as the (only) dimension.
# Several call sites and tests still import this name; it equals DIM_HANDCRAFT.
DIM = DIM_HANDCRAFT


@dataclass
class Episode:
    """One stored experience."""

    handcraft_vector: np.ndarray              # shape (DIM_HANDCRAFT,)
    agent: str
    reward: float
    embedding: Optional[np.ndarray] = None    # shape (DIM_SEMANTIC,) or None
    task_hash: Optional[str] = None           # sha256 of original task description
    timestamp: float = field(default_factory=time.time)


class EpisodicMemory:
    """HNSW-backed episodic memory for similarity-weighted reward shaping.

    Two-tower retrieval: handcraft (always available) and semantic (optional).

    Usage (legacy / handcraft-only — unchanged):

        mem = EpisodicMemory(state_dir=Path("~/.mahoraga-v2"))
        mem.add(context.to_vector(), agent="aider", reward=0.82)
        biases = mem.query_biases(context.to_vector(),
                                  available_agents=["aider", "ollama"])

    Usage (semantic-augmented — Phase 2+):

        mem.add_episode(
            handcraft_vector=context.to_vector(),
            embedding=embedding_service.encode(task.description),
            agent="aider", reward=0.82,
            task_hash="abc123", timestamp=time.time(),
        )
        biases = mem.query_semantic(embedding,
                                    available_agents=["aider", "ollama"])
    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        # Parallel storage. Index `i` is the same episode across all lists.
        self._handcraft_vectors: list[np.ndarray] = []
        self._embeddings: list[Optional[np.ndarray]] = []
        self._agents: list[str] = []
        self._rewards: list[float] = []
        self._task_hashes: list[Optional[str]] = []
        self._timestamps: list[float] = []

        # Two HNSW indices; each lazily (re)built when its vectors change.
        self._index_handcraft: Any = None
        self._index_semantic: Any = None
        self._handcraft_dirty = False
        self._semantic_dirty = False

        # Maps semantic-index label → episode index (only some episodes have
        # an embedding, so the semantic index is potentially smaller than the
        # episode list).
        self._semantic_label_to_idx: list[int] = []

        # Tracks the model_id of currently-loaded embeddings. If a future add
        # uses a different model, that's a programming error and we log it.
        self._loaded_model_id: Optional[str] = None

        self._state_dir = Path(state_dir) if state_dir else None
        if self._state_dir and _HNSWLIB_AVAILABLE:
            self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Total number of stored episodes (with or without embeddings)."""
        return len(self._handcraft_vectors)

    @property
    def semantic_size(self) -> int:
        """Number of episodes that carry a semantic embedding."""
        return sum(1 for e in self._embeddings if e is not None)

    def add(
        self, vector: np.ndarray, agent: str, reward: float
    ) -> None:
        """Backwards-compat: store an episode without a semantic embedding.

        New code should prefer `add_episode()` to also record the embedding,
        task hash, and timestamp.
        """
        self.add_episode(
            handcraft_vector=vector,
            agent=agent,
            reward=reward,
            embedding=None,
            task_hash=None,
            timestamp=None,
        )

    def add_episode(
        self,
        *,
        handcraft_vector: np.ndarray,
        agent: str,
        reward: float,
        embedding: Optional[np.ndarray] = None,
        task_hash: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """Store one episode. Embedding is optional; pass None when only the
        9-dim handcraft path is available (e.g. embedding service is offline).

        Triggers FIFO eviction when MAX_EPISODES is reached.
        """
        if len(self._handcraft_vectors) >= MAX_EPISODES:
            self._evict(MAX_EPISODES // 10)

        hc = np.asarray(handcraft_vector, dtype=np.float32)
        if hc.shape != (DIM_HANDCRAFT,):
            logger.warning(
                "add_episode: handcraft vector has shape %s, expected (%d,); "
                "skipping",
                hc.shape, DIM_HANDCRAFT,
            )
            return

        emb: Optional[np.ndarray] = None
        if embedding is not None:
            cand = np.asarray(embedding, dtype=np.float32)
            if cand.shape == (DIM_SEMANTIC,) and np.isfinite(cand).all():
                emb = cand
            else:
                logger.warning(
                    "add_episode: embedding has bad shape %s or non-finite "
                    "values; storing without semantic key",
                    cand.shape,
                )

        self._handcraft_vectors.append(hc)
        self._embeddings.append(emb)
        self._agents.append(agent)
        self._rewards.append(reward)
        self._task_hashes.append(task_hash)
        self._timestamps.append(
            timestamp if timestamp is not None else time.time()
        )

        self._handcraft_dirty = True
        if emb is not None:
            self._semantic_dirty = True
            if self._loaded_model_id is None:
                self._loaded_model_id = SEMANTIC_MODEL_ID

        if self._state_dir and _HNSWLIB_AVAILABLE:
            self._persist()

    def query_biases(
        self,
        vector: np.ndarray,
        available_agents: list[str],
        k: int = 10,
    ) -> dict[str, float]:
        """Handcraft retrieval (legacy). Returns weighted-mean reward per agent
        from the k nearest episodes in the 9-dim space.

        Returns {} when hnswlib is unavailable, fewer than
        MIN_EPISODES_FOR_BIAS episodes are stored, or no agent has enough
        neighbours.
        """
        if not _HNSWLIB_AVAILABLE or len(self._handcraft_vectors) < MIN_EPISODES_FOR_BIAS:
            return {}

        self._maybe_rebuild_handcraft()
        if self._index_handcraft is None:
            return {}

        k_actual = min(k, len(self._handcraft_vectors))
        labels, distances = self._index_handcraft.knn_query(
            np.asarray(vector, dtype=np.float32).reshape(1, -1), k=k_actual
        )
        return self._biases_from_labels(
            labels[0], distances[0], available_agents
        )

    def query_semantic(
        self,
        embedding: np.ndarray,
        available_agents: list[str],
        k: int = 10,
    ) -> dict[str, float]:
        """Semantic retrieval (Phase 2+). Returns weighted-mean reward per
        agent from the k nearest episodes in the 384-dim embedding space.

        Returns {} when:
          - hnswlib is unavailable, or
          - the embedding is None / wrong shape, or
          - fewer than MIN_EPISODES_FOR_BIAS embedded episodes exist, or
          - no agent has enough neighbours.
        """
        if not _HNSWLIB_AVAILABLE:
            return {}
        if embedding is None:
            return {}

        emb = np.asarray(embedding, dtype=np.float32)
        if emb.shape != (DIM_SEMANTIC,) or not np.isfinite(emb).all():
            return {}

        if self.semantic_size < MIN_EPISODES_FOR_BIAS:
            return {}

        self._maybe_rebuild_semantic()
        if self._index_semantic is None:
            return {}

        k_actual = min(k, len(self._semantic_label_to_idx))
        labels, distances = self._index_semantic.knn_query(
            emb.reshape(1, -1), k=k_actual
        )
        # Map semantic-index labels → episode indices
        episode_indices = np.asarray(
            [self._semantic_label_to_idx[int(lbl)] for lbl in labels[0]],
            dtype=np.int64,
        )
        return self._biases_from_labels(
            episode_indices, distances[0], available_agents
        )

    # ── Internals: shared retrieval math ──────────────────────────────────────

    def _biases_from_labels(
        self,
        episode_indices: Any,
        distances: np.ndarray,
        available_agents: list[str],
    ) -> dict[str, float]:
        """Compute per-agent weighted-mean reward from an episode-index list
        and matching distance array. Used by both retrieval paths."""
        sims = np.exp(-distances)

        weighted_sum: dict[str, float] = {}
        weight_total: dict[str, float] = {}
        counts: dict[str, int] = {}

        for idx, sim in zip(episode_indices, sims):
            ep = int(idx)
            a = self._agents[ep]
            if a in available_agents:
                weighted_sum[a] = weighted_sum.get(a, 0.0) + sim * self._rewards[ep]
                weight_total[a] = weight_total.get(a, 0.0) + sim
                counts[a] = counts.get(a, 0) + 1

        biases: dict[str, float] = {}
        for a in available_agents:
            if counts.get(a, 0) >= MIN_EPISODES_FOR_BIAS and weight_total.get(a, 0) > 0:
                biases[a] = round(weighted_sum[a] / weight_total[a], 4)
        return biases

    # ── Internals: HNSW management ─────────────────────────────────────────────

    def _maybe_rebuild_handcraft(self) -> None:
        if not self._handcraft_dirty or not self._handcraft_vectors or not _HNSWLIB_AVAILABLE:
            return
        n = len(self._handcraft_vectors)
        idx = hnswlib.Index(space="l2", dim=DIM_HANDCRAFT)
        idx.init_index(max_elements=max(n, 64), ef_construction=_EF_CONSTRUCTION, M=_M)
        idx.set_ef(_EF_SEARCH)
        data = np.stack(self._handcraft_vectors, axis=0)
        idx.add_items(data, list(range(n)))
        self._index_handcraft = idx
        self._handcraft_dirty = False

    def _maybe_rebuild_semantic(self) -> None:
        if not self._semantic_dirty or not _HNSWLIB_AVAILABLE:
            return
        embedded = [
            (i, e) for i, e in enumerate(self._embeddings) if e is not None
        ]
        if not embedded:
            self._index_semantic = None
            self._semantic_label_to_idx = []
            self._semantic_dirty = False
            return

        n = len(embedded)
        idx = hnswlib.Index(space="ip", dim=DIM_SEMANTIC)
        idx.init_index(max_elements=max(n, 64), ef_construction=_EF_CONSTRUCTION, M=_M)
        idx.set_ef(_EF_SEARCH)
        data = np.stack([e for _, e in embedded], axis=0).astype(np.float32)
        idx.add_items(data, list(range(n)))

        self._index_semantic = idx
        self._semantic_label_to_idx = [i for i, _ in embedded]
        self._semantic_dirty = False

    def _evict(self, n_drop: int) -> None:
        """Drop the oldest n_drop episodes (FIFO). Forces both indices to
        rebuild on the next query."""
        self._handcraft_vectors = self._handcraft_vectors[n_drop:]
        self._embeddings = self._embeddings[n_drop:]
        self._agents = self._agents[n_drop:]
        self._rewards = self._rewards[n_drop:]
        self._task_hashes = self._task_hashes[n_drop:]
        self._timestamps = self._timestamps[n_drop:]
        self._handcraft_dirty = True
        self._semantic_dirty = True

    # ── Persistence ────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save indices + metadata to state_dir."""
        if self._state_dir is None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._maybe_rebuild_handcraft()
        if self._index_handcraft is not None:
            self._index_handcraft.save_index(
                str(self._state_dir / "episodic_memory.bin")
            )

        self._maybe_rebuild_semantic()
        sem_path = self._state_dir / "episodic_memory_v2.bin"
        if self._index_semantic is not None:
            self._index_semantic.save_index(str(sem_path))
        elif sem_path.exists():
            # Eviction emptied the semantic index; remove the stale file.
            try:
                sem_path.unlink()
            except OSError:
                pass

        meta = {
            "version": INDEX_VERSION,
            # `dim` is preserved as a backwards-compat alias (= handcraft dim).
            "dim": DIM_HANDCRAFT,
            "dim_handcraft": DIM_HANDCRAFT,
            "dim_semantic": DIM_SEMANTIC,
            "model_id": self._loaded_model_id or SEMANTIC_MODEL_ID,
            "agents": self._agents,
            "rewards": self._rewards,
            "task_hashes": self._task_hashes,
            "timestamps": self._timestamps,
            "has_embeddings": [e is not None for e in self._embeddings],
            "size": len(self._handcraft_vectors),
        }
        meta_tmp = self._state_dir / "episodic_memory.meta.json.tmp"
        meta_tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(
            str(meta_tmp), str(self._state_dir / "episodic_memory.meta.json")
        )

    def _load(self) -> None:
        """Load persisted indices + metadata. Handles both v1 (legacy) and v2
        metadata. On corruption / dim / model mismatch, logs a warning and
        starts with an empty store rather than crashing."""
        if self._state_dir is None:
            return
        meta_path = self._state_dir / "episodic_memory.meta.json"
        hc_path = self._state_dir / "episodic_memory.bin"
        sem_path = self._state_dir / "episodic_memory_v2.bin"

        if not meta_path.exists() or not hc_path.exists():
            return

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "episodic_memory: metadata unreadable (%s: %s). "
                "Reinitialising memory.",
                type(exc).__name__, exc,
            )
            return

        version = meta.get("version", 1)
        agents = meta.get("agents", [])
        rewards = meta.get("rewards", [])
        n = meta.get("size", 0)

        # Dimension guard for the handcraft index. v1 used `dim`; v2 uses
        # `dim_handcraft` (and also keeps `dim` as a backwards-compat alias).
        saved_hc_dim = meta.get("dim_handcraft", meta.get("dim"))
        if saved_hc_dim is not None and saved_hc_dim != DIM_HANDCRAFT:
            logger.warning(
                "episodic_memory: persisted handcraft dim=%s but current "
                "DIM_HANDCRAFT=%d. Reinitialising memory (old episodes "
                "discarded).",
                saved_hc_dim, DIM_HANDCRAFT,
            )
            return

        if n != len(agents) or n != len(rewards):
            logger.warning(
                "episodic_memory: metadata size mismatch (size=%d, agents=%d, "
                "rewards=%d). Reinitialising memory.",
                n, len(agents), len(rewards),
            )
            return

        # Load the handcraft index.
        try:
            hc_idx = hnswlib.Index(space="l2", dim=DIM_HANDCRAFT)
            hc_idx.load_index(str(hc_path), max_elements=MAX_EPISODES)
            hc_idx.set_ef(_EF_SEARCH)
            handcraft_vectors = [
                np.array(hc_idx.get_items([i])[0], dtype=np.float32)
                for i in range(n)
            ]
        except Exception as exc:
            logger.warning(
                "episodic_memory: failed to load persisted state (%s: %s). "
                "Reinitialising memory.",
                type(exc).__name__, exc,
            )
            return

        # Optional fields, defaulted for legacy v1 metadata.
        task_hashes = meta.get("task_hashes") or [None] * n
        timestamps = meta.get("timestamps") or [0.0] * n
        has_embeddings = meta.get("has_embeddings") or [False] * n

        # Try to load the semantic index, but only if metadata says it exists,
        # the file is there, and the model matches what we expect.
        embeddings: list[Optional[np.ndarray]] = [None] * n
        sem_idx = None
        sem_label_to_idx: list[int] = []
        loaded_model_id: Optional[str] = None

        want_semantic = (
            version >= 2
            and any(has_embeddings)
            and sem_path.exists()
        )
        if want_semantic:
            saved_model = meta.get("model_id")
            saved_dim_sem = meta.get("dim_semantic")
            if saved_model != SEMANTIC_MODEL_ID:
                logger.warning(
                    "episodic_memory: persisted semantic model_id=%r differs "
                    "from current %r. Discarding semantic embeddings; run "
                    "`orch memory backfill` to regenerate.",
                    saved_model, SEMANTIC_MODEL_ID,
                )
            elif saved_dim_sem != DIM_SEMANTIC:
                logger.warning(
                    "episodic_memory: persisted semantic dim=%s but current "
                    "DIM_SEMANTIC=%d. Discarding semantic embeddings.",
                    saved_dim_sem, DIM_SEMANTIC,
                )
            else:
                try:
                    sem_idx = hnswlib.Index(space="ip", dim=DIM_SEMANTIC)
                    sem_idx.load_index(str(sem_path), max_elements=MAX_EPISODES)
                    sem_idx.set_ef(_EF_SEARCH)
                    # Reconstruct the embeddings array from the index.
                    n_sem = sum(1 for h in has_embeddings if h)
                    sem_vectors = [
                        np.array(sem_idx.get_items([i])[0], dtype=np.float32)
                        for i in range(n_sem)
                    ]
                    j = 0
                    for i, has in enumerate(has_embeddings):
                        if has:
                            embeddings[i] = sem_vectors[j]
                            sem_label_to_idx.append(i)
                            j += 1
                    loaded_model_id = SEMANTIC_MODEL_ID
                except Exception as exc:
                    logger.warning(
                        "episodic_memory: failed to load semantic index "
                        "(%s: %s). Falling back to handcraft retrieval; run "
                        "`orch memory backfill` to rebuild.",
                        type(exc).__name__, exc,
                    )
                    embeddings = [None] * n
                    sem_idx = None
                    sem_label_to_idx = []

        # Commit loaded state.
        self._index_handcraft = hc_idx
        self._handcraft_vectors = handcraft_vectors
        self._embeddings = embeddings
        self._agents = agents
        self._rewards = rewards
        self._task_hashes = list(task_hashes)
        self._timestamps = list(timestamps)
        self._index_semantic = sem_idx
        self._semantic_label_to_idx = sem_label_to_idx
        self._loaded_model_id = loaded_model_id
        self._handcraft_dirty = False
        self._semantic_dirty = False
