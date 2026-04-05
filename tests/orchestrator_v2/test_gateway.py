"""Tests for backend.orchestrator.gateway.Gateway."""
from __future__ import annotations

import time
import uuid
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


def _make_attempt(task_id: str, summary: str = "Task done.") -> TaskAttempt:
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
                  adaptive_store=None) -> Gateway:
    registry = MagicMock()
    verifier = MagicMock()
    gw = Gateway(
        store=store,
        registry=registry,
        verifier=verifier,
        adaptive_store=adaptive_store,
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
    msg = _make_msg()

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
