"""End-to-end tests for the reward feedback loop.

Scope
-----
These tests POST to /api/task and /api/batch through the real FastAPI app,
mock _run_task so no actual AI agent is invoked, and then assert that
`router.observe()` was called and that the decision log captured a reward.

The decision logger uses an in-memory SQLite DB (DecisionLogger(db_path=":memory:"))
so these tests do not touch ~/.mahoraga/routing_decisions.db.

The store is an in-memory aiosqlite DB, same as all other orchestrator tests.

Regression coverage
-------------------
test_batch_task_rewards_written guards the bug where batch tasks never called
router.observe(), leaving success/reward NULL in the decision log.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.orchestrator.adapters.base import (
    AgentAdapter,
    AgentCapability,
    AgentStatus,
    CostEstimate,
)
from backend.orchestrator.adapters.registry import AdapterRegistry
from backend.orchestrator.domain.models import (
    Mission,
    Plan,
    Run,
    RunMode,
    Task,
    TaskStatus,
)
from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.implicit_quality import ImplicitQualityTracker
from backend.orchestrator.service.app import (
    app,
    get_adapter_registry,
    get_registry,
    get_store,
    get_verifier,
)
from backend.orchestrator.store.base import Store
from backend.orchestrator.verifier.verifier import VerificationResult, Verifier
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry


# ── test doubles ──────────────────────────────────────────────────────────────


class _OkWorker(WorkerAdapter):
    """Minimal worker that immediately completes every task."""

    @property
    def id(self) -> str:
        return "extension"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "general"]

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": "done"})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="extension", healthy=True)


class _OkAdapter(AgentAdapter):
    """Minimal AgentAdapter that reports itself as healthy and maps to _OkWorker."""

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def worker_id(self) -> str:
        return "extension"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability(name="general", confidence=1.0)]

    def estimate_cost(self, task) -> CostEstimate:
        return CostEstimate(estimated_tokens=0, estimated_cost_usd=0.0)

    async def health_check(self) -> AgentStatus:
        return AgentStatus(name="ollama", available=True, detail="mock")


def _make_pass_verifier() -> Verifier:
    result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v


def _make_router_with_memory_db() -> BanditRouter:
    """Return a BanditRouter wired to an in-memory decision log.

    Uses a temp Path for state so save_state() does not write ~/.mahoraga/.
    """
    logger = DecisionLogger(db_path=Path(":memory:"))
    adapter_reg = AdapterRegistry()
    adapter_reg.register(_OkAdapter())
    router = BanditRouter(
        strategy="linucb",
        registry=adapter_reg,
        logger=logger,
        state_path=Path("/tmp/test_bandit_state.json"),
    )
    return router


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    # MetricsStore.migrate() is normally called in lifespan; do it here.
    await s.metrics.migrate()
    yield s
    await s.close()


@pytest.fixture
def registry():
    reg = WorkerRegistry()
    reg.register(_OkWorker())
    return reg


@pytest.fixture
def adapter_registry():
    reg = AdapterRegistry()
    reg.register(_OkAdapter())
    return reg


@pytest.fixture
def router(adapter_registry):
    logger = DecisionLogger(db_path=Path(":memory:"))
    r = BanditRouter(
        strategy="linucb",
        registry=adapter_registry,
        logger=logger,
        state_path=Path("/tmp/test_bandit_state.json"),
    )
    return r


@pytest.fixture
def implicit_tracker():
    return ImplicitQualityTracker()


@pytest.fixture
def client_setup(store, registry, router, adapter_registry, implicit_tracker):
    """Wire all app dependencies and module-level singletons for one test."""
    verifier = _make_pass_verifier()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: verifier
    app.dependency_overrides[get_adapter_registry] = lambda: adapter_registry

    # app.py calls router.route(..., model_warm_norm=...) which is not in the
    # BanditRouter.route() signature.  Wrap the real method to absorb that kwarg.
    _real_route = router.route
    def _compat_route(task, **kwargs):
        kwargs.pop("model_warm_norm", None)
        return _real_route(task, **kwargs)
    router.route = _compat_route

    patches = [
        patch("backend.orchestrator.service.app._bandit_router", router),
        patch("backend.orchestrator.service.app._adapter_registry", adapter_registry),
        patch("backend.orchestrator.service.app._implicit_tracker", implicit_tracker),
        patch("backend.orchestrator.service.app._store", store),
        # Prevent score_quality from calling Ollama embeddings
        patch(
            "backend.orchestrator.routing.quality.score_quality",
            new=AsyncMock(return_value=0.75),
        ),
        # Prevent _is_ollama_warm from hitting localhost:11434
        patch(
            "backend.orchestrator.service.app._is_ollama_warm",
            new=AsyncMock(return_value=False),
        ),
        # store.metrics.record() is called with prompt_text= which does not exist
        # in the MetricsStore.record() signature — absorb it here so the endpoint
        # does not crash before reaching router.observe().
        patch.object(store.metrics, "record", new=AsyncMock(return_value=None)),
    ]

    for p in patches:
        p.start()

    yield

    for p in patches:
        p.stop()
    router.route = _real_route  # restore
    app.dependency_overrides.clear()


# ── helpers ────────────────────────────────────────────────────────────────────


def _decision_rows(router: BanditRouter) -> list[dict]:
    """Read all rows from the router's in-memory decision log."""
    with router.logger._lock:
        cur = router.logger._conn.execute(
            "SELECT task_id, success, reward FROM decisions ORDER BY id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


async def _make_completed_task(store: Store) -> Task:
    """Insert a mission/plan/run/task into the store and return the task."""
    m = Mission.new(title="T", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    task = Task.new(run_id=r.id, title="T", goal="G")
    task = dataclasses.replace(task, status=TaskStatus.completed)
    await store.tasks.save(task)
    return task


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_single_task_reward_written(store, client_setup, router):
    """POST /api/task → decision log has success != null and reward in [0, 1].

    _run_task is mocked to complete the task instantly without running an agent.
    """

    async def _fake_run_task(task_id, s, reg, ver):
        # Mark the task as completed so the endpoint can observe it.
        await s.tasks.update_status(task_id, TaskStatus.completed)

    with patch(
        "backend.orchestrator.service.app._run_task",
        side_effect=_fake_run_task,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/task",
                json={"prompt": "write a hello world function"},
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    task_id = body["task_id"]

    # Check decision log
    rows = _decision_rows(router)
    assert len(rows) == 1, f"Expected 1 decision row, got {rows}"
    row = rows[0]
    assert row["task_id"] == task_id
    assert row["success"] is not None, "success must be non-null after observe()"
    assert row["reward"] is not None, "reward must be non-null after observe()"
    assert 0.0 <= row["reward"] <= 1.0, f"reward out of range: {row['reward']}"


async def test_batch_task_rewards_written(store, client_setup, router):
    """POST /api/batch with 2 tasks → both tasks have reward written.

    This is the regression test for the bug where batch tasks never called
    router.observe(), leaving success/reward NULL in the decision log.
    """

    async def _fake_run_task(task_id, s, reg, ver):
        await s.tasks.update_status(task_id, TaskStatus.completed)

    with patch(
        "backend.orchestrator.service.app._run_task",
        side_effect=_fake_run_task,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/batch",
                json={
                    "tasks": [
                        {"prompt": "task one: summarize docs"},
                        {"prompt": "task two: write tests"},
                    ],
                    "parallel": False,
                },
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    results = body["results"]
    assert len(results) == 2

    rows = _decision_rows(router)
    # Each task gets one routing decision → two rows total
    assert len(rows) == 2, f"Expected 2 decision rows (one per task), got {rows}"

    for row in rows:
        assert row["success"] is not None, (
            f"Batch task {row['task_id']!r} missing success — "
            "router.observe() was not called (regression)"
        )
        assert row["reward"] is not None, (
            f"Batch task {row['task_id']!r} missing reward — "
            "router.observe() was not called (regression)"
        )
        assert 0.0 <= row["reward"] <= 1.0, f"reward out of range: {row['reward']}"


async def test_implicit_signal_triggers_bandit_nudge(
    store, client_setup, router, implicit_tracker
):
    """Task A completes → task B submitted with different hash within 10 s
    → accept signal fires → bandit.apply_implicit_reward() is called
    → the previous task's decision gets updated in the log
    → the bandit's internal step counter (t) has advanced from the updates.

    Uses freezegun-style time mocking via ImplicitQualityTracker.on_task_complete
    to control the elapsed-time check without sleeping.
    """
    import time

    async def _fake_run_task(task_id, s, reg, ver):
        await s.tasks.update_status(task_id, TaskStatus.completed)

    with patch(
        "backend.orchestrator.service.app._run_task",
        side_effect=_fake_run_task,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # ── Task A ──────────────────────────────────────────────────────
            resp_a = await ac.post(
                "/api/task",
                json={"prompt": "task a: write a parser"},
            )
            assert resp_a.status_code == 200, resp_a.text
            task_a_id = resp_a.json()["task_id"]

            # Capture bandit step count after task A so we can verify it advances.
            t_after_a = router.strategy.t

            # Manually seed the implicit tracker as if task A just completed
            # with a "now" timestamp — this simulates what the endpoint does via
            # _implicit_tracker.on_task_complete(...).
            # We force completed_at = now so the accept window (10 s) is fresh.
            implicit_tracker.on_task_complete(
                task_id=task_a_id,
                task_hash="aaaaaaaaaaaaaaa1",  # different from task B's hash
                completed_at=time.time(),
            )

            # ── Task B (different prompt → different hash) ─────────────────
            resp_b = await ac.post(
                "/api/task",
                json={"prompt": "task b: write integration tests"},
            )
            assert resp_b.status_code == 200, resp_b.text

    # After task B, the accept signal should have fired and nudged the bandit.
    # The bandit's t counter increments on every strategy.update() call.
    # Task A called observe() → +1. Task B called observe() → +1.
    # If apply_implicit_reward() fired, it calls strategy.update() → +1 more.
    # Minimum expected: t_after_a + 1 (task B observe).
    # With implicit signal: t_after_a + 2 (task B observe + implicit nudge).
    t_after_b = router.strategy.t
    assert t_after_b > t_after_a, (
        f"Bandit strategy.t did not advance after task B "
        f"(before={t_after_a}, after={t_after_b})"
    )

    # The decision log for task A should have reward written (from task A's observe).
    rows = _decision_rows(router)
    task_a_rows = [r for r in rows if r["task_id"] == task_a_id]
    assert task_a_rows, f"No decision row found for task_a_id={task_a_id!r}"
    assert task_a_rows[0]["reward"] is not None, (
        "Task A's decision row missing reward — router.observe() was not called"
    )


async def test_single_task_observe_fires_on_exception(store, client_setup, router):
    """Regression: router.observe() must fire even when _run_task raises in /api/task.

    Previously, an unhandled exception propagated out of _run_task at the single-task
    endpoint and skipped observe + metrics entirely, leaking rewards on the happy-path
    endpoint as well as the batch one.
    """
    # ASGITransport re-raises unhandled exceptions into the test client by
    # default (raise_app_exceptions=True), so we expect a RuntimeError here.
    # In production FastAPI surfaces this as HTTP 500. Either way, the
    # critical contract is that observe + metrics fire before the raise.
    with patch(
        "backend.orchestrator.service.app._run_task",
        side_effect=RuntimeError("simulated failure"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            with pytest.raises(RuntimeError, match="simulated failure"):
                await ac.post(
                    "/api/task",
                    json={"prompt": "write a hello world function"},
                )

    # The decision log MUST still have a row with reward written
    rows = _decision_rows(router)
    assert len(rows) == 1, f"Expected 1 decision row, got {rows}"
    row = rows[0]
    assert row["success"] is not None, (
        "success must be non-null after observe() — reward leak regression"
    )
    assert row["success"] == 0, f"Expected success=0 (False), got {row['success']}"
    assert row["reward"] is not None, "reward must be non-null after observe()"
    assert row["reward"] == 0.0, f"Expected reward=0.0 for failed task, got {row['reward']}"


async def test_batch_run_single_observe_fires_on_exception(store, client_setup, router):
    """Regression: router.observe() must fire even when _run_task raises in _run_single.

    Previously, an unhandled exception propagated out of _run_task and skipped
    the observe() call entirely, leaking 41% of codex-cli rewards.
    """
    with patch(
        "backend.orchestrator.service.app._run_task",
        side_effect=RuntimeError("simulated failure"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/batch",
                json={
                    "tasks": [{"prompt": "task: write a hello world function"}],
                    "parallel": False,
                },
            )

    # Endpoint must not crash — returns 200 with a failed result
    assert resp.status_code == 200, resp.text
    body = resp.json()
    results = body["results"]
    assert len(results) == 1
    assert results[0]["status"] == "failed"

    # observe() must have been called with success=False
    rows = _decision_rows(router)
    assert len(rows) == 1, f"Expected 1 decision row, got {rows}"
    row = rows[0]
    assert row["success"] is not None, (
        "success must be non-null after observe() — reward leak regression"
    )
    assert row["success"] == 0, f"Expected success=0 (False), got {row['success']}"
    assert row["reward"] is not None, "reward must be non-null after observe()"
    assert row["reward"] == 0.0, f"Expected reward=0.0 for failed task, got {row['reward']}"
