"""End-to-end tests for the escalation cascade on the /api/task serving path.

These POST through the real FastAPI app with `_run_task` mocked (no agent runs),
a scripted judge verdict, and a scripted escalation arm — so they assert the
*wiring*, not model behaviour.

The invariants that matter, and why:

  - a judge REJECT swaps what the caller is served, which is the entire point:
    before this wire the verdict was computed, spent on the reward, and the
    known-bad answer went out anyway;
  - the bandit keeps observing the LOCAL arm's own output. Crediting the local
    arm with the escalation arm's answer would re-break the correctness signal
    Era 23 established, and the reward would start rewarding failure;
  - an accept, an abstain, or a failed escalation all leave the response byte
    for byte what it was before the cascade existed.
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
from backend.orchestrator.domain.models import Artifact, TaskStatus
from backend.orchestrator.routing import cascade
from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.decision_log import DecisionLogger
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

LOCAL_OUTPUT = "```python\ndef f(n):\n    return n + 1\n```"
CLOUD_OUTPUT = "```python\ndef f(n):\n    return n * 2\n```"


# ── test doubles ─────────────────────────────────────────────────────────────


class _OkWorker(WorkerAdapter):
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
    @property
    def name(self) -> str:
        return "ollama"

    @property
    def worker_id(self) -> str:
        return "extension"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability(name="code", confidence=1.0)]

    def estimate_cost(self, task) -> CostEstimate:
        return CostEstimate(estimated_tokens=0, estimated_cost_usd=0.0)

    async def health_check(self) -> AgentStatus:
        return AgentStatus(name="ollama", available=True, detail="mock")


def _make_pass_verifier() -> Verifier:
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )
    return v


# ── fixtures ─────────────────────────────────────────────────────────────────


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
    return BanditRouter(
        strategy="linucb",
        registry=adapter_registry,
        logger=DecisionLogger(db_path=Path(":memory:")),
        state_path=Path("/tmp/test_cascade_bandit_state.json"),
    )


@pytest.fixture
def client_setup(store, registry, router, adapter_registry, monkeypatch):
    """Wire app dependencies; keep every model call off the network."""
    for var in ("MAHORAGA_CASCADE", "MAHORAGA_ESCALATE_TO",
                "MAHORAGA_ESCALATE_MAX_PER_DAY", "MAHORAGA_REWARD_JUDGE"):
        monkeypatch.delenv(var, raising=False)
    cascade.reset_budget_for_tests()

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = _make_pass_verifier
    app.dependency_overrides[get_adapter_registry] = lambda: adapter_registry

    patches = [
        patch("backend.orchestrator.service.app._bandit_router", router),
        patch("backend.orchestrator.service.app._adapter_registry", adapter_registry),
        patch("backend.orchestrator.service.app._implicit_tracker", None),
        patch("backend.orchestrator.service.app._cost_ledger", None),
        patch("backend.orchestrator.service.app._store", store),
        patch("backend.orchestrator.routing.quality.score_quality",
              new=AsyncMock(return_value=0.75)),
        patch("backend.orchestrator.service.app._is_ollama_warm",
              new=AsyncMock(return_value=False)),
        # The exec gate would run the generated code in a subprocess; the
        # cascade is what's under test here, so keep it out of the way.
        patch("backend.orchestrator.routing.execution_gate.exec_gate_enabled",
              return_value=False),
        patch.object(store.metrics, "record", new=AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()
    app.dependency_overrides.clear()
    cascade.reset_budget_for_tests()


# ── helpers ──────────────────────────────────────────────────────────────────


def _run_task_producing(output: str):
    """Mock `_run_task` so the task completes with `output` as its artifact."""

    async def _fake(task_id, s, reg, ver):
        task = await s.tasks.get(task_id)
        await s.artifacts.save(
            Artifact.new(
                run_id=task.run_id,
                task_id=task_id,
                attempt_id="att-test",
                type="text_output",
                location={"content": output},
            )
        )
        await s.tasks.update_status(task_id, TaskStatus.completed)

    return _fake


async def _post_code_task(judge_correctness_value, escalate_result):
    """POST one code task with a scripted judge verdict + escalation result."""
    with patch("backend.orchestrator.service.app._run_task",
               side_effect=_run_task_producing(LOCAL_OUTPUT)), \
         patch("backend.orchestrator.routing.reward_judge.judge_correctness",
               new=AsyncMock(return_value=(judge_correctness_value, 0.0, "scripted"))), \
         patch("backend.orchestrator.routing.cascade.escalate",
               new=AsyncMock(return_value=escalate_result)) as esc:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/task",
                json={"prompt": "write a python function f(n)", "capability_hint": "code"},
            )
    return resp, esc


def _decision_rows(router: BanditRouter) -> list[dict]:
    with router.logger._lock:
        cur = router.logger._conn.execute(
            "SELECT task_id, success, reward, correctness FROM decisions ORDER BY id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── the wire ─────────────────────────────────────────────────────────────────


async def test_reject_serves_escalated_output(store, client_setup, router):
    """The whole point: a rejected local answer is replaced, not shipped."""
    resp, esc = await _post_code_task(0.0, (CLOUD_OUTPUT, 0.02, "escalated to claude-cli"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"] == CLOUD_OUTPUT
    assert body["cascade"]["escalated"] is True
    assert body["cascade"]["escalated_to"] == "claude-cli"
    assert body["cascade"]["escalation_cost_usd"] == pytest.approx(0.02)
    esc.assert_awaited_once()


async def test_accept_serves_local_output_and_never_escalates(store, client_setup, router):
    resp, esc = await _post_code_task(1.0, (CLOUD_OUTPUT, 0.02, "unused"))

    body = resp.json()
    assert body["output"] == LOCAL_OUTPUT
    assert body["cascade"]["escalated"] is False
    assert body["cascade"]["escalated_to"] is None
    esc.assert_not_awaited()


async def test_judge_abstain_never_escalates(store, client_setup, router):
    """A None verdict is the judge being off/unavailable — it must not spend."""
    resp, esc = await _post_code_task(None, (CLOUD_OUTPUT, 0.02, "unused"))

    assert resp.json()["output"] == LOCAL_OUTPUT
    esc.assert_not_awaited()


async def test_failed_escalation_falls_back_to_local(store, client_setup, router):
    """The arm being down must degrade to the pre-cascade behaviour, not a 500."""
    resp, esc = await _post_code_task(0.0, (None, 0.0, "escalation skipped: arm unavailable"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"] == LOCAL_OUTPUT
    assert body["cascade"]["escalated"] is False
    assert "unavailable" in body["cascade"]["escalation_detail"]
    esc.assert_awaited_once()


async def test_cascade_disabled_leaves_serving_untouched(store, client_setup, router, monkeypatch):
    monkeypatch.setenv("MAHORAGA_CASCADE", "off")
    resp, esc = await _post_code_task(0.0, (CLOUD_OUTPUT, 0.02, "unused"))

    assert resp.json()["output"] == LOCAL_OUTPUT
    esc.assert_not_awaited()


# ── the attribution invariant ────────────────────────────────────────────────


async def test_bandit_observes_local_arm_not_the_escalation(store, client_setup, router):
    """The decision row must record the LOCAL arm's judged failure.

    If escalation re-attributed the outcome, a rejected arm would be logged as
    correct — the bandit would learn that producing wrong code is fine as long
    as something else fixes it, which is exactly the reward corruption Era 23
    was spent removing.
    """
    resp, _esc = await _post_code_task(0.0, (CLOUD_OUTPUT, 0.02, "escalated to claude-cli"))

    rows = _decision_rows(router)
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == resp.json()["task_id"]
    assert row["correctness"] == 0.0, "the local arm's own verdict must survive escalation"
    assert row["reward"] is not None


async def test_escalation_cost_is_not_charged_to_the_local_arm(store, client_setup, router):
    """φ_cost must keep describing what the selected arm charged.

    Folding escalation spend into the outcome would penalize the local arm
    twice for one rejection — once via correctness=0, again via a bill it never
    ran up.
    """
    observed = {}
    real_observe = router.observe

    def _capture(task, outcome):
        observed["cost_usd"] = outcome.cost_usd
        observed["agent"] = outcome.agent_name
        return real_observe(task, outcome)

    with patch.object(router, "observe", side_effect=_capture):
        await _post_code_task(0.0, (CLOUD_OUTPUT, 0.75, "escalated to claude-cli"))

    assert observed["agent"] == "ollama"
    assert observed["cost_usd"] == 0.0, "escalation spend leaked into the bandit's cost feature"


# ── the exec-gate trigger on the serving path ────────────────────────────────

BROKEN_OUTPUT = "```python\ndef f(n):\n    return n +\n```"


async def _post_broken_code_task(escalate_result):
    """POST a task whose output does not compile, with the exec gate ENABLED.

    The reward judge is left unpatched on purpose: the gate flips the task to
    failed, so the `if success` guard means the judge never runs — which is
    exactly the path that used to skip escalation.
    """
    with patch("backend.orchestrator.service.app._run_task",
               side_effect=_run_task_producing(BROKEN_OUTPUT)), \
         patch("backend.orchestrator.routing.execution_gate.exec_gate_enabled",
               return_value=True), \
         patch("backend.orchestrator.routing.cascade.escalate",
               new=AsyncMock(return_value=escalate_result)) as esc:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/task",
                json={"prompt": "write a python function f(n)", "capability_hint": "code"},
            )
    return resp, esc


async def test_non_compiling_output_escalates(store, client_setup, router):
    """The regression this was written for: broken code used to be served as-is.

    The exec gate marks the task failed, which skips the reward judge, which
    left `correctness=None` — and a None never escalates. So the one class of
    answer known for certain to be wrong was the one class that never got a
    second opinion.
    """
    resp, esc = await _post_broken_code_task((CLOUD_OUTPUT, 0.02, "escalated to claude-cli"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    esc.assert_awaited_once()
    assert body["output"] == CLOUD_OUTPUT
    assert body["cascade"]["escalated"] is True


async def test_escalated_exec_failure_reports_success_to_the_caller(store, client_setup, router):
    """The caller has a working answer, so the request succeeded."""
    resp, _esc = await _post_broken_code_task((CLOUD_OUTPUT, 0.02, "escalated"))
    assert resp.json()["status"] == "success"


async def test_exec_failure_still_records_the_local_arm_as_failed(store, client_setup, router):
    """The response says success; the bandit must still see the arm's failure.

    Conflating these would teach the bandit that emitting uncompilable code is
    fine, since the cascade cleans up after it.
    """
    await _post_broken_code_task((CLOUD_OUTPUT, 0.02, "escalated"))

    rows = _decision_rows(router)
    assert len(rows) == 1
    assert rows[0]["success"] == 0, "the local arm's exec-gate failure must survive escalation"


async def test_failed_escalation_keeps_the_failed_status(store, client_setup, router):
    """If escalation buys nothing, the request is still a failure — no lying."""
    resp, _esc = await _post_broken_code_task((None, 0.0, "arm unavailable"))
    body = resp.json()
    assert body["status"] == "failed"
    assert body["output"] == BROKEN_OUTPUT
    assert body["cascade"]["escalated"] is False
