import json
import pytest
from unittest.mock import patch, AsyncMock
from backend.orchestrator import run
from backend.models import Complexity


def _fake_classify(complexity: str, task_type: str = "code"):
    """Return an async mock that resolves to a Classification."""
    from backend.models import Classification, TaskType
    result = Classification(complexity=Complexity(complexity), task_type=TaskType(task_type))
    return AsyncMock(return_value=result)


def _fake_verify(verdict: str, corrections: str = ""):
    payload = {"verdict": verdict}
    if corrections:
        payload["corrections"] = corrections
    return AsyncMock(return_value=payload)


async def _agent_events(*events):
    """Async generator that yields the given event dicts."""
    for e in events:
        yield e


@pytest.mark.asyncio
async def test_run_simple_skips_verify(tmp_path):
    ws = str(tmp_path)

    agent_output = [
        {"type": "token", "content": "done"},
        {"type": "done"},
    ]

    with (
        patch("backend.orchestrator.classify", _fake_classify("simple")),
        patch("backend.orchestrator.run_agent", return_value=_agent_events(*agent_output)),
        patch("backend.orchestrator.verify") as mock_verify,
    ):
        events = [e async for e in run("fix typo", ws, [])]

    # verify must NOT be called for simple tasks
    mock_verify.assert_not_called()
    assert any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
async def test_run_medium_calls_verify_and_accepts(tmp_path):
    ws = str(tmp_path)

    agent_output = [
        {"type": "token", "content": "fixed it"},
        {"type": "done"},
    ]

    with (
        patch("backend.orchestrator.classify", _fake_classify("medium")),
        patch("backend.orchestrator.run_agent", return_value=_agent_events(*agent_output)),
        patch("backend.orchestrator.verify", _fake_verify("ACCEPT")),
    ):
        events = [e async for e in run("refactor auth", ws, [])]

    assert any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
async def test_run_emits_model_event(tmp_path):
    ws = str(tmp_path)

    agent_output = [{"type": "done"}]

    with (
        patch("backend.orchestrator.classify", _fake_classify("simple")),
        patch("backend.orchestrator.run_agent", return_value=_agent_events(*agent_output)),
    ):
        events = [e async for e in run("do something", ws, [])]

    model_events = [e for e in events if e["type"] == "model"]
    assert len(model_events) == 1
    assert model_events[0]["model"] == "qwen2.5-coder:7b"


@pytest.mark.asyncio
async def test_run_retries_on_revise(tmp_path):
    ws = str(tmp_path)

    call_count = 0

    async def mock_agent(model, messages, workspace, **kwargs):
        nonlocal call_count
        call_count += 1
        yield {"type": "token", "content": f"attempt {call_count}"}
        yield {"type": "done"}

    verify_responses = [
        {"verdict": "REVISE", "corrections": "missing error handling"},
        {"verdict": "ACCEPT"},
    ]
    verify_iter = iter(verify_responses)

    async def mock_verify(msg, resp):
        return next(verify_iter)

    with (
        patch("backend.orchestrator.classify", _fake_classify("medium")),
        patch("backend.orchestrator.run_agent", side_effect=mock_agent),
        patch("backend.orchestrator.verify", mock_verify),
    ):
        events = [e async for e in run("write function", ws, [])]

    assert call_count == 2  # first attempt + one retry
    assert any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
async def test_run_escalates_after_three_failures(tmp_path):
    ws = str(tmp_path)

    models_used = []

    async def mock_agent(model, messages, workspace, **kwargs):
        models_used.append(model)
        yield {"type": "token", "content": "attempt"}
        yield {"type": "done"}

    async def always_revise(msg, resp):
        return {"verdict": "REVISE", "corrections": "still wrong"}

    with (
        patch("backend.orchestrator.classify", _fake_classify("medium")),
        patch("backend.orchestrator.run_agent", side_effect=mock_agent),
        patch("backend.orchestrator.verify", always_revise),
    ):
        events = [e async for e in run("hard task", ws, [])]

    # Should escalate on third failure
    assert "qwen2.5-coder:14b" in models_used
    assert any(e["type"] == "model" and e["model"] == "qwen3:14b" for e in events)
