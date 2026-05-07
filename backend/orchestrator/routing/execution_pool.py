"""
F2 — ExecutionPool + QueueTracker.

Spec: docs/v2-debug-F1-F4.md §F2.

Centralized concurrency primitives for the agent-execution layer.
Three goals:

  1. Hard cap on total concurrent agent invocations
     (`MAHORAGA_MAX_CONCURRENT`, default 3) so a burst of MCP
     `run_task` calls can't OOM the box.

  2. Per-resource-group semaphores (delegated to
     `routing.resource_groups`) so local Ollama serializes at
     concurrency 1 while cloud APIs parallelize freely.

  3. Live `queue_depth_norm` exposed back to `TaskContext` so the
     bandit's 9-dim input includes contention state. Feature 9 was
     reserved/zero before F2; with the pool in place it becomes
     `min(1.0, depth / max_concurrent)` — the bandit can finally
     learn "when the queue is full, prefer fast agents."

The pool is a process-wide singleton: route() reads
`queue_depth_norm` for context construction; the execution layer
acquires/releases semaphores around adapter `.execute()` calls.

Distinct from `WaveExecutor` (in `service/wave_executor.py`):

  - WaveExecutor schedules a static *batch* under dependency + file
    constraints. It runs N waves of asyncio.gather.
  - ExecutionPool is per-call-site concurrency control. Single
    `run_task` invocations + each task inside a wave both go
    through the pool. Provides the global cap that batches and
    one-off requests share.

Env config:
  MAHORAGA_MAX_CONCURRENT=3        # global cap; default 3 for 16 GB
  MAHORAGA_TASK_TIMEOUT=120        # per-task hard deadline in seconds
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from ..resource_groups import get_group_concurrency, get_resource_group

_log = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 3
DEFAULT_TASK_TIMEOUT_S = 120


def resolve_max_concurrent() -> int:
    raw = os.environ.get("MAHORAGA_MAX_CONCURRENT", "").strip()
    if not raw:
        return DEFAULT_MAX_CONCURRENT
    try:
        v = int(raw)
        if 1 <= v <= 32:
            return v
    except ValueError:
        pass
    return DEFAULT_MAX_CONCURRENT


def resolve_task_timeout() -> float:
    raw = os.environ.get("MAHORAGA_TASK_TIMEOUT", "").strip()
    if not raw:
        return float(DEFAULT_TASK_TIMEOUT_S)
    try:
        v = float(raw)
        if v > 0:
            return v
    except ValueError:
        pass
    return float(DEFAULT_TASK_TIMEOUT_S)


# ── QueueTracker ──────────────────────────────────────────────────────────────


class QueueTracker:
    """Lock-protected counter for currently-active agent invocations.

    `depth` reflects "how many agents are mid-flight right now." Used
    to compute `queue_depth_norm` for the bandit's context vector.
    Atomic acquire/release pattern; safe under concurrent
    asyncio.gather.
    """

    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        async with self._lock:
            self._active += 1
            return self._active

    async def release(self) -> int:
        async with self._lock:
            self._active = max(0, self._active - 1)
            return self._active

    @property
    def depth(self) -> int:
        return self._active

    @property
    def depth_norm(self) -> float:
        """`min(1.0, depth / max_concurrent)`. Always in [0, 1]; bandit
        sees full queue (1.0) as the saturation signal."""
        return min(1.0, self._active / self.max_concurrent)


# ── ExecutionPool ─────────────────────────────────────────────────────────────


@dataclass
class PoolStats:
    """Read-only snapshot of pool state for telemetry."""
    max_concurrent: int
    depth: int
    depth_norm: float
    group_capacities: dict[str, int]


class ExecutionPool:
    """Process-wide concurrency manager.

    Use as an async context manager around adapter execution:

        async with pool.acquire("ollama"):
            result = await adapter.execute(task)

    `acquire(agent_name)` resolves the agent's resource group and
    locks both the global semaphore AND the per-group semaphore. On
    release, both are freed.

    Group semaphores are created lazily and cached. They respect the
    `max_concurrent` configured per group in
    `routing.resource_groups.RESOURCE_GROUPS`.
    """

    def __init__(self, max_concurrent: Optional[int] = None) -> None:
        self.max_concurrent = max_concurrent or resolve_max_concurrent()
        self._global = asyncio.Semaphore(self.max_concurrent)
        self._group_sems: dict[str, asyncio.Semaphore] = {}
        self.tracker = QueueTracker(max_concurrent=self.max_concurrent)

    def _group_semaphore(self, group: str) -> asyncio.Semaphore:
        sem = self._group_sems.get(group)
        if sem is None:
            sem = asyncio.Semaphore(get_group_concurrency(group))
            self._group_sems[group] = sem
        return sem

    @asynccontextmanager
    async def acquire(self, agent_name: str):
        """Lock global + group semaphores for the lifetime of the block.

        Order matters: global first, then group. Releasing in reverse
        on context exit avoids deadlocks under concurrent acquire
        from multiple agents in the same group.
        """
        group = get_resource_group(agent_name)
        group_sem = self._group_semaphore(group)
        async with self._global:
            async with group_sem:
                await self.tracker.acquire()
                try:
                    yield
                finally:
                    await self.tracker.release()

    @property
    def depth(self) -> int:
        return self.tracker.depth

    @property
    def queue_depth_norm(self) -> float:
        return self.tracker.depth_norm

    def stats(self) -> PoolStats:
        return PoolStats(
            max_concurrent=self.max_concurrent,
            depth=self.depth,
            depth_norm=self.queue_depth_norm,
            group_capacities={
                g: get_group_concurrency(g) for g in self._group_sems
            },
        )


# ── Module-level singleton ────────────────────────────────────────────────────


_DEFAULT_POOL: Optional[ExecutionPool] = None


def get_default_pool() -> ExecutionPool:
    """Lazy-built process-wide pool. Honors MAHORAGA_MAX_CONCURRENT
    on first construction."""
    global _DEFAULT_POOL
    if _DEFAULT_POOL is None:
        _DEFAULT_POOL = ExecutionPool()
    return _DEFAULT_POOL


def reset_default_pool() -> None:
    """Drop the cached pool. Mainly for tests; in production the pool
    lives for the duration of the process."""
    global _DEFAULT_POOL
    _DEFAULT_POOL = None


# ── Timeout wrapper ───────────────────────────────────────────────────────────


async def execute_with_timeout(coro, timeout_s: Optional[float] = None):
    """Run `coro` with a hard timeout, returning a (result, error) pair.

    Spec §F2: a hung agent task should not block other tasks. The
    timeout converts an indefinite hang into a clean failure. Caller
    handles the error case (assigns reward 0.0 on timeout).

    Returns `(result, None)` on success, `(None, "timeout")` on
    deadline, `(None, str(exc))` on other exceptions.
    """
    timeout = timeout_s if timeout_s is not None else resolve_task_timeout()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return result, None
    except asyncio.TimeoutError:
        _log.warning("execute_with_timeout: deadline %.1fs exceeded", timeout)
        return None, "timeout"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
