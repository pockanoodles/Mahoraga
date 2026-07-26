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

    def __init__(self, worker_id: str, metrics: dict | None = None, output: str = "ok output",
                 capabilities: list[str] | None = None):
        self._id = worker_id
        self._metrics = metrics
        self._output = output
        self._capabilities = capabilities or ["general"]

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

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


async def _post(store, registry, router, adapter_reg, path: str, body: dict, ledger=None):
    pool_p1, pool_p2 = _pool_patches()

    async def _quality(prompt, output, bucket):
        return (0.8, {"overall": 0.8})

    if ledger is None:
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
                resp = await client.post(path, json=body)
    finally:
        app.dependency_overrides.clear()
    return resp


async def _post_task(store, registry, router, adapter_reg, prompt: str = "explain the fix",
                     ledger=None):
    return await _post(store, registry, router, adapter_reg,
                       "/api/task", {"prompt": prompt}, ledger=ledger)


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


# ── ledger failure resilience ─────────────────────────────────────────────────

async def test_ledger_write_failure_does_not_fail_api_task(store):
    """A ledger hiccup must not 500 the request or lose the metrics row —
    the money was already spent and the bandit still needs its update."""
    agent, worker_id = "claude-cli", "claude-cli:sonnet"
    registry = WorkerRegistry()
    registry.register(_MetricsWorker(worker_id, metrics=_CLAUDE_CLI_METRICS))
    router = _mock_router(agent)

    ledger = CostLedger(store._conn)
    await ledger.migrate()
    ledger.record = AsyncMock(side_effect=RuntimeError("disk full"))

    resp = await _post_task(store, registry, router, _adapter_registry(agent, worker_id),
                            ledger=ledger)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    ledger.record.assert_awaited()  # the failing write was actually attempted

    # metrics row still written with the real cost
    cur = await store._conn.execute(
        "SELECT cost_usd FROM task_metrics WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.209364)

    # bandit still observed the outcome
    router.observe.assert_called()


# ── gateway chat path (coverage gap) ──────────────────────────────────────────

async def test_gateway_chat_cost_lands_in_ledger_and_chat_log(store, tmp_path):
    """Worker-reported cost on the chat path must land in cost_ledger and sum
    into ChatLogEntry.cost_usd."""
    from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock
    from backend.orchestrator.config import MahoragaConfig
    from backend.orchestrator.channels.base import ChannelMessage
    from backend.orchestrator.gateway import Gateway
    from backend.orchestrator.verifier.verifier import Verifier, VerificationResult

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"active_backend": "claude"}')

    registry = WorkerRegistry()
    registry.register(_MetricsWorker(
        "claude-cli:sonnet",
        metrics=_CLAUDE_CLI_METRICS,
        output="here is the summary",
        capabilities=["general", "code", "plan"],
    ))

    verifier = _MagicMock(spec=Verifier)
    verifier.verify = _AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )

    ledger = CostLedger(store._conn)
    await ledger.migrate()

    gw = Gateway(
        store=store,
        registry=registry,
        verifier=verifier,
        cost_ledger=ledger,
        config=MahoragaConfig(path=cfg_path),
    )
    msg = ChannelMessage.new(user_id="chat-user", channel="web", text="summarize the notes")
    chunks = [c async for c in gw.handle_message(msg)]
    assert any("here is the summary" in c for c in chunks)

    # cost_ledger row carries the worker-reported cost + tokens
    cur = await store._conn.execute(
        "SELECT user_id, model, input_tokens, output_tokens, cost_usd FROM cost_ledger"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    user_id, model, in_tok, out_tok, cost = rows[0]
    assert user_id == "chat-user"
    assert model == "claude-sonnet-5"
    assert (in_tok, out_tok) == (120, 400)
    assert cost == pytest.approx(0.209364)

    # ChatLogEntry.cost_usd sums the per-task costs
    cur = await store._conn.execute("SELECT cost_usd FROM chat_log")
    log_rows = await cur.fetchall()
    assert len(log_rows) == 1
    assert log_rows[0][0] == pytest.approx(0.209364)


# ── /api/batch (coverage gap) ─────────────────────────────────────────────────

async def test_batch_cost_recorded_to_ledger_and_outcome(store):
    """Worker-reported cost on the batch path must land in cost_ledger and the
    bandit TaskOutcome."""
    agent, worker_id = "claude-cli", "claude-cli:sonnet"
    registry = WorkerRegistry()
    registry.register(_MetricsWorker(worker_id, metrics=_CLAUDE_CLI_METRICS))
    router = _mock_router(agent)

    resp = await _post(
        store, registry, router, _adapter_registry(agent, worker_id),
        "/api/batch",
        {"tasks": [{"prompt": "explain the fix"}], "parallel": False},
    )
    assert resp.status_code == 200

    cur = await store._conn.execute(
        "SELECT model, input_tokens, output_tokens, cache_read_tokens, cost_usd FROM cost_ledger"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    model, in_tok, out_tok, cache_tok, cost = rows[0]
    assert model == "claude-sonnet-5"
    assert (in_tok, out_tok, cache_tok) == (120, 400, 10)
    assert cost == pytest.approx(0.209364)

    # bandit outcome carried the real cost
    router.observe.assert_called()
    outcome = router.observe.call_args.args[1]
    assert outcome.cost_usd == pytest.approx(0.209364)
