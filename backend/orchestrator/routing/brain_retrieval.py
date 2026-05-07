"""
A4 — Brain/journal integration (read-only retrieval).

Spec: docs/semantic-routing.md §15 — "What A1 Unlocks: A4".

This module indexes the repo-local `brain/` markdown corpus
(decisions, journal, concepts, state) into the same MiniLM-L6-v2
embedding space used by episodic memory. At route() time we can query
the index for project-context entries similar to the current task and
surface them as `_last_brain_context` for telemetry / dashboards.

Scope of v1 (this session):
  - Read brain/{decisions,journal,concepts,state}/*.md
  - Embed each entry (one entry = one file; chunking is a v2 concern).
  - Build an in-memory hnswlib semantic index (cosine via L2-norm + IP).
  - Expose `query(text, k)` returning top-k entries + similarities.

Out of scope (deliberately deferred):
  - Per-agent bias derivation. Brain entries are free text; mapping
    them to agent preferences requires either tagging convention or
    LLM extraction. Both are real design choices, not 30-min slices.
  - Persistence. Build is fast enough (~50 entries × ~5 ms each) to
    rebuild on process start. Add SQLite-backed cache when corpus
    grows past 1000 entries.
  - Wiring into bandit score blending. v1 emits signal only.

Env knobs:
  MAHORAGA_BRAIN_INTEGRATION_ENABLED  (bool, default off)
  MAHORAGA_BRAIN_DIR                  (path, default <repo>/brain)
  MAHORAGA_BRAIN_TOP_K                (int, default 3)
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

_log = logging.getLogger(__name__)

# Match the same model EpisodicMemory's semantic tower uses, so future
# fusion is a no-op on the embedding side.
DEFAULT_BRAIN_DIR = Path(__file__).resolve().parents[3] / "brain"
DEFAULT_TOP_K = 3
SEMANTIC_DIM = 384

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def resolve_enabled() -> bool:
    return _read_bool_env("MAHORAGA_BRAIN_INTEGRATION_ENABLED", default=False)


def resolve_brain_dir() -> Path:
    raw = os.environ.get("MAHORAGA_BRAIN_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_BRAIN_DIR


def resolve_top_k() -> int:
    raw = os.environ.get("MAHORAGA_BRAIN_TOP_K", "").strip()
    if not raw:
        return DEFAULT_TOP_K
    try:
        v = int(raw)
        if 1 <= v <= 50:
            return v
    except ValueError:
        pass
    return DEFAULT_TOP_K


@dataclass
class BrainEntry:
    path: str            # repo-relative
    title: str
    body: str
    kind: str            # "decisions" | "journal" | "concepts" | "state" | "overview" | "benchmarks" | "experiments" | "other"
    timestamp: Optional[str]  # YYYY-MM-DD if filename has a date prefix

    def to_dict(self) -> dict:
        d = asdict(self)
        # Truncate body for telemetry surfaces; full body still in-memory.
        d["body"] = (self.body[:240] + "…") if len(self.body) > 240 else self.body
        return d


@dataclass
class BrainHit:
    entry: BrainEntry
    similarity: float

    def to_dict(self) -> dict:
        return {"entry": self.entry.to_dict(), "similarity": self.similarity}


class BrainIndex:
    """In-memory semantic index over the brain/ corpus.

    Lifecycle:
      1. Construct with a brain dir + an EmbeddingService.
      2. Call .build() — reads files, embeds, populates hnswlib index.
      3. Call .query(text, k) for top-k similar entries.

    Safe to construct without sentence-transformers: build() short-circuits
    when the embedding service isn't available, and .available stays False.
    """

    def __init__(
        self,
        brain_dir: Path | str | None = None,
        embedding_service=None,
    ) -> None:
        self.brain_dir = Path(brain_dir) if brain_dir else resolve_brain_dir()
        self.embedding_service = embedding_service
        self.entries: list[BrainEntry] = []
        self._index = None  # hnswlib.Index | None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def size(self) -> int:
        return len(self.entries)

    # ── Reading ──────────────────────────────────────────────────────────────

    def _iter_files(self) -> Iterable[Path]:
        if not self.brain_dir.exists():
            return
        for p in self.brain_dir.rglob("*.md"):
            # Skip MEMORY.md or other top-level meta files at the brain root —
            # they're indexes, not content.
            if p.name in {"MEMORY.md", ".obsidian"}:
                continue
            yield p

    def _classify_kind(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.brain_dir)
        except ValueError:
            return "other"
        first = rel.parts[0] if rel.parts else "other"
        return first or "other"

    def _parse_entry(self, path: Path) -> Optional[BrainEntry]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _log.warning("brain_retrieval: failed to read %s (%s)", path, exc)
            return None
        text = text.strip()
        if not text:
            return None
        # Pull the first H1 as title if present, else use the filename.
        title = path.stem
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break
            if stripped:
                break
        # Date prefix in filename, e.g. "2026-04-22-mahoraga-session.md".
        m = _DATE_PREFIX_RE.match(path.stem)
        timestamp = m.group(1) if m else None
        try:
            rel_path = str(path.relative_to(self.brain_dir.parent))
        except ValueError:
            rel_path = str(path)
        return BrainEntry(
            path=rel_path,
            title=title,
            body=text,
            kind=self._classify_kind(path),
            timestamp=timestamp,
        )

    # ── Build ────────────────────────────────────────────────────────────────

    def build(self) -> int:
        """Read brain/, embed each entry, build HNSW. Returns entry count.

        No-op when:
          - brain_dir doesn't exist
          - the embedding service is unavailable
        Both leave .available = False so callers can tell.
        """
        self.entries = []
        self._index = None
        self._available = False

        if not self.brain_dir.exists():
            _log.info("brain_retrieval: %s does not exist", self.brain_dir)
            return 0

        svc = self.embedding_service
        if svc is None:
            try:
                from .embeddings import EmbeddingService
                svc = EmbeddingService()
                self.embedding_service = svc
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "brain_retrieval: failed to init EmbeddingService (%s)", exc,
                )
                return 0
        if not getattr(svc, "available", False):
            _log.info("brain_retrieval: embedding service unavailable")
            return 0

        entries: list[BrainEntry] = []
        texts: list[str] = []
        for path in self._iter_files():
            entry = self._parse_entry(path)
            if entry is None:
                continue
            entries.append(entry)
            # Title boost: prepend title once so the embedding emphasises
            # the entry's gist over body filler.
            texts.append(f"{entry.title}\n\n{entry.body}")

        if not entries:
            return 0

        embeddings = svc.encode_batch(texts)
        if embeddings is None or len(embeddings) != len(entries):
            _log.warning("brain_retrieval: embedding batch returned wrong shape")
            return 0

        # Drop entries whose embedding failed (encode_batch returns None per
        # failed item). Keep only the ones that have a usable vector.
        kept_entries: list[BrainEntry] = []
        kept_vecs: list[np.ndarray] = []
        for entry, vec in zip(entries, embeddings):
            if vec is None or not isinstance(vec, np.ndarray):
                continue
            if vec.shape != (SEMANTIC_DIM,):
                continue
            kept_entries.append(entry)
            kept_vecs.append(vec)
        if not kept_entries:
            _log.warning("brain_retrieval: no usable embeddings produced")
            return 0

        try:
            import hnswlib  # type: ignore
        except ImportError:
            _log.warning("brain_retrieval: hnswlib not installed")
            return 0

        # Cosine = inner product on L2-normalised vectors. EmbeddingService
        # already L2-normalises encode() output for the semantic tower.
        index = hnswlib.Index(space="ip", dim=SEMANTIC_DIM)
        index.init_index(
            max_elements=max(64, len(kept_entries) * 2),
            ef_construction=128, M=16,
        )
        index.add_items(
            np.asarray(kept_vecs, dtype=np.float32),
            np.arange(len(kept_entries)),
        )
        index.set_ef(min(64, len(kept_entries)))

        self.entries = kept_entries
        self._index = index
        self._available = True
        return len(kept_entries)

    # ── Query ────────────────────────────────────────────────────────────────

    def query(self, text: str, k: int = DEFAULT_TOP_K) -> list[BrainHit]:
        if not text or not text.strip():
            return []
        if not self._available or self._index is None:
            return []
        if k <= 0 or not self.entries:
            return []
        svc = self.embedding_service
        if svc is None or not getattr(svc, "available", False):
            return []
        embedding = svc.encode(text)
        if embedding is None:
            return []
        k_eff = min(k, len(self.entries))
        labels, distances = self._index.knn_query(
            embedding.reshape(1, -1).astype(np.float32), k=k_eff,
        )
        hits: list[BrainHit] = []
        for label, dist in zip(labels[0], distances[0]):
            # `space="ip"` returns 1 - inner_product as distance, so
            # similarity = 1 - distance.
            sim = float(1.0 - dist)
            hits.append(BrainHit(entry=self.entries[int(label)], similarity=sim))
        return hits


# ── Module-level convenience singleton ────────────────────────────────────────


_SINGLETON: Optional[BrainIndex] = None
_SINGLETON_LOCK = threading.Lock()


def get_default_index(force_rebuild: bool = False) -> BrainIndex:
    """Lazy-built process-wide BrainIndex. Rebuilt on demand.

    Thread-safe under concurrent route() calls — without the lock two
    requests landing on a fresh process would each trigger a full
    rebuild (MiniLM model load + brain corpus walk + HNSW build).
    """
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None or force_rebuild:
            idx = BrainIndex()
            idx.build()
            _SINGLETON = idx
        return _SINGLETON


def reset_default_index() -> None:
    """Drop the cached index. Next get_default_index() will rebuild."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None
