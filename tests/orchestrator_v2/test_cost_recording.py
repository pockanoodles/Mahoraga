"""Tests for real cost capture through the /api/task recording path.

Validates that a worker-reported `metrics` event carrying cost_usd flows into:
 - task_metrics.cost_usd (MetricsStore)
 - the cost_ledger (CostLedger), with token counts
 - the bandit outcome (router.observe cost_usd)
and that local arms (no cost in the payload) still record 0.0 with no ledger rows.
"""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from backend.orchestrator.routing.escalation_strategies import EscalationStrategy
from backend.orchestrator.routing.execution_pool import ExecutionPool
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.store.base import Store
from backend.orchestrator.tracking.ledger import CostLedger
from backend.orchestrator.tracking.pricing import resolve_cost, calculate_cost
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry


# ── Helpers (mirrors test_double_run.py setup) ────────────────────────────────

def _pool_patches():
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


class _MetricsWorker(WorkerAdapter):
    """Worker that emits a metrics event (like ClaudeCliWorker/OllamaWorker)."""

    def __init__(self, worker_id: str, metrics: dict | None = None, output: str = "ok output"):
        self._id = worker_id
        self._metrics = metrics
        self._output = output

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return ["general"]

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        if self._metrics is not None:
            yield WorkerEvent("metrics", dict(self._metrics))
        yield WorkerEvent("attempt.completed", {"summary": self._output})

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


def _mock_router(route_agent: str):
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
    composed.escalation_strategy = EscalationStrategy.NONE.value
    router._last_composed = composed
    return router


def _adapter_registry(agent: str, worker_id: str):
    ar = MagicMock()
    ar.get = lambda name: MagicMock(worker_id=worker_id) if name == agent else None
    ar.all = MagicMock(return_value=[])
    return ar


_CLAUDE_CLI_METRICS = {
    "elapsed_s": 1.64,
    "tokens": 400,
    "throughput_tps": 244.0,
    "prompt_tokens": 120,
    "cache_read_tokens": 10,
    "cache_creation_tokens": 34883,
    "cost_usd": 0.209364,
    "model": "claude-sonnet-5",
}


async def _post_task(store, registry, router, adapter_reg, prompt: str = "explain the fix"):
    pool_p1, pool_p2 = _pool_patches()

    async def _quality(prompt, output, bucket):
        return (0.8, {"overall": 0.8})

    ledger = CostLedger(store._conn)
    await ledger.migrate()

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_verifier()
    try:
        with (
            patch("backend.orchestrator.service.app.get_bandit_router", return_value=router),
            patch("backend.orchestrator.service.app.get_adapter_registry", return_value=adapter_reg),
            patch("backend.orchestrator.service.app._cost_ledger", ledger),
            patch("backend.orchestrator.routing.quality.score_quality_detailed", side_effect=_quality),
            pool_p1,
            pool_p2,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/task", json={"prompt": prompt})
    finally:
        app.dependency_overrides.clear()
    return resp


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


# ── resolve_cost unit behaviour ───────────────────────────────────────────────

def test_resolve_cost_prefers_reported_cost():
    assert resolve_cost(_CLAUDE_CLI_METRICS) == pytest.approx(0.209364)


def test_resolve_cost_computes_from_tokens_when_cost_missing():
    payload = {k: v for k, v in _CLAUDE_CLI_METRICS.items() if k != "cost_usd"}
    expected = calculate_cost("claude-sonnet-5", 120, 400, 10)
    assert resolve_cost(payload) == pytest.approx(expected)


def test_resolve_cost_zero_for_local_arms():
    # Ollama payload: tokens but no model, no cost_usd
    assert resolve_cost({"elapsed_s": 2.0, "tokens": 100, "throughput_tps": 50.0}) == 0.0


def test_resolve_cost_empty_payload():
    assert resolve_cost({}) == 0.0


# ── /api/task recording path ──────────────────────────────────────────────────

async def test_worker_reported_cost_written_to_task_metrics_and_ledger(store):
    agent, worker_id = "claude-cli", "claude-cli:sonnet"
    registry = WorkerRegistry()
    registry.register(_MetricsWorker(worker_id, metrics=_CLAUDE_CLI_METRICS))
    router = _mock_router(agent)

    resp = await _post_task(store, registry, router, _adapter_registry(agent, worker_id))
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # task_metrics.cost_usd carries the worker-reported cost
    cur = await store._conn.execute(
        "SELECT cost_usd, tokens_generated, prompt_tokens FROM task_metrics WHERE task_id = ?",
        (task_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.209364)
    assert row[1] == 400
    assert row[2] == 120

    # cost_ledger got one entry with the same cost + token counts
    cur = await store._conn.execute(
        "SELECT user_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd FROM cost_ledger"
    )
    ledger_rows = await cur.fetchall()
    assert len(ledger_rows) == 1
    user_id, model, in_tok, out_tok, cache_tok, cost = ledger_rows[0]
    assert user_id == "web-user"
    assert model == "claude-sonnet-5"
    assert (in_tok, out_tok, cache_tok) == (120, 400, 10)
    assert cost == pytest.approx(0.209364)

    # bandit outcome saw the real cost (feeds decision_log.log_outcome / φ_cost)
    outcome = router.observe.call_args.args[1]
    assert outcome.cost_usd == pytest.approx(0.209364)


async def test_local_arm_records_zero_cost_and_no_ledger_rows(store):
    agent, worker_id = "ollama", "ollama:qwen3.5:general"
    registry = WorkerRegistry()
    # Ollama-style payload: tokens present, no model/cost
    registry.register(_MetricsWorker(
        worker_id, metrics={"elapsed_s": 2.0, "tokens": 150, "throughput_tps": 75.0},
    ))
    router = _mock_router(agent)

    resp = await _post_task(store, registry, router, _adapter_registry(agent, worker_id))
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    cur = await store._conn.execute(
        "SELECT cost_usd FROM task_metrics WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0.0

    cur = await store._conn.execute("SELECT COUNT(*) FROM cost_ledger")
    assert (await cur.fetchone())[0] == 0

    outcome = router.observe.call_args.args[1]
    assert outcome.cost_usd == 0.0
