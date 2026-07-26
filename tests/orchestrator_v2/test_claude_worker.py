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


# ── metrics emission (SDK arm) ────────────────────────────────────────────────

def _mock_response_with_usage(text: str, input_tokens: int, output_tokens: int,
                              cache_read: int = 0) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
    )
    return resp


async def test_execute_emits_metrics_with_cost_before_completed():
    """The SDK arm must report real cost like claude-cli does — otherwise the
    bandit's cost penalty prefers this arm for accounting reasons, not real ones."""
    from backend.orchestrator.tracking.pricing import calculate_cost

    resp = _mock_response_with_usage("I fixed the bug", input_tokens=1200,
                                     output_tokens=400, cache_read=50)
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(return_value=resp)):
        w = ClaudeWorker(api_key="fake", model="claude-sonnet-4-6")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert [e.type for e in events] == ["metrics", "attempt.completed"]
    m = events[0].payload
    assert m["tokens"] == 400
    assert m["prompt_tokens"] == 1200
    assert m["cache_read_tokens"] == 50
    assert m["model"] == "claude-sonnet-4-6"
    assert m["cost_usd"] == pytest.approx(
        calculate_cost("claude-sonnet-4-6", 1200, 400, 50)
    )
    assert "elapsed_s" in m and "throughput_tps" in m


async def test_execute_missing_cache_read_defaults_to_zero():
    resp = MagicMock()
    resp.content = [MagicMock(text="done")]
    resp.usage = MagicMock(spec=["input_tokens", "output_tokens"])
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 20
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(return_value=resp)):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    m = events[0].payload
    assert m["cache_read_tokens"] == 0
    assert m["prompt_tokens"] == 100


async def test_execute_unusable_usage_skips_metrics_but_completes():
    """A garbage usage object must not break the task — metrics are skipped."""
    resp = MagicMock()
    resp.content = [MagicMock(text="still done")]
    resp.usage = MagicMock(output_tokens="garbage", input_tokens="nope")  # int() raises
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(return_value=resp)):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert [e.type for e in events] == ["attempt.completed"]
    assert "still done" in events[0].payload["summary"]
