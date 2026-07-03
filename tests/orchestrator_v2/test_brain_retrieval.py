"""Tests for A4 — brain retrieval (routing/brain_retrieval.py)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.orchestrator.routing import brain_retrieval as br
from backend.orchestrator.routing.brain_retrieval import (
    BrainEntry,
    BrainHit,
    BrainIndex,
    SEMANTIC_DIM,
    resolve_brain_dir,
    resolve_enabled,
    resolve_top_k,
)


# ── env hygiene ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MAHORAGA_BRAIN_INTEGRATION_ENABLED",
        "MAHORAGA_BRAIN_DIR",
        "MAHORAGA_BRAIN_TOP_K",
    ):
        monkeypatch.delenv(k, raising=False)
    br.reset_default_index()
    yield
    br.reset_default_index()


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_default_disabled():
    assert resolve_enabled() is False


def test_enabled_env(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BRAIN_INTEGRATION_ENABLED", "true")
    assert resolve_enabled() is True


def test_top_k_env(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BRAIN_TOP_K", "7")
    assert resolve_top_k() == 7


def test_top_k_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_BRAIN_TOP_K", "garbage")
    assert resolve_top_k() == 3


def test_brain_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAHORAGA_BRAIN_DIR", str(tmp_path))
    assert resolve_brain_dir() == tmp_path


# ── BrainIndex with stub embedder ─────────────────────────────────────────────


class _StubEmbedder:
    """Deterministic stub. Encodes text → 384-dim vector based on first chars."""

    def __init__(self):
        self.available = True
        self.calls = 0

    def _vec_for(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text[:32])) % (2**32))
        v = rng.normal(size=SEMANTIC_DIM).astype(np.float32)
        v /= np.linalg.norm(v) + 1e-9
        return v

    def encode(self, text: str):
        self.calls += 1
        return self._vec_for(text)

    def encode_batch(self, texts):
        self.calls += len(texts)
        return [self._vec_for(t) for t in texts]


def _write_md(path: Path, title: str, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def test_build_empty_dir_returns_zero(tmp_path: Path):
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    n = idx.build()
    assert n == 0
    assert idx.available is False


def test_build_missing_dir_returns_zero(tmp_path: Path):
    idx = BrainIndex(
        brain_dir=tmp_path / "does_not_exist",
        embedding_service=_StubEmbedder(),
    )
    assert idx.build() == 0
    assert idx.available is False


def test_build_with_entries(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "2026-01-01-foo.md", "First decision", "context here")
    _write_md(tmp_path / "journal" / "2026-01-02-bar.md", "Journal entry", "did stuff")
    _write_md(tmp_path / "concepts" / "auth.md", "Auth model")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    n = idx.build()
    assert n == 3
    assert idx.available is True
    assert idx.size == 3


def test_entry_kind_classification(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "a.md", "A")
    _write_md(tmp_path / "journal" / "b.md", "B")
    _write_md(tmp_path / "concepts" / "c.md", "C")
    _write_md(tmp_path / "state" / "d.md", "D")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    kinds = {e.kind for e in idx.entries}
    assert kinds == {"decisions", "journal", "concepts", "state"}


def test_entry_timestamp_extracted_from_filename(tmp_path: Path):
    _write_md(tmp_path / "journal" / "2026-04-22-foo.md", "Foo")
    _write_md(tmp_path / "journal" / "no-date.md", "No date")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    by_title = {e.title: e for e in idx.entries}
    assert by_title["Foo"].timestamp == "2026-04-22"
    assert by_title["No date"].timestamp is None


def test_entry_title_extracted_from_h1(tmp_path: Path):
    p = tmp_path / "decisions" / "raw.md"
    p.parent.mkdir(parents=True)
    p.write_text("# The Real Title\n\nbody", encoding="utf-8")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    assert idx.entries[0].title == "The Real Title"


def test_entry_title_falls_back_to_stem(tmp_path: Path):
    p = tmp_path / "decisions" / "no_h1.md"
    p.parent.mkdir(parents=True)
    p.write_text("just some body without an h1", encoding="utf-8")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    assert idx.entries[0].title == "no_h1"


def test_query_returns_topk(tmp_path: Path):
    for i in range(8):
        _write_md(tmp_path / "concepts" / f"e{i}.md", f"Entry {i}", f"text {i}")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    hits = idx.query("anything", k=3)
    assert len(hits) == 3
    assert all(isinstance(h, BrainHit) for h in hits)
    assert all(-1.001 <= h.similarity <= 1.001 for h in hits)


def test_query_returns_empty_when_index_unbuilt(tmp_path: Path):
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    assert idx.query("anything") == []


def test_query_returns_empty_for_empty_text(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "a.md", "A")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    assert idx.query("") == []
    assert idx.query("   ") == []


def test_query_clamps_k_to_size(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "a.md", "A")
    _write_md(tmp_path / "journal" / "b.md", "B")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    idx.build()
    hits = idx.query("anything", k=20)
    assert len(hits) == 2


def test_build_with_unavailable_embedder(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "a.md", "A")
    bad_embedder = MagicMock()
    bad_embedder.available = False
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=bad_embedder)
    assert idx.build() == 0
    assert idx.available is False


def test_build_with_partial_embed_failure(tmp_path: Path):
    _write_md(tmp_path / "decisions" / "ok.md", "OK")
    _write_md(tmp_path / "decisions" / "bad.md", "BAD")

    class _PartialFailEmbedder:
        available = True
        def encode(self, text):
            return np.ones(SEMANTIC_DIM, dtype=np.float32) / np.sqrt(SEMANTIC_DIM)
        def encode_batch(self, texts):
            return [
                np.ones(SEMANTIC_DIM, dtype=np.float32) / np.sqrt(SEMANTIC_DIM)
                if "OK" in t else None
                for t in texts
            ]

    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_PartialFailEmbedder())
    n = idx.build()
    assert n == 1
    assert idx.available is True


def test_skipped_files_empty_body(tmp_path: Path):
    p = tmp_path / "journal" / "blank.md"
    p.parent.mkdir(parents=True)
    p.write_text("", encoding="utf-8")
    idx = BrainIndex(brain_dir=tmp_path, embedding_service=_StubEmbedder())
    n = idx.build()
    assert n == 0


def test_to_dict_roundtrips_and_truncates_body():
    e = BrainEntry(
        path="brain/journal/x.md",
        title="X",
        body="x" * 500,
        kind="journal",
        timestamp="2026-04-22",
    )
    d = e.to_dict()
    assert d["title"] == "X"
    assert d["timestamp"] == "2026-04-22"
    assert len(d["body"]) <= 250  # truncated


def test_brain_hit_to_dict():
    e = BrainEntry(path="p", title="t", body="b", kind="other", timestamp=None)
    h = BrainHit(entry=e, similarity=0.85)
    d = h.to_dict()
    assert d["similarity"] == 0.85
    assert d["entry"]["title"] == "t"


def test_singleton_caches(tmp_path: Path, monkeypatch):
    _write_md(tmp_path / "decisions" / "a.md", "A")
    monkeypatch.setenv("MAHORAGA_BRAIN_DIR", str(tmp_path))
    # Stub the EmbeddingService loaded inside build() to avoid model load.
    monkeypatch.setattr(
        "backend.orchestrator.routing.brain_retrieval.BrainIndex.build",
        lambda self: 1,  # type: ignore[arg-type]
    )
    a = br.get_default_index()
    b = br.get_default_index()
    assert a is b
    c = br.get_default_index(force_rebuild=True)
    assert c is not a
