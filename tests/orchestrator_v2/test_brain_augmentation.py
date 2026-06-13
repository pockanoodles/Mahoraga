"""Tests for A4 — brain hits → embedding augmentation.

Spec: docs/specs/v2-remaining-work.md §A4. Brain entries don't get mapped to
per-agent biases; they tag the task description with project context
keywords before semantic encoding. Episodic retrieval then lands in
project-specific clusters.
"""
from __future__ import annotations

from backend.orchestrator.routing.brain_retrieval import (
    BrainEntry,
    BrainHit,
    augment_for_embedding,
    summarise_brain_hits,
)


def _hit(title: str, body: str, sim: float = 0.5) -> BrainHit:
    return BrainHit(
        entry=BrainEntry(
            path=f"brain/{title}.md", title=title, body=body,
            kind="other", timestamp=None,
        ),
        similarity=sim,
    )


# ── summarise_brain_hits ──────────────────────────────────────────────────────


def test_empty_hits_yield_empty_string():
    assert summarise_brain_hits([]) == ""


def test_extracts_distinctive_tokens_from_top_hits():
    hits = [
        _hit("Postgres deployment", "We use PostgreSQL with pgBouncer for connection pooling.", 0.9),
        _hit("Locking strategy", "Team chose optimistic locking for sprint 12.", 0.7),
    ]
    summary = summarise_brain_hits(hits)
    # Important domain terms must show up.
    assert "PostgreSQL" in summary
    assert "pgBouncer" in summary
    assert "optimistic" in summary


def test_drops_stopwords_and_short_tokens():
    hits = [
        _hit("Auth", "We use the auth flow for the new system.", 0.9),
    ]
    summary = summarise_brain_hits(hits)
    assert "the" not in summary
    assert "we" not in summary.lower()
    # 3+ char tokens preserved.
    assert "auth" in summary or "Auth" in summary


def test_dedup_case_insensitive():
    hits = [
        _hit("PostgreSQL notes",
             "PostgreSQL is great. PostgreSQL has pgBouncer. POSTGRESQL again.",
             0.9),
    ]
    summary = summarise_brain_hits(hits)
    # Only one PostgreSQL token, preferring the first-seen casing.
    tokens = [t.strip() for t in summary.split(",")]
    pg_tokens = [t for t in tokens if t.lower() == "postgresql"]
    assert len(pg_tokens) == 1
    assert pg_tokens[0] == "PostgreSQL"


def test_top_k_respects_similarity():
    """Lower-similarity hits get ignored once top_k is reached."""
    hits = [
        _hit("first", "alpha alpha", 0.9),
        _hit("second", "beta beta", 0.8),
        _hit("third", "gamma gamma", 0.7),
        _hit("fourth", "delta delta", 0.1),  # below top_k
    ]
    summary = summarise_brain_hits(hits, top_k=3)
    assert "alpha" in summary
    assert "beta" in summary
    assert "gamma" in summary
    assert "delta" not in summary


def test_max_tokens_caps_output():
    body = " ".join(f"Word{i}" for i in range(200))
    hits = [_hit("noisy", body, 0.9)]
    summary = summarise_brain_hits(hits, max_tokens=10)
    tokens = [t for t in summary.split(",") if t.strip()]
    assert len(tokens) <= 10


def test_preserves_proper_noun_casing():
    hits = [_hit("infra", "We use MiniLM and HNSW with 384 dim.", 0.9)]
    summary = summarise_brain_hits(hits)
    assert "MiniLM" in summary
    assert "HNSW" in summary


# ── augment_for_embedding ─────────────────────────────────────────────────────


def test_augment_with_summary():
    out = augment_for_embedding(
        "Fix the race condition in the connection pool",
        "PostgreSQL, pgBouncer, optimistic",
    )
    assert out == "Fix the race condition in the connection pool [PostgreSQL, pgBouncer, optimistic]"


def test_augment_with_empty_summary_passthrough():
    """Cold-start (no brain) must produce identical text — otherwise
    pre-A4 episodes would be unreachable from post-A4 queries."""
    text = "Fix the race condition in the connection pool"
    assert augment_for_embedding(text, "") == text
    assert augment_for_embedding(text, "   ") == text


def test_augment_is_pure():
    """Same inputs → same outputs. No global state."""
    a1 = augment_for_embedding("hello", "x, y")
    a2 = augment_for_embedding("hello", "x, y")
    assert a1 == a2
