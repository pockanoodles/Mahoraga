import pytest
import aiosqlite
from backend.orchestrator.store.rankings_store import RankingsStore


@pytest.fixture
async def store():
    async with aiosqlite.connect(":memory:") as conn:
        s = RankingsStore(conn)
        await s.migrate()
        yield s


@pytest.mark.asyncio
async def test_upsert_and_get_benchmark_run(store):
    await store.upsert_benchmark_run(
        agent="ollama:general",
        bucket="code",
        difficulty="simple",
        avg_latency_ms=1200.0,
        median_latency_ms=1100.0,
        p90_latency_ms=1800.0,
        win_rate=0.75,
        reward_mean=0.72,
        sample_count=20,
        source="harness",
    )
    rows = await store.get_benchmark_runs(agent="ollama:general")
    assert len(rows) >= 1
    assert rows[0]["win_rate"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_replace_scope_rankings(store):
    rankings = [
        {"agent": "ollama:general", "rank": 1, "win_rate": 0.8, "ci_low": 0.65, "ci_high": 0.90,
         "avg_latency_ms": 1200.0, "avg_reward": 0.75, "sample_count": 30},
        {"agent": "claude:sonnet", "rank": 2, "win_rate": 0.7, "ci_low": 0.55, "ci_high": 0.82,
         "avg_latency_ms": 2800.0, "avg_reward": 0.78, "sample_count": 25},
    ]
    await store.replace_scope_rankings("overall", "all", rankings)
    rows = await store.get_rankings(scope_type="overall", scope_value="all")
    assert len(rows) == 2
    assert rows[0]["agent"] == "ollama:general"
    assert rows[0]["rank"] == 1


@pytest.mark.asyncio
async def test_get_rankings_filters(store):
    rankings = [
        {"agent": "aider:default", "rank": 1, "win_rate": 0.85, "ci_low": 0.7, "ci_high": 0.94,
         "avg_latency_ms": 4000.0, "avg_reward": 0.80, "sample_count": 15},
    ]
    await store.replace_scope_rankings("bucket", "code", rankings)
    rows = await store.get_rankings(scope_type="bucket", scope_value="code")
    assert len(rows) == 1
    assert rows[0]["agent"] == "aider:default"
