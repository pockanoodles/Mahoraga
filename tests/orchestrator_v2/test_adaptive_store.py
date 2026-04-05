import time
import pytest
import aiosqlite

from backend.orchestrator.adaptive.models import (
    AdaptationCategory,
    UserAdaptation,
    UserProfile,
)
from backend.orchestrator.adaptive.store import AdaptiveStore


@pytest.fixture
async def adaptive_store():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = AdaptiveStore(conn)
    await store.migrate()
    yield store
    await conn.close()


async def test_save_and_get_profile(adaptive_store):
    profile = UserProfile.new("user-123")
    await adaptive_store.save_profile(profile)

    fetched = await adaptive_store.get_profile("user-123")
    assert fetched is not None
    assert fetched.user_id == "user-123"
    assert fetched.created_at == profile.created_at
    assert fetched.updated_at == profile.updated_at


async def test_get_missing_profile_returns_none(adaptive_store):
    result = await adaptive_store.get_profile("nonexistent")
    assert result is None


async def test_save_and_list_adaptations(adaptive_store):
    a1 = UserAdaptation.new("user-1", AdaptationCategory.preference, "theme", "dark", confidence=0.9)
    a2 = UserAdaptation.new("user-1", AdaptationCategory.style, "verbosity", "concise", confidence=0.7)
    a3 = UserAdaptation.new("user-1", AdaptationCategory.correction, "tone", "formal", confidence=0.85)

    for a in [a1, a2, a3]:
        await adaptive_store.save_adaptation(a)

    all_adaptations = await adaptive_store.list_adaptations("user-1")
    assert len(all_adaptations) == 3
    # ordered by confidence DESC
    assert all_adaptations[0].confidence >= all_adaptations[1].confidence
    assert all_adaptations[1].confidence >= all_adaptations[2].confidence


async def test_list_adaptations_filtered_by_category(adaptive_store):
    a1 = UserAdaptation.new("user-2", AdaptationCategory.preference, "lang", "python")
    a2 = UserAdaptation.new("user-2", AdaptationCategory.style, "length", "short")
    await adaptive_store.save_adaptation(a1)
    await adaptive_store.save_adaptation(a2)

    prefs = await adaptive_store.list_adaptations("user-2", category=AdaptationCategory.preference)
    assert len(prefs) == 1
    assert prefs[0].key == "lang"


async def test_update_confidence(adaptive_store):
    a = UserAdaptation.new("user-3", AdaptationCategory.pattern, "greeting", "hey")
    await adaptive_store.save_adaptation(a)

    await adaptive_store.reinforce(a.id, 0.95)

    adaptations = await adaptive_store.list_adaptations("user-3")
    assert len(adaptations) == 1
    assert abs(adaptations[0].confidence - 0.95) < 1e-6


async def test_decay_stale_adaptations(adaptive_store):
    now = time.time()
    stale_time = now - 31 * 86400  # 31 days ago

    a = UserAdaptation.new("user-4", AdaptationCategory.preference, "color", "blue", confidence=0.8)
    # Override last_reinforced to simulate stale
    a.last_reinforced = stale_time
    await adaptive_store.save_adaptation(a)

    # Also add a fresh adaptation that should NOT be decayed
    fresh = UserAdaptation.new("user-4", AdaptationCategory.style, "format", "markdown", confidence=0.8)
    await adaptive_store.save_adaptation(fresh)

    count = await adaptive_store.decay_stale("user-4", days=30, factor=0.5)
    assert count == 1

    adaptations = await adaptive_store.list_adaptations("user-4")
    stale_adapt = next(a for a in adaptations if a.key == "color")
    assert abs(stale_adapt.confidence - 0.4) < 1e-6

    fresh_adapt = next(a for a in adaptations if a.key == "format")
    assert abs(fresh_adapt.confidence - 0.8) < 1e-6


async def test_decay_returns_zero_when_nothing_stale(adaptive_store):
    a = UserAdaptation.new("user-5", AdaptationCategory.preference, "mode", "auto")
    await adaptive_store.save_adaptation(a)

    count = await adaptive_store.decay_stale("user-5", days=30, factor=0.5)
    assert count == 0
