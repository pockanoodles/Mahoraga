"""Tests for backend.orchestrator.gateway.Gateway."""
from __future__ import annotations

import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.channels.base import ChannelMessage
from backend.orchestrator.domain.models import Task, TaskStatus, TaskAttempt, AttemptStatus
from backend.orchestrator.gateway import Gateway
from backend.orchestrator.planning.planner import PlannerError


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_msg(text: str = "Do something useful", user_id: str = "test-user") -> ChannelMessage:
    return ChannelMessage.new(user_id=user_id, channel="web", text=text)


def _make_task(run_id: str = "run-1", title: str = "Task A") -> Task:
    return Task(
        id=str(uuid.uuid4()),
        run_id=run_id,
        parent_task_id=None,
        title=title,
        goal="Do the thing",
        scope=[],
        context_refs=[],
        done_criteria="",
        dependencies=[],
        constraints=[],
        preferred_worker_type=None,
        required_capabilities=[],
        escalation_count=0,
        status=TaskStatus.ready,
        created_at=time.time(),
        updated_at=time.time(),
    )


def _make_attempt(task_id: str, summary: str = "Task done.", output: str = "") -> TaskAttempt:
    return TaskAttempt(
        id=str(uuid.uuid4()),
        task_id=task_id,
        worker_id="claude:sonnet",
        status=AttemptStatus.completed,
        error_code="",
        blocking_reason="",
        started_at=time.time(),
        ended_at=time.time(),
        summary=summary,
        output=output,
        artifact_refs=[],
        validator_refs=[],
    )


def _make_store(task: Task | None = None, attempts: list | None = None) -> MagicMock:
    """Build a minimal mock Store."""
    store = MagicMock()

    # missions sub-store
    store.missions.save = AsyncMock()
    store.missions.save_plan = AsyncMock()
    store.missions.save_run = AsyncMock()

    # tasks sub-store
    store.tasks.save = AsyncMock()
    store.tasks.get = AsyncMock(return_value=task)
    store.tasks.list_attempts = AsyncMock(return_value=attempts or [])

    return store


def _make_gateway(store, tasks_from_planner=None, run_task_side_effect=None,
                  adaptive_store=None, adapter_registry=None) -> Gateway:
    registry = MagicMock()
    verifier = MagicMock()
    gw = Gateway(
        store=store,
        registry=registry,
        verifier=verifier,
        adaptive_store=adaptive_store,
        adapter_registry=adapter_registry,
    )

    # Attach patches as attributes so callers can use them as context managers
    planner_mock = AsyncMock(return_value=tasks_from_planner or [])
    gw._planner_patch = patch("backend.orchestrator.gateway.generate_tasks", planner_mock)
    gw._planner_mock = planner_mock

    if run_task_side_effect is not None:
        rt_mock = AsyncMock(side_effect=run_task_side_effect)
    else:
        rt_mock = AsyncMock(return_value=None)
    gw._run_task_patch = patch("backend.orchestrator.gateway.run_task", rt_mock)
    gw._run_task_mock = rt_mock

    return gw


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_gateway_routes_message_creates_mission():
    """A valid message must create and persist a Mission."""
    task = _make_task()
    store = _make_store(task=task, attempts=[_make_attempt(task.id)])
    gw = _make_gateway(store, tasks_from_planner=[task])

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    store.missions.save.assert_awaited_once()
    saved_mission = store.missions.save.call_args[0][0]
    assert saved_mission.goal == msg.text


async def test_gateway_returns_response_chunks():
    """Gateway yields the attempt summaries from completed tasks."""
    task = _make_task()
    attempt = _make_attempt(task.id, summary="Great success.")
    store = _make_store(task=task, attempts=[attempt])
    gw = _make_gateway(store, tasks_from_planner=[task])

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    assert "Great success." in chunks


async def test_gateway_handles_planner_error():
    """PlannerError must be caught; gateway yields an error chunk, not a traceback."""
    store = _make_store()
    gw = _make_gateway(store)

    planner_error = AsyncMock(side_effect=PlannerError("model overloaded"))
    # Use a tier-3 message (contains "architecture" keyword) so the planner is invoked
    msg = _make_msg("Design the full system architecture for a microservices platform")

    with patch("backend.orchestrator.gateway.generate_tasks", planner_error):
        chunks = [c async for c in gw.handle_message(msg)]

    assert len(chunks) == 1
    assert "Planner error" in chunks[0]
    assert "model overloaded" in chunks[0]
    # Mission was still saved before the planner was called
    store.missions.save.assert_awaited_once()


async def test_gateway_works_without_adaptive_store():
    """Gateway must function correctly when adaptive_store=None."""
    task = _make_task()
    attempt = _make_attempt(task.id, summary="Done without adaptation.")
    store = _make_store(task=task, attempts=[attempt])
    gw = _make_gateway(store, tasks_from_planner=[task], adaptive_store=None)

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    assert "Done without adaptation." in chunks


async def test_gateway_yields_nothing_when_no_tasks():
    """An empty task list produces no response chunks (but no exception either)."""
    store = _make_store()
    gw = _make_gateway(store, tasks_from_planner=[])

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    assert chunks == []


async def test_gateway_run_task_exception_yields_error_chunk():
    """If run_task raises, gateway yields an error chunk and continues."""
    task = _make_task()
    store = _make_store(task=task, attempts=[])
    gw = _make_gateway(
        store,
        tasks_from_planner=[task],
        run_task_side_effect=RuntimeError("worker crashed"),
    )

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    assert any("failed" in c.lower() or "worker crashed" in c for c in chunks)


async def test_gateway_plan_and_run_are_saved():
    """Gateway must persist a Plan and a Run for each message."""
    task = _make_task()
    store = _make_store(task=task, attempts=[_make_attempt(task.id)])
    gw = _make_gateway(store, tasks_from_planner=[task])

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        _ = [c async for c in gw.handle_message(msg)]

    store.missions.save_plan.assert_awaited_once()
    store.missions.save_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_sets_preferred_worker_for_ollama_backend(store, tmp_path):
    """When active_backend is ollama, gateway sets preferred_worker_type on tasks."""
    from backend.orchestrator.config import MahoragaConfig
    from backend.orchestrator.workers.registry import WorkerRegistry
    from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
    from backend.orchestrator.domain.models import Task, TaskAttempt
    from backend.orchestrator.gateway import Gateway
    from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
    from backend.orchestrator.channels.base import ChannelMessage
    from typing import AsyncIterator
    from unittest.mock import AsyncMock, MagicMock, patch

    # Config pointing to ollama
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"active_backend": "ollama", "ollama_base_url": "http://localhost:11434"}')
    cfg = MahoragaConfig(path=cfg_path)

    # Worker that accepts any task
    class _AnyWorker(WorkerAdapter):
        @property
        def id(self): return "ollama:general"
        @property
        def capabilities(self): return ["general", "code_generation", "analysis"]
        async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
            yield WorkerEvent("attempt.completed", {"summary": "done"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id="ollama:general", healthy=True)

    registry = WorkerRegistry()
    registry.register(_AnyWorker())

    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )

    saved_tasks: list[Task] = []
    original_save = store.tasks.save

    async def capture_save(task):
        saved_tasks.append(task)
        return await original_save(task)

    store.tasks.save = capture_save

    # Patch generate_tasks to return a single code task
    with patch(
        "backend.orchestrator.gateway.generate_tasks",
        new_callable=AsyncMock,
        return_value=[
            Task.new(run_id="__pending__", title="Write function", goal="implement fibonacci")
        ],
    ):
        gw = Gateway(store=store, registry=registry, verifier=verifier, config=cfg)
        msg = ChannelMessage.new(user_id="test", channel="web", text="write fibonacci")
        chunks = [c async for c in gw.handle_message(msg)]

    assert any(t.preferred_worker_type is not None for t in saved_tasks), \
        "Expected gateway to set preferred_worker_type for ollama tasks"
    assert saved_tasks[0].preferred_worker_type == "ollama:qwen3-4b:coder"


@pytest.mark.asyncio
async def test_response_assembler_uses_summary_fallback():
    """Gateway must yield worker output even when attempt.output is empty (legacy DB rows)."""
    task = _make_task()
    attempt = _make_attempt(task.id, summary="4", output="")  # output="" simulates legacy DB row
    store = _make_store(task=task, attempts=[attempt])
    gw = _make_gateway(store, tasks_from_planner=[task])

    msg = _make_msg("whats 2+2")
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    assert "4" in chunks, f"Expected '4' in output chunks, got: {chunks}"


@pytest.mark.asyncio
async def test_gateway_uses_adapter_registry_for_routing():
    """When an AdapterRegistry is provided, gateway routes via capability matching."""
    from backend.orchestrator.adapters.registry import AdapterRegistry
    from backend.orchestrator.adapters.base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

    class _MockOllamaAdapter(AgentAdapter):
        @property
        def name(self): return "ollama"
        @property
        def worker_id(self): return "ollama:coder"
        @property
        def capabilities(self): return [AgentCapability("code", 0.9)]
        def estimate_cost(self, task): return CostEstimate()
        async def health_check(self): return AgentStatus(name="ollama", available=True)

    adapter_registry = AdapterRegistry()
    adapter_registry.register(_MockOllamaAdapter())

    task = _make_task()
    store = _make_store(task=task, attempts=[_make_attempt(task.id)])
    gw = _make_gateway(store, tasks_from_planner=[task], adapter_registry=adapter_registry)

    msg = _make_msg("write a hello world function")
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]
    # No assertion on content — just verify no exception raised when adapter_registry is provided


@pytest.mark.asyncio
async def test_gateway_observe_fires_on_run_task_exception():
    """Regression: observe() must be called even when run_task raises.

    Previously, the `continue` in the except block skipped observe(), leaking
    37% of rewards from the bandit decision log.
    """
    from backend.orchestrator.routing.reward import TaskOutcome

    task = _make_task()
    store = _make_store(task=task, attempts=[])

    bandit_router = MagicMock()
    bandit_router.observe = MagicMock()

    gw = _make_gateway(
        store,
        tasks_from_planner=[task],
        run_task_side_effect=RuntimeError("simulated failure"),
    )
    gw._bandit_router = bandit_router

    msg = _make_msg()
    with gw._planner_patch, gw._run_task_patch:
        chunks = [c async for c in gw.handle_message(msg)]

    # Error chunk still yielded to the user
    assert any("failed" in c.lower() or "simulated failure" in c for c in chunks)

    # observe() must have been called exactly once with success=False
    bandit_router.observe.assert_called_once()
    _, outcome = bandit_router.observe.call_args[0]
    assert isinstance(outcome, TaskOutcome)
    assert outcome.success is False
    assert outcome.quality_score == 0.0
