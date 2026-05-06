"""
Semantic embedding service for task descriptions.

Provides 384-dim L2-normalised vectors via all-MiniLM-L6-v2 for the
two-tower episodic memory upgrade (see docs/semantic-routing.md §4).

Two-layer cache:
  1. In-memory LRU (1000 entries) — hot path, <0.01 ms
  2. SQLite at ~/.mahoraga-v2/embedding_cache.sqlite — warm path, <1 ms
  3. Model inference — cold path, ~5 ms on M-series CPU

Normalisation contract: text is `strip().lower()`-normalised before both
hashing AND encoding. Casing/whitespace variants of the same text share
an embedding (and a cache slot) — this is intentional, locked in design
review.

Graceful degradation: if sentence-transformers isn't installed or the
model fails to load, `available` returns False and `encode()` returns
None. Callers should fall back to keyword-based retrieval.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

import numpy as np

from backend.orchestrator.lru_cache import ThreadSafeLRUCache

logger = logging.getLogger(__name__)


MODEL_ID = "all-MiniLM-L6-v2"
DIM = 384
LRU_SIZE = 1_000
DEFAULT_CACHE_PATH = Path.home() / ".mahoraga-v2" / "embedding_cache.sqlite"
_BATCH_SIZE = 64


class _Encoder(Protocol):
    """Duck-typed encoder interface (sentence-transformers SentenceTransformer
    satisfies this)."""

    def encode(
        self,
        texts: Sequence[str],
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        show_progress_bar: bool = False,
    ) -> np.ndarray: ...


# Process-wide singleton for the default model. The model is ~90 MB and
# loading it is ~1 s, so we load once and share the instance across every
# EmbeddingService that uses the default loader. Tests that pass an
# explicit `model=` to the constructor bypass this entirely.
_default_model: Optional[_Encoder] = None
_default_model_attempted: bool = False
_default_model_lock = threading.Lock()


def _try_load_default_model() -> Optional[_Encoder]:
    """Best-effort load of the default MiniLM model, cached at module level.

    Returns None on failure (sentence-transformers missing, model download
    failed, etc.). Subsequent calls return the cached result without
    re-attempting the load.
    """
    global _default_model, _default_model_attempted  # noqa: PLW0603
    if _default_model_attempted:
        return _default_model

    with _default_model_lock:
        if _default_model_attempted:
            return _default_model
        _default_model_attempted = True

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning(
                "sentence-transformers not installed; embedding service unavailable. "
                "Install with `pip install -r requirements-semantic.txt`."
            )
            return None
        try:
            _default_model = SentenceTransformer(MODEL_ID)
        except Exception as e:  # noqa: BLE001 — torch/HF can raise many things
            logger.warning("Failed to load embedding model %s: %s", MODEL_ID, e)
            _default_model = None
        return _default_model


class EmbeddingService:
    """Semantic embedding service with two-layer caching and graceful degradation.

    The model is loaded lazily on first use, not at construction. Pass a
    pre-built encoder to `model=` for testing or to inject an alternative.
    """

    def __init__(
        self,
        cache_path: Path | str | None = DEFAULT_CACHE_PATH,
        model: Optional[_Encoder] = None,
    ) -> None:
        self._cache_path: Optional[Path] = Path(cache_path) if cache_path else None
        self._lru: ThreadSafeLRUCache[str, np.ndarray] = ThreadSafeLRUCache(LRU_SIZE)
        self._load_lock = threading.Lock()
        self._sqlite_local = threading.local()

        # _model = None and _load_attempted = False  → not yet tried
        # _model = None and _load_attempted = True   → tried, failed
        # _model is encoder                          → ready
        self._model: Optional[_Encoder] = model
        self._load_attempted: bool = model is not None

        if self._cache_path:
            self._init_sqlite_schema()

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def model_id(self) -> str:
        return MODEL_ID

    @property
    def dim(self) -> int:
        return DIM

    @property
    def available(self) -> bool:
        """True if the encoder is loaded (or can be loaded). Triggers lazy load."""
        self._ensure_model()
        return self._model is not None

    def encode(self, text: str) -> Optional[np.ndarray]:
        """Encode `text` to a 384-dim L2-normalised float32 vector.

        Returns None when:
          - text is empty or whitespace-only
          - the embedding service is unavailable
          - the model produces a non-finite or wrongly-shaped vector
        """
        if not text or not text.strip():
            return None

        key = self._hash(text)

        cached = self._lru.get(key)
        if cached is not None:
            return cached

        sqlite_hit = self._sqlite_get(key)
        if sqlite_hit is not None:
            self._lru.put(key, sqlite_hit)
            return sqlite_hit

        self._ensure_model()
        if self._model is None:
            return None

        normalized = self._normalize_text(text)
        try:
            arr = self._model.encode(
                [normalized],
                normalize_embeddings=True,
                batch_size=1,
                show_progress_bar=False,
            )
        except Exception as e:  # noqa: BLE001 — model can raise many things
            logger.error("Encode failed for %r: %s", normalized[:80], e)
            return None

        vec = np.asarray(arr[0], dtype=np.float32)
        if vec.shape != (DIM,) or not np.isfinite(vec).all():
            logger.error("Bad embedding (shape=%s) for %r", vec.shape, normalized[:80])
            return None

        self._lru.put(key, vec)
        self._sqlite_put(key, vec)
        return vec

    def encode_batch(self, texts: Sequence[str]) -> list[Optional[np.ndarray]]:
        """Encode many texts; cached entries skip the model. Misses go in
        batches of 64 to the encoder."""
        results: list[Optional[np.ndarray]] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_normalized: list[str] = []
        miss_keys: list[str] = []

        for i, text in enumerate(texts):
            if not text or not text.strip():
                continue
            key = self._hash(text)
            cached = self._lru.get(key)
            if cached is None:
                cached = self._sqlite_get(key)
                if cached is not None:
                    self._lru.put(key, cached)
            if cached is not None:
                results[i] = cached
            else:
                miss_indices.append(i)
                miss_normalized.append(self._normalize_text(text))
                miss_keys.append(key)

        if not miss_indices:
            return results

        self._ensure_model()
        if self._model is None:
            return results

        try:
            arrs = self._model.encode(
                miss_normalized,
                normalize_embeddings=True,
                batch_size=_BATCH_SIZE,
                show_progress_bar=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Batch encode failed (%d texts): %s", len(miss_indices), e)
            return results

        for idx, key, arr in zip(miss_indices, miss_keys, arrs):
            vec = np.asarray(arr, dtype=np.float32)
            if vec.shape != (DIM,) or not np.isfinite(vec).all():
                continue
            self._lru.put(key, vec)
            self._sqlite_put(key, vec)
            results[idx] = vec

        return results

    @staticmethod
    def similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity. Assumes both inputs are L2-normalised (which all
        outputs of `encode()` are)."""
        return float(np.dot(a, b))

    def close(self) -> None:
        """Close the per-thread SQLite connection. Safe to call multiple times."""
        conn = getattr(self._sqlite_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._sqlite_local.conn = None

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.strip().lower()

    @classmethod
    def _hash(cls, text: str) -> str:
        return hashlib.sha256(cls._normalize_text(text).encode("utf-8")).hexdigest()

    def _ensure_model(self) -> None:
        if self._load_attempted:
            return
        with self._load_lock:
            if self._load_attempted:
                return
            self._model = _try_load_default_model()
            self._load_attempted = True

    # ── SQLite cache layer ──────────────────────────────────────────────────

    def _conn(self) -> Optional[sqlite3.Connection]:
        if not self._cache_path:
            return None
        conn: Optional[sqlite3.Connection] = getattr(self._sqlite_local, "conn", None)
        if conn is not None:
            return conn
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._cache_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError as e:
            logger.warning("SQLite cache unusable (%s); recreating from scratch", e)
            try:
                self._cache_path.unlink(missing_ok=True)
                conn = sqlite3.connect(str(self._cache_path), check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
            except Exception as e2:  # noqa: BLE001
                logger.error("Could not recreate SQLite cache: %s", e2)
                return None
        self._sqlite_local.conn = conn
        return conn

    def _init_sqlite_schema(self) -> None:
        conn = self._conn()
        if conn is None:
            return
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    text_hash  TEXT PRIMARY KEY,
                    model_id   TEXT NOT NULL,
                    dim        INTEGER NOT NULL,
                    embedding  BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_embeddings_model
                    ON embeddings(model_id);
                """
            )
            conn.commit()
        except sqlite3.DatabaseError as e:
            logger.error("SQLite schema init failed: %s", e)

    def _sqlite_get(self, key: str) -> Optional[np.ndarray]:
        conn = self._conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT dim, embedding FROM embeddings "
                "WHERE text_hash = ? AND model_id = ?",
                (key, MODEL_ID),
            ).fetchone()
        except sqlite3.DatabaseError as e:
            logger.warning("SQLite read failed (%s); resetting cache", e)
            self._reset_sqlite()
            return None
        if row is None:
            return None
        dim, blob = row
        if dim != DIM:
            return None
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape != (DIM,):
            return None
        return vec.copy()  # frombuffer view is read-only; callers may need writeable

    def _sqlite_put(self, key: str, vec: np.ndarray) -> None:
        conn = self._conn()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO embeddings "
                "(text_hash, model_id, dim, embedding) VALUES (?, ?, ?, ?)",
                (key, MODEL_ID, DIM, vec.astype(np.float32).tobytes()),
            )
            conn.commit()
        except sqlite3.DatabaseError as e:
            logger.warning("SQLite write failed: %s", e)

    def _reset_sqlite(self) -> None:
        if not self._cache_path:
            return
        conn = getattr(self._sqlite_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._sqlite_local.conn = None
        try:
            self._cache_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._init_sqlite_schema()
