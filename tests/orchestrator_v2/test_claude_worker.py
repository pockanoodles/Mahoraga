# tests/orchestrator_v2/test_claude_worker.py
import dataclasses
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from backend.orchestrator.workers.claude import ClaudeWorker
from backend.orchestrator.workers.base import WorkerEvent, _build_prompt
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Fix auth", goal="Fix the login bug")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(worker_id="claude:sonnet") -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id=worker_id)


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


# ── identity ──────────────────────────────────────────────────────────────────

def test_sonnet_worker_id():
    w = ClaudeWorker(api_key="fake")
    assert w.id == "claude:sonnet"


def test_opus_worker_id():
    w = ClaudeWorker(api_key="fake", model="claude-opus-4-6", worker_id="claude:opus",
                     capabilities=["complex_reasoning", "deep_reasoning", "general"])
    assert w.id == "claude:opus"


def test_sonnet_default_capabilities():
    w = ClaudeWorker(api_key="fake")
    assert "deep_reasoning" in w.capabilities
    assert "general" in w.capabilities


def test_opus_capabilities():
    w = ClaudeWorker(api_key="fake", model="claude-opus-4-6", worker_id="claude:opus",
                     capabilities=["complex_reasoning", "deep_reasoning", "general"])
    assert "complex_reasoning" in w.capabilities


# ── _build_prompt (regression) ────────────────────────────────────────────────

def test_build_prompt_includes_goal():
    task = make_task(goal="Fix the login redirect bug")
    prompt = _build_prompt(task)
    assert "Fix the login redirect bug" in prompt


def test_build_prompt_includes_done_criteria():
    task = make_task(done_criteria="All auth tests pass")
    prompt = _build_prompt(task)
    assert "All auth tests pass" in prompt


# ── first execute (no feedback) ───────────────────────────────────────────────

async def test_execute_yields_completed_on_success():
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(return_value=_mock_response("I fixed the bug"))):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "I fixed the bug" in completed[0].payload["summary"]


async def test_execute_first_call_sends_single_user_message():
    captured = {}
    async def fake_to_thread(fn, **kwargs):
        captured.update(kwargs)
        return _mock_response("result")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]

    messages = captured["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert task.goal in messages[0]["content"]


# ── retry with feedback ───────────────────────────────────────────────────────

async def test_execute_retry_appends_feedback_to_history():
    call_messages = []

    async def fake_to_thread(fn, **kwargs):
        call_messages.append(list(kwargs["messages"]))
        return _mock_response("attempt output")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        attempt1 = make_attempt()
        attempt2 = make_attempt()

        # First call
        _ = [ev async for ev in w.execute(attempt1, task, feedback=None)]
        # Retry call with feedback
        _ = [ev async for ev in w.execute(attempt2, task, feedback="Missing X, add Y")]

    # First call: 1 message
    assert len(call_messages[0]) == 1
    # Retry call: 3 messages [user:prompt, assistant:prior_output, user:feedback]
    assert len(call_messages[1]) == 3
    assert call_messages[1][1]["role"] == "assistant"
    assert call_messages[1][1]["content"] == "attempt output"
    assert call_messages[1][2]["role"] == "user"
    assert "Missing X, add Y" in call_messages[1][2]["content"]


async def test_execute_second_retry_has_five_messages():
    """Two retries → history grows: [user, assistant, user, assistant, user]."""
    call_messages = []
    call_count = [0]

    async def fake_to_thread(fn, **kwargs):
        call_messages.append(list(kwargs["messages"]))
        call_count[0] += 1
        return _mock_response(f"output {call_count[0]}")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="first feedback")]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="second feedback")]

    assert len(call_messages[2]) == 5


# ── clear_history ─────────────────────────────────────────────────────────────

async def test_clear_history_resets_task_state():
    async def fake_to_thread(fn, **kwargs):
        return _mock_response("output")

    call_messages = []

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        w.clear_history(task.id)

    async def capture(fn, **kwargs):
        call_messages.append(kwargs["messages"])
        return _mock_response("fresh")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=capture):
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="ignored after clear")]

    assert len(call_messages[0]) == 1


# ── error paths ───────────────────────────────────────────────────────────────

async def test_execute_yields_failed_on_empty_response():
    resp = MagicMock()
    resp.content = []
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", new=AsyncMock(return_value=resp)):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "empty_response"


async def test_execute_yields_failed_on_api_error():
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(side_effect=Exception("API error"))):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "api_error"


async def test_health_returns_healthy():
    w = ClaudeWorker(api_key="fake")
    h = await w.health()
    assert h.healthy is True
