"""Tests for F2 — ExecutionPool + QueueTracker.

Spec: docs/specs/v2-debug-F1-F4.md §F2 acceptance criteria.

Most assertions cover concurrency invariants (semaphores actually serialize
overlapping work) — verified by collecting timestamped acquire/release
events and inspecting the resulting trajectories.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from backend.orchestrator.routing.execution_pool import (
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_TASK_TIMEOUT_S,
    ExecutionPool,
    QueueTracker,
    execute_with_timeout,
    get_default_pool,
    reset_default_pool,
    resolve_max_concurrent,
    resolve_task_timeout,
)


# ── env hygiene ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MAHORAGA_MAX_CONCURRENT", "MAHORAGA_TASK_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    reset_default_pool()
    yield
    reset_default_pool()


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_default_max_concurrent():
    assert resolve_max_concurrent() == DEFAULT_MAX_CONCURRENT


def test_env_max_concurrent_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_MAX_CONCURRENT", "8")
    assert resolve_max_concurrent() == 8


def test_env_max_concurrent_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_MAX_CONCURRENT", "not_a_number")
    assert resolve_max_concurrent() == DEFAULT_MAX_CONCURRENT


def test_env_max_concurrent_out_of_range_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_MAX_CONCURRENT", "99999")
    assert resolve_max_concurrent() == DEFAULT_MAX_CONCURRENT


def test_env_task_timeout_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_TASK_TIMEOUT", "30")
    assert resolve_task_timeout() == 30.0


def test_env_task_timeout_default():
    assert resolve_task_timeout() == DEFAULT_TASK_TIMEOUT_S


# ── QueueTracker ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_depth_tracks_active():
    """Acceptance criterion 6: depth goes up on acquire, down on release."""
    qt = QueueTracker(max_concurrent=4)
    assert qt.depth == 0
    await qt.acquire()
    assert qt.depth == 1
    await qt.acquire()
    assert qt.depth == 2
    await qt.release()
    assert qt.depth == 1
    await qt.release()
    assert qt.depth == 0


@pytest.mark.asyncio
async def test_queue_depth_norm_clamps_at_one():
    qt = QueueTracker(max_concurrent=2)
    assert qt.depth_norm == 0.0
    await qt.acquire()
    assert qt.depth_norm == 0.5
    await qt.acquire()
    assert qt.depth_norm == 1.0
    # Even if we somehow exceed (shouldn't happen with semaphores), cap at 1.
    await qt.acquire()
    assert qt.depth_norm == 1.0


@pytest.mark.asyncio
async def test_queue_release_floor_at_zero():
    """Releasing past zero should never produce negative depth."""
    qt = QueueTracker(max_concurrent=2)
    await qt.release()
    await qt.release()
    assert qt.depth == 0


# ── ExecutionPool ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pool_global_cap_serializes_overflow():
    """Acceptance criterion 1: with max_concurrent=2 and 5 cloud tasks, only
    2 run at once. Track concurrent depth — peak must equal 2."""
    pool = ExecutionPool(max_concurrent=2)
    peak = 0

    async def task(name: str):
        nonlocal peak
        async with pool.acquire("gemini-cli"):  # cloud agent, group cap=3
            peak = max(peak, pool.depth)
            await asyncio.sleep(0.05)

    await asyncio.gather(*[task(str(i)) for i in range(5)])
    assert peak == 2  # global cap, not group cap, is the bottleneck


@pytest.mark.asyncio
async def test_pool_local_semaphore_serialises():
    """Acceptance criterion 2: local_ollama group has max_concurrent=1.
    Two ollama tasks overlap-test should be sequential."""
    pool = ExecutionPool(max_concurrent=4)  # generous global cap
    peak_concurrent_ollama = 0
    active_ollama = 0
    lock = asyncio.Lock()

    async def task():
        nonlocal active_ollama, peak_concurrent_ollama
        async with pool.acquire("ollama"):
            async with lock:
                active_ollama += 1
                peak_concurrent_ollama = max(peak_concurrent_ollama, active_ollama)
            await asyncio.sleep(0.05)
            async with lock:
                active_ollama -= 1

    await asyncio.gather(task(), task(), task())
    assert peak_concurrent_ollama == 1  # local group serializes


@pytest.mark.asyncio
async def test_pool_cloud_parallelises():
    """Acceptance criterion 3: gemini-cli group has max_concurrent=3.
    Three concurrent gemini tasks all overlap."""
    pool = ExecutionPool(max_concurrent=4)
    peak = 0

    async def task():
        nonlocal peak
        async with pool.acquire("gemini-cli"):
            peak = max(peak, pool.depth)
            await asyncio.sleep(0.05)

    await asyncio.gather(task(), task(), task())
    assert peak == 3


@pytest.mark.asyncio
async def test_pool_mixed_local_cloud():
    """Acceptance criterion 4: 1 local + 2 cloud → local serial, cloud parallel.

    Verify by checking the timeline: at peak, total depth should be 3
    (1 ollama + 2 gemini), not 1 (which would mean local was blocking
    cloud somehow)."""
    pool = ExecutionPool(max_concurrent=8)
    peak = 0

    async def task(agent: str):
        nonlocal peak
        async with pool.acquire(agent):
            peak = max(peak, pool.depth)
            await asyncio.sleep(0.05)

    await asyncio.gather(
        task("ollama"),
        task("gemini-cli"),
        task("gemini-cli"),
    )
    assert peak == 3  # all three overlap because their groups don't conflict


@pytest.mark.asyncio
async def test_pool_release_on_exception():
    """Semaphores must release even when the wrapped block raises."""
    pool = ExecutionPool(max_concurrent=2)

    async def failing():
        async with pool.acquire("ollama"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await failing()
    assert pool.depth == 0  # released despite the exception


@pytest.mark.asyncio
async def test_pool_depth_norm_tracks():
    pool = ExecutionPool(max_concurrent=2)
    depths_seen: list[float] = []

    async def task():
        async with pool.acquire("gemini-cli"):
            depths_seen.append(pool.queue_depth_norm)
            await asyncio.sleep(0.05)

    await asyncio.gather(task(), task())
    # At peak both tasks see depth_norm == 1.0; first one to enter sees 0.5.
    assert max(depths_seen) == 1.0
    assert min(depths_seen) >= 0.5


def test_pool_stats_dict():
    pool = ExecutionPool(max_concurrent=4)
    stats = pool.stats()
    assert stats.max_concurrent == 4
    assert stats.depth == 0
    assert stats.depth_norm == 0.0


# ── singleton ────────────────────────────────────────────────────────────────


def test_default_pool_singleton():
    p1 = get_default_pool()
    p2 = get_default_pool()
    assert p1 is p2


def test_reset_clears_singleton():
    p1 = get_default_pool()
    reset_default_pool()
    p2 = get_default_pool()
    assert p1 is not p2


def test_default_pool_uses_env(monkeypatch):
    monkeypatch.setenv("MAHORAGA_MAX_CONCURRENT", "5")
    reset_default_pool()
    assert get_default_pool().max_concurrent == 5


# ── execute_with_timeout ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_returns_error_marker():
    """Acceptance criterion 5: a task that hangs >timeout returns a
    failure result; doesn't propagate the TimeoutError to the caller."""
    async def hangs():
        await asyncio.sleep(2.0)
        return "should never get here"

    result, err = await execute_with_timeout(hangs(), timeout_s=0.05)
    assert result is None
    assert err == "timeout"


@pytest.mark.asyncio
async def test_timeout_passes_success_result():
    async def quick():
        return "done"

    result, err = await execute_with_timeout(quick(), timeout_s=1.0)
    assert result == "done"
    assert err is None


@pytest.mark.asyncio
async def test_timeout_captures_other_exceptions():
    async def boom():
        raise ValueError("nope")

    result, err = await execute_with_timeout(boom(), timeout_s=1.0)
    assert result is None
    assert "nope" in err


# ── Integration: BanditRouter reads queue_depth from pool ─────────────────────


def test_bandit_router_reads_pool_queue_depth(tmp_path, monkeypatch):
    """When route() is called and the pool has active tasks, the
    bandit's TaskContext should see queue_depth_norm > 0 — the
    real-value wiring."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.decision_log import DecisionLogger

    pool = get_default_pool()
    # Manually pump the tracker as if a task is mid-flight.
    asyncio.run(pool.tracker.acquire())

    router = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "s.json",
    )

    class T:
        id = "qd1"
        title = "Refactor auth"
        goal = "Refactor auth"
    router.route(T(), available_agents=["ollama", "aider"])

    # The most recent decision row should show queue_depth_norm > 0
    # in its context_vector (feature 9).
    import json as _json
    row = router.logger._conn.execute(
        "SELECT context_vector FROM decisions WHERE task_id='qd1'"
    ).fetchone()
    ctx = _json.loads(row[0])
    # Context is 9-dim; feature 9 (index 8) is queue_depth_norm.
    assert ctx[8] > 0.0
    asyncio.run(pool.tracker.release())


def test_bandit_router_queue_depth_zero_when_pool_idle(tmp_path):
    """Idle pool → context feature 9 stays 0 (preserves pre-F2 behavior)."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.decision_log import DecisionLogger

    reset_default_pool()
    router = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "s.json",
    )

    class T:
        id = "qd0"
        title = "Idle pool task"
        goal = "Idle pool task"
    router.route(T(), available_agents=["ollama", "aider"])

    import json as _json
    row = router.logger._conn.execute(
        "SELECT context_vector FROM decisions WHERE task_id='qd0'"
    ).fetchone()
    ctx = _json.loads(row[0])
    assert ctx[8] == 0.0
