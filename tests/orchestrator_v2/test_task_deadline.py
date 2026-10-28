"""/api/task's deadline is the caller's, and a timeout comes back as an answer.

On 2026-10-28 the daemon's own 120 s clock cut granite4.2 off while the bench
believed its limit was 180 s, and each timeout left the endpoint as a raised
exception: uvicorn closed the connection behind the 500, and the bench lost
the next prompt on it, 59 times.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from backend.orchestrator.routing.escalation_strategies import EscalationStrategy
from backend.orchestrator.routing.execution_pool import ExecutionPool
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.store.base import Store
from backend.orchestrator.tracking.ledger import CostLedger
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry

AGENT, WORKER_ID = "ollama:granite4.2-8b", "ollama:granite4.2-8b:general"


# ── Helpers (mirrors test_cost_recording.py) ─────────────────────────────────

class _MetricsWorker(WorkerAdapter):
    def __init__(self, metrics: dict):
        self._metrics = metrics

    @property
    def id(self) -> str:
        return WORKER_ID

    @property
    def capabilities(self) -> list[str]:
        return ["general"]

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("metrics", dict(self._metrics))
        yield WorkerEvent("attempt.completed", {"summary": "ok output"})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=WORKER_ID, healthy=True)


def _mock_router():
    from backend.orchestrator.routing.reward import RewardCalculator

    router = MagicMock()
    router.route = MagicMock(return_value=AGENT)
    router.observe = MagicMock()
    router.log_override = MagicMock(return_value=1)
    router.reward_calc = RewardCalculator()
    router.strategy = MagicMock()
    router.strategy.name = "linucb_per_bucket"
    router.strategy.get_scores = MagicMock(return_value={})
    router.logger = MagicMock()
    router.logger.count = MagicMock(return_value=0)
    composed = MagicMock()
    composed.escalation_strategy = EscalationStrategy.NONE.value
    router._last_composed = composed
    return router


def _adapter_registry():
    ar = MagicMock()
    ar.get = lambda name: MagicMock(worker_id=WORKER_ID) if name == AGENT else None
    ar.all = MagicMock(return_value=[])
    return ar


def _make_verifier() -> Verifier:
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )
    return v


async def _post(store, router, body: dict, registry=None, default_timeout: float = 30.0):
    async def _quality(prompt, output, bucket):
        return (0.8, {"overall": 0.8})

    ledger = CostLedger(store._conn)
    await ledger.migrate()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry or WorkerRegistry()
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry",
                  return_value=_adapter_registry()),
            patch("backend.orchestrator.service.app._cost_ledger", ledger),
            patch("backend.orchestrator.routing.quality.score_quality_detailed",
                  side_effect=_quality),
            patch("backend.orchestrator.routing.execution_pool.get_default_pool",
                  return_value=ExecutionPool(max_concurrent=8)),
            patch("backend.orchestrator.routing.execution_pool.resolve_task_timeout",
                  return_value=default_timeout),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post("/api/task", json=body)
    finally:
        app.dependency_overrides.clear()


async def _never_finishes(*args, **kwargs):
    await asyncio.sleep(30)


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


# ── The deadline ─────────────────────────────────────────────────────────────

async def test_the_request_deadline_cuts_the_task_and_answers_504(store):
    router = _mock_router()
    with patch("backend.orchestrator.service.app._run_task", side_effect=_never_finishes):
        resp = await _post(store, router, {"prompt": "write add()", "timeout_s": 0.2})

    assert resp.status_code == 504, resp.text
    detail = resp.json()["detail"]
    assert detail["status"] == "timed_out"
    assert detail["timeout_s"] == 0.2
    assert detail["elapsed_s"] < 5
    router.observe.assert_called_once()  # the bandit still learns from the timeout


async def test_without_a_request_deadline_the_daemon_default_applies(store):
    with patch("backend.orchestrator.service.app._run_task", side_effect=_never_finishes):
        resp = await _post(store, _mock_router(), {"prompt": "write add()"}, default_timeout=0.2)

    assert resp.status_code == 504, resp.text
    assert resp.json()["detail"]["timeout_s"] == 0.2


async def test_a_deadline_must_be_positive(store):
    resp = await _post(store, _mock_router(), {"prompt": "write add()", "timeout_s": 0})
    assert resp.status_code == 422


async def test_a_failure_is_answered_not_raised(store):
    """ASGITransport re-raises app exceptions by default, so getting a response
    at all shows the failure no longer escapes the endpoint."""
    with patch("backend.orchestrator.service.app._run_task",
               side_effect=RuntimeError("simulated failure")):
        resp = await _post(store, _mock_router(), {"prompt": "write add()"})

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["status"] == "failed"
    assert "RuntimeError: simulated failure" in detail["error"]


async def test_the_response_reports_the_reasoning_a_reply_carried(store):
    registry = WorkerRegistry()
    registry.register(_MetricsWorker({"tokens": 1400, "throughput_tps": 18.0,
                                      "elapsed_s": 77.0, "thinking_chars": 4100}))
    resp = await _post(store, _mock_router(), {"prompt": "write add()"}, registry=registry)

    assert resp.status_code == 200, resp.text
    assert resp.json()["metrics"]["thinking_chars"] == 4100
