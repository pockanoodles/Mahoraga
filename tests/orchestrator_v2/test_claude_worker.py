import dataclasses
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.orchestrator.workers.claude import ClaudeWorker, _build_prompt
from backend.orchestrator.workers.base import WorkerEvent
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Fix auth", goal="Fix the login bug")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(worker_id="claude") -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id=worker_id)


def test_claude_worker_id():
    w = ClaudeWorker(api_key="fake")
    assert w.id == "claude"


def test_claude_worker_capabilities():
    w = ClaudeWorker(api_key="fake")
    assert "deep_reasoning" in w.capabilities
    assert "planning" in w.capabilities
    assert "file_editing" in w.capabilities
    assert "review" in w.capabilities


def test_build_prompt_includes_goal():
    task = make_task(goal="Fix the login redirect bug")
    prompt = _build_prompt(task)
    assert "Fix the login redirect bug" in prompt


def test_build_prompt_includes_context_refs():
    task = make_task(context_refs=["src/auth.py", "tests/test_auth.py"])
    prompt = _build_prompt(task)
    assert "src/auth.py" in prompt
    assert "tests/test_auth.py" in prompt


def test_build_prompt_includes_constraints():
    task = make_task(constraints=["do not modify public API"])
    prompt = _build_prompt(task)
    assert "do not modify public API" in prompt


def test_build_prompt_includes_done_criteria():
    task = make_task(done_criteria="All auth tests pass")
    prompt = _build_prompt(task)
    assert "All auth tests pass" in prompt


def test_build_prompt_omits_empty_context_refs():
    task = make_task(context_refs=[], constraints=[])
    prompt = _build_prompt(task)
    # Should not crash or include empty sections
    assert "## Goal" in prompt


async def test_execute_yields_completed_on_success():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="I fixed the bug")]

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", new=AsyncMock(return_value=mock_response)):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        attempt = make_attempt()
        events = [ev async for ev in w.execute(attempt, task)]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "I fixed the bug" in completed[0].payload.get("summary", "")


async def test_execute_yields_failed_on_empty_response():
    mock_response = MagicMock()
    mock_response.content = []

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", new=AsyncMock(return_value=mock_response)):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        attempt = make_attempt()
        events = [ev async for ev in w.execute(attempt, task)]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("error_code") == "empty_response"


async def test_execute_yields_failed_on_api_error():
    with patch(
        "backend.orchestrator.workers.claude.asyncio.to_thread",
        new=AsyncMock(side_effect=Exception("API error")),
    ):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        attempt = make_attempt()
        events = [ev async for ev in w.execute(attempt, task)]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("error_code") == "api_error"


async def test_health_returns_healthy():
    w = ClaudeWorker(api_key="fake")
    h = await w.health()
    assert h.worker_id == "claude"
    assert h.healthy is True
