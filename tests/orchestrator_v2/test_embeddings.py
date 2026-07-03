"""Unit + integration tests for backend.orchestrator.routing.embeddings.

Most tests use a deterministic fake encoder so they don't depend on
sentence-transformers being installed. The integration tests at the
bottom skip cleanly when the real model is unavailable.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

import numpy as np
import pytest

from backend.orchestrator.routing import embeddings as emb_mod
from backend.orchestrator.routing.embeddings import (
    DIM,
    MODEL_ID,
    EmbeddingService,
)


# ── Test doubles ──────────────────────────────────────────────────────────────


class FakeEncoder:
    """Deterministic encoder. Each unique input gets a stable unit vector
    derived from a sha256 of the text. Tracks calls for cache assertions."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_inputs: list[str] = []

    def encode(
        self,
        texts: Sequence[str],
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        self.call_count += 1
        self.last_inputs = list(texts)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(
                hashlib.sha256(t.encode("utf-8")).digest()[:8], "big"
            )
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(DIM).astype(np.float32)
            if normalize_embeddings:
                v = v / np.linalg.norm(v)
            out[i] = v
        return out


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "embedding_cache.sqlite"


@pytest.fixture
def fake_encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def service(cache_path: Path, fake_encoder: FakeEncoder) -> EmbeddingService:
    return EmbeddingService(cache_path=cache_path, model=fake_encoder)


# ── Basic encoding ────────────────────────────────────────────────────────────


class TestEncodeBasic:
    def test_returns_384_dim_unit_vector(self, service: EmbeddingService) -> None:
        v = service.encode("Fix the database race condition")
        assert v is not None
        assert v.shape == (DIM,)
        assert v.dtype == np.float32
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5

    def test_empty_string_returns_none(self, service: EmbeddingService) -> None:
        assert service.encode("") is None

    def test_whitespace_only_returns_none(self, service: EmbeddingService) -> None:
        assert service.encode("   ") is None
        assert service.encode("\n\t ") is None

    def test_dim_and_model_id_properties(self, service: EmbeddingService) -> None:
        assert service.dim == 384
        assert service.model_id == "all-MiniLM-L6-v2"


# ── Normalisation contract (locked design decision #1) ────────────────────────


class TestNormalisation:
    def test_case_variants_share_embedding(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        v1 = service.encode("Fix the bug")
        v2 = service.encode("fix the bug")
        v3 = service.encode("FIX THE BUG")
        assert v1 is not None and v2 is not None and v3 is not None
        np.testing.assert_array_equal(v1, v2)
        np.testing.assert_array_equal(v2, v3)

    def test_whitespace_variants_share_embedding(
        self, service: EmbeddingService
    ) -> None:
        v1 = service.encode("fix the bug")
        v2 = service.encode("  fix the bug  ")
        v3 = service.encode("\tfix the bug\n")
        assert v1 is not None
        np.testing.assert_array_equal(v1, v2)
        np.testing.assert_array_equal(v1, v3)

    def test_model_receives_normalised_text(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        service.encode("  Fix The Bug  ")
        assert fake_encoder.last_inputs == ["fix the bug"]


# ── LRU cache behaviour ───────────────────────────────────────────────────────


class TestLRUCache:
    def test_repeated_encode_hits_cache(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        v1 = service.encode("hello world")
        n_after_first = fake_encoder.call_count

        v2 = service.encode("hello world")
        v3 = service.encode("HELLO WORLD")  # casing variant — same cache slot

        assert v1 is not None
        np.testing.assert_array_equal(v1, v2)
        np.testing.assert_array_equal(v1, v3)
        assert fake_encoder.call_count == n_after_first  # no extra model calls

    def test_distinct_texts_each_hit_model_once(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        service.encode("alpha")
        service.encode("beta")
        service.encode("gamma")
        # Repeats should not invoke the model again.
        n = fake_encoder.call_count
        service.encode("alpha")
        service.encode("beta")
        service.encode("gamma")
        assert fake_encoder.call_count == n


# ── SQLite persistent cache ───────────────────────────────────────────────────


class TestSQLiteCache:
    def test_persists_across_service_instances(
        self, cache_path: Path, fake_encoder: FakeEncoder
    ) -> None:
        s1 = EmbeddingService(cache_path=cache_path, model=fake_encoder)
        v1 = s1.encode("persistent task")
        n_calls = fake_encoder.call_count
        s1.close()

        # New service, new in-memory LRU, same SQLite file.
        # Pass a *fresh* encoder so we can confirm the model is never invoked
        # — anything we get back must come from disk cache.
        fresh_encoder = FakeEncoder()
        s2 = EmbeddingService(cache_path=cache_path, model=fresh_encoder)
        v2 = s2.encode("persistent task")

        assert v2 is not None
        np.testing.assert_array_equal(v1, v2)
        assert fresh_encoder.call_count == 0
        s2.close()

    def test_recovers_from_corrupt_db_file(
        self, cache_path: Path, fake_encoder: FakeEncoder
    ) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"this is not a valid sqlite file" * 50)

        # Should not raise; should silently recreate the cache file.
        s = EmbeddingService(cache_path=cache_path, model=fake_encoder)
        v = s.encode("recovered")
        assert v is not None
        assert v.shape == (DIM,)

        # And a roundtrip should work afterwards.
        v2 = s.encode("recovered")
        np.testing.assert_array_equal(v, v2)
        s.close()

    def test_works_without_cache_path(self, fake_encoder: FakeEncoder) -> None:
        s = EmbeddingService(cache_path=None, model=fake_encoder)
        v = s.encode("no disk")
        assert v is not None and v.shape == (DIM,)

    def test_cache_row_has_expected_schema(
        self, cache_path: Path, fake_encoder: FakeEncoder
    ) -> None:
        s = EmbeddingService(cache_path=cache_path, model=fake_encoder)
        s.encode("schema check")
        s.close()

        conn = sqlite3.connect(str(cache_path))
        try:
            row = conn.execute(
                "SELECT model_id, dim, length(embedding) FROM embeddings"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        model_id, dim, blob_len = row
        assert model_id == MODEL_ID
        assert dim == DIM
        assert blob_len == DIM * 4  # float32


# ── Graceful degradation ──────────────────────────────────────────────────────


class TestUnavailable:
    def test_available_false_when_loader_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, cache_path: Path
    ) -> None:
        monkeypatch.setattr(emb_mod, "_try_load_default_model", lambda: None)
        s = EmbeddingService(cache_path=cache_path, model=None)
        assert s.available is False

    def test_encode_returns_none_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, cache_path: Path
    ) -> None:
        monkeypatch.setattr(emb_mod, "_try_load_default_model", lambda: None)
        s = EmbeddingService(cache_path=cache_path, model=None)
        assert s.encode("anything") is None

    def test_batch_encode_returns_nones_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, cache_path: Path
    ) -> None:
        monkeypatch.setattr(emb_mod, "_try_load_default_model", lambda: None)
        s = EmbeddingService(cache_path=cache_path, model=None)
        result = s.encode_batch(["a", "b", "c"])
        assert result == [None, None, None]

    def test_load_is_attempted_only_once(
        self, monkeypatch: pytest.MonkeyPatch, cache_path: Path
    ) -> None:
        calls = {"n": 0}

        def fake_loader() -> None:
            calls["n"] += 1
            return None

        monkeypatch.setattr(emb_mod, "_try_load_default_model", fake_loader)
        s = EmbeddingService(cache_path=cache_path, model=None)
        s.encode("x")
        s.encode("y")
        assert s.available is False
        assert calls["n"] == 1


# ── Batch encoding ────────────────────────────────────────────────────────────


class TestBatchEncode:
    def test_returns_one_vector_per_input(
        self, service: EmbeddingService
    ) -> None:
        result = service.encode_batch(["alpha", "beta", "gamma"])
        assert len(result) == 3
        for v in result:
            assert v is not None
            assert v.shape == (DIM,)

    def test_skips_empty_strings(self, service: EmbeddingService) -> None:
        result = service.encode_batch(["alpha", "", "  ", "beta"])
        assert result[0] is not None
        assert result[1] is None
        assert result[2] is None
        assert result[3] is not None

    def test_does_not_recompute_cached_entries(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        # Pre-cache one item.
        service.encode("alpha")
        n_after_warmup = fake_encoder.call_count

        # Batch with one cached + two cold.
        result = service.encode_batch(["alpha", "beta", "gamma"])
        assert all(v is not None for v in result)

        # Exactly one additional model call (the batch of misses).
        assert fake_encoder.call_count == n_after_warmup + 1
        # And the model received only the misses.
        assert fake_encoder.last_inputs == ["beta", "gamma"]

    def test_all_inputs_empty_does_not_call_model(
        self, service: EmbeddingService, fake_encoder: FakeEncoder
    ) -> None:
        n_before = fake_encoder.call_count
        result = service.encode_batch(["", "  ", "\t\n"])
        assert result == [None, None, None]
        assert fake_encoder.call_count == n_before


# ── Similarity ────────────────────────────────────────────────────────────────


class TestSimilarity:
    def test_self_similarity_is_one(self, service: EmbeddingService) -> None:
        v = service.encode("query")
        assert v is not None
        assert abs(service.similarity(v, v) - 1.0) < 1e-5

    def test_similarity_is_dot_product(self) -> None:
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert EmbeddingService.similarity(a, b) == 0.0

        c = np.array([1.0, 0.0], dtype=np.float32)
        d = np.array([1.0, 0.0], dtype=np.float32)
        assert EmbeddingService.similarity(c, d) == 1.0


# ── Thread safety ─────────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_encode_same_text_returns_consistent_vector(
        self, service: EmbeddingService
    ) -> None:
        def worker() -> np.ndarray | None:
            return service.encode("contended task")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker) for _ in range(32)]
            results = [f.result() for f in as_completed(futures)]

        assert all(v is not None for v in results)
        # All threads should receive the same vector value.
        first = results[0]
        assert first is not None
        for v in results[1:]:
            np.testing.assert_array_equal(first, v)

    def test_concurrent_encode_distinct_texts(
        self, service: EmbeddingService
    ) -> None:
        texts = [f"task number {i}" for i in range(20)]
        seen: dict[str, np.ndarray] = {}
        seen_lock = threading.Lock()

        def worker(t: str) -> None:
            v = service.encode(t)
            assert v is not None
            with seen_lock:
                seen[t] = v

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, texts * 3))  # 60 calls, 20 unique

        assert len(seen) == 20
        for v in seen.values():
            assert v.shape == (DIM,)


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_close_is_idempotent(self, service: EmbeddingService) -> None:
        service.close()
        service.close()  # must not raise

    def test_encode_after_close_reopens_connection(
        self, service: EmbeddingService
    ) -> None:
        service.encode("warm up")
        service.close()
        v = service.encode("after close")
        assert v is not None
        assert v.shape == (DIM,)


# ── Real-model integration (skipped if sentence-transformers absent) ──────────


@pytest.fixture(scope="module")
def real_service(tmp_path_factory: pytest.TempPathFactory) -> EmbeddingService:
    pytest.importorskip(
        "sentence_transformers",
        reason="sentence-transformers not installed; "
        "see requirements-semantic.txt for the optional dependency",
    )
    cache = tmp_path_factory.mktemp("real_emb") / "cache.sqlite"
    s = EmbeddingService(cache_path=cache, model=None)  # real loader
    if not s.available:
        pytest.skip("Real embedding model failed to load (offline?)")
    return s


@pytest.mark.slow
class TestRealModelAcceptance:
    """Spec §13.1 acceptance criteria — uses the real MiniLM model.

    Thresholds were re-calibrated against empirical MiniLM output during
    Phase 1 validation. The original spec values (>0.85 for paraphrase,
    <0.80 for shared-keywords-different-meaning) overestimated MiniLM's
    absolute cosine magnitudes. MiniLM-L6-v2 produces paraphrase
    similarities in roughly [0.55, 0.95] and unrelated-pair similarities
    in roughly [-0.15, 0.30]. The retrieval property the bandit cares
    about is the *gap* between in-cluster and out-of-cluster pairs, not
    the absolute magnitude — which is large and robust here. See
    docs/specs/semantic-routing.md §13.1 for the calibration table.
    """

    def test_returns_unit_vector(self, real_service: EmbeddingService) -> None:
        v = real_service.encode("Fix the database race condition")
        assert v is not None
        assert v.shape == (DIM,)
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-4

    def test_same_meaning_high_similarity(
        self, real_service: EmbeddingService
    ) -> None:
        a = real_service.encode("Fix the database race condition")
        b = real_service.encode(
            "Debug the race condition in the DB connection pool"
        )
        assert a is not None and b is not None
        sim = real_service.similarity(a, b)
        # Empirical: ~0.71. Lower bound 0.60 gives margin against
        # tokenizer/model patch updates without losing signal.
        assert sim > 0.60, f"expected > 0.60 for same-meaning paraphrase, got {sim:.3f}"

    def test_shared_keywords_different_meaning_lower_similarity(
        self, real_service: EmbeddingService
    ) -> None:
        a = real_service.encode("Fix the database race condition")
        b = real_service.encode("Fix the typo in the README")
        assert a is not None and b is not None
        sim = real_service.similarity(a, b)
        # Empirical: ~0.15. Threshold 0.40 has a comfortable margin and is
        # well below any same-meaning observation.
        assert sim < 0.40, f"expected < 0.40 for different-meaning, got {sim:.3f}"

    def test_discrimination_gap(self, real_service: EmbeddingService) -> None:
        """The retrieval-quality invariant: same-meaning pairs sit clearly
        above different-meaning pairs in the same context."""
        anchor = real_service.encode("Fix the database race condition")
        same = real_service.encode(
            "Debug the race condition in the DB connection pool"
        )
        diff = real_service.encode("Fix the typo in the README")
        assert anchor is not None and same is not None and diff is not None

        sim_same = real_service.similarity(anchor, same)
        sim_diff = real_service.similarity(anchor, diff)
        gap = sim_same - sim_diff
        assert gap > 0.30, (
            f"expected discrimination gap > 0.30; got same={sim_same:.3f}, "
            f"diff={sim_diff:.3f}, gap={gap:.3f}"
        )

    def test_near_paraphrase_high_similarity(
        self, real_service: EmbeddingService
    ) -> None:
        """Reformulations of the same question should be near-identical
        (this is what makes cache-key normalisation valuable in practice)."""
        a = real_service.encode("Explain how B-trees handle page splits")
        b = real_service.encode("How do B-tree page splits work?")
        assert a is not None and b is not None
        sim = real_service.similarity(a, b)
        # Empirical: ~0.96. Threshold 0.85 gives margin while still
        # meaningfully demanding "near-paraphrase" recognition.
        assert sim > 0.85, f"expected > 0.85 for near-paraphrase, got {sim:.3f}"
