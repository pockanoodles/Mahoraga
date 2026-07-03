"""Tests for F2.2 — real double-run parallel execution in /api/task.

Validates that when double_run_alt fires:
 - both agents execute (two tasks created and run)
 - router.observe is called for both agents
 - the higher-quality output wins (returned in response)
 - primary wins when alt fails
"""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from backend.orchestrator.domain.models import Task, TaskStatus
from backend.orchestrator.routing.escalation_strategies import EscalationStrategy
from backend.orchestrator.routing.execution_pool import ExecutionPool
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.store.base import Store
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pool_patches():
    """Return patches giving each test a fresh ExecutionPool bound to the
    running event loop so asyncio semaphores don't bleed between tests.

    The imports are local inside the endpoint function body, so we patch
    the source module — the function re-imports on every call."""
    fresh = ExecutionPool(max_concurrent=8)
    return (
        patch(
            "backend.orchestrator.routing.execution_pool.get_default_pool",
            return_value=fresh,
        ),
        patch(
            "backend.orchestrator.routing.execution_pool.resolve_task_timeout",
            return_value=30.0,
        ),
    )


class _OkWorker(WorkerAdapter):
    def __init__(self, worker_id: str, output: str = "ok output"):
        self._id = worker_id
        self._output = output

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return ["general"]

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": self._output, "output": self._output})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self._id, healthy=True)


def _make_verifier() -> Verifier:
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )
    return v


def _mock_router(
    route_agent: str = "ollama",
    escalation_strategy: str = EscalationStrategy.NONE.value,
    would_be_agent: str | None = None,
):
    from backend.orchestrator.routing.reward import RewardCalculator

    router = MagicMock()
    router.route = MagicMock(return_value=route_agent)
    router.observe = MagicMock()
    router.log_override = MagicMock(return_value=1)
    router.reward_calc = RewardCalculator()
    router.strategy = MagicMock()
    router.strategy.name = "linucb_per_bucket"
    router.strategy.get_scores = MagicMock(return_value={})
    router.logger = MagicMock()
    router.logger.count = MagicMock(return_value=0)

    composed = MagicMock()
    composed.escalation_strategy = escalation_strategy
    composed.bandit_pick = route_agent
    composed.would_be_agent = would_be_agent
    router._last_composed = composed
    return router


def _adapter_registry(primary: str, alt: str | None = None):
    ar = MagicMock()

    def _get(name):
        if name == primary:
            return MagicMock(worker_id=primary)
        if alt and name == alt:
            return MagicMock(worker_id=alt)
        return None

    ar.get = _get
    ar.all = MagicMock(return_value=[])
    return ar


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_double_run_calls_observe_for_both_agents(store):
    """When double_run_alt fires, both outcomes are fed to router.observe."""
    primary, alt = "ollama", "aider"
    registry = WorkerRegistry()
    registry.register(_OkWorker("ollama", output="primary result"))
    registry.register(_OkWorker("aider", output="alt result"))
    router = _mock_router(
        route_agent=primary,
        escalation_strategy=EscalationStrategy.DOUBLE_RUN.value,
        would_be_agent=alt,
    )

    async def _quality(prompt, output, bucket):
        return (0.7, {"overall": 0.7})

    pool_p1, pool_p2 = _pool_patches()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry",
                  return_value=_adapter_registry(primary, alt)),
            patch("backend.orchestrator.routing.quality.score_quality_detailed",
                  side_effect=_quality),
            pool_p1,
            pool_p2,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/task", json={"prompt": "write hello world"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["routing"]["double_run_alt"] == alt
        # Both agents observed
        assert router.observe.call_count == 2
        agents_observed = {c.args[1].agent_name for c in router.observe.call_args_list}
        assert primary in agents_observed
        assert alt in agents_observed
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_double_run_winner_is_higher_quality(store):
    """Alt agent wins when its quality score exceeds the primary's."""
    primary, alt = "ollama", "aider"
    registry = WorkerRegistry()
    registry.register(_OkWorker("ollama", output="mediocre answer"))
    registry.register(_OkWorker("aider", output="great answer"))
    router = _mock_router(
        route_agent=primary,
        escalation_strategy=EscalationStrategy.DOUBLE_RUN.value,
        would_be_agent=alt,
    )

    async def _quality_by_output(prompt, output, bucket):
        return (0.95, {"overall": 0.95}) if "great" in output else (0.40, {"overall": 0.40})

    pool_p1, pool_p2 = _pool_patches()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry",
                  return_value=_adapter_registry(primary, alt)),
            patch("backend.orchestrator.routing.quality.score_quality_detailed",
                  side_effect=_quality_by_output),
            pool_p1,
            pool_p2,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/task", json={"prompt": "explain quantum computing"}
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["routing"]["double_run_winner"] == alt
        assert "great answer" in data["output"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_double_run_primary_wins_when_tied(store):
    """Primary wins when both agents return identical quality (first wins)."""
    primary, alt = "ollama", "aider"
    registry = WorkerRegistry()
    registry.register(_OkWorker("ollama", output="answer A"))
    registry.register(_OkWorker("aider", output="answer B"))
    router = _mock_router(
        route_agent=primary,
        escalation_strategy=EscalationStrategy.DOUBLE_RUN.value,
        would_be_agent=alt,
    )

    async def _quality(prompt, output, bucket):
        return (0.80, {"overall": 0.80})

    pool_p1, pool_p2 = _pool_patches()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry",
                  return_value=_adapter_registry(primary, alt)),
            patch("backend.orchestrator.routing.quality.score_quality_detailed",
                  side_effect=_quality),
            pool_p1,
            pool_p2,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/task", json={"prompt": "summarise this"})

        assert resp.status_code == 200
        data = resp.json()
        # alt did NOT beat primary → winner is primary
        assert data["routing"]["double_run_winner"] == primary
        assert "answer A" in data["output"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_no_double_run_when_no_alt_flag(store):
    """Normal single-agent execution when double_run_alt is absent."""
    registry = WorkerRegistry()
    registry.register(_OkWorker("ollama"))
    router = _mock_router(route_agent="ollama")

    async def _quality(prompt, output, bucket):
        return (0.8, {"overall": 0.8})

    pool_p1, pool_p2 = _pool_patches()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry",
                  return_value=_adapter_registry("ollama")),
            patch("backend.orchestrator.routing.quality.score_quality_detailed",
                  side_effect=_quality),
            pool_p1,
            pool_p2,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/task", json={"prompt": "hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["routing"]["double_run_alt"] is None
        assert data["routing"]["double_run_winner"] is None
        assert router.observe.call_count == 1
    finally:
        app.dependency_overrides.clear()
