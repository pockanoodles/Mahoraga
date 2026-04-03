import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from backend.orchestrator.workers.ollama import OllamaWorker, _build_prompt
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Refactor auth", goal="Extract login to service")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt() -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id="ollama:qwen3:8b")


def _mock_chat_client(content: str | None = "done it", raise_exc: Exception | None = None) -> MagicMock:
    """Build a mock httpx.AsyncClient context manager for /api/chat."""
    async def fake_post(url, **kwargs):
        if raise_exc:
            raise raise_exc
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": content or ""},
            "done": True,
        })
        return resp

    async def fake_get(url, **kwargs):
        if raise_exc:
            raise raise_exc
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.get = AsyncMock(side_effect=fake_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def test_id_includes_model():
    w = OllamaWorker(model="qwen3:8b")
    assert w.id == "ollama:qwen3:8b"


def test_id_uses_configured_model():
    w = OllamaWorker(model="llama3.2:3b")
    assert w.id == "ollama:llama3.2:3b"


def test_capabilities():
    w = OllamaWorker()
    assert "file_editing" in w.capabilities
    assert "general" in w.capabilities
    assert "cheap_repetitive" in w.capabilities


def test_build_prompt_includes_goal():
    task = make_task(goal="Extract login to a service layer")
    prompt = _build_prompt(task)
    assert "Extract login to a service layer" in prompt


def test_build_prompt_includes_title():
    task = make_task(title="Refactor auth")
    prompt = _build_prompt(task)
    assert "Refactor auth" in prompt


def test_build_prompt_includes_context_refs():
    task = make_task(context_refs=["src/auth.py", "tests/test_auth.py"])
    prompt = _build_prompt(task)
    assert "src/auth.py" in prompt
    assert "tests/test_auth.py" in prompt


def test_build_prompt_includes_constraints():
    task = make_task(constraints=["do not break existing API"])
    prompt = _build_prompt(task)
    assert "do not break existing API" in prompt


def test_build_prompt_includes_done_criteria():
    task = make_task(done_criteria="All tests pass with no regressions")
    prompt = _build_prompt(task)
    assert "All tests pass with no regressions" in prompt


async def test_execute_yields_completed_on_success():
    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _mock_chat_client(content="I refactored it")
        w = OllamaWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "I refactored it" in completed[0].payload["summary"]


async def test_execute_yields_failed_on_empty_content():
    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _mock_chat_client(content="")
        w = OllamaWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "empty_response"


async def test_execute_yields_failed_on_missing_message_key():
    """Ollama response without 'message' key still fails cleanly."""
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"done": True})  # no "message" key
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = OllamaWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "empty_response"


async def test_execute_yields_failed_on_http_error():
    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _mock_chat_client(
            raise_exc=httpx.ConnectError("connection refused")
        )
        w = OllamaWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "http_error"
    assert "connection refused" in failed[0].payload["error"]


async def test_health_returns_healthy_when_ollama_up():
    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _mock_chat_client()
        w = OllamaWorker()
        h = await w.health()

    assert h.worker_id == "ollama:qwen3:8b"
    assert h.healthy is True


async def test_health_returns_unhealthy_when_ollama_down():
    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _mock_chat_client(raise_exc=httpx.ConnectError("refused"))
        w = OllamaWorker()
        h = await w.health()

    assert h.worker_id == "ollama:qwen3:8b"
    assert h.healthy is False
    assert "refused" in h.detail


async def test_execute_first_call_sends_single_user_message():
    """First call sends exactly 1 message (the task prompt)."""
    captured_payload = {}

    async def fake_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Task completed"},
            "done": True,
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = OllamaWorker()
        task = make_task()
        attempt = make_attempt()
        events = [ev async for ev in w.execute(attempt, task)]

    assert len(captured_payload["messages"]) == 1
    assert captured_payload["messages"][0]["role"] == "user"


async def test_execute_retry_appends_feedback_to_history():
    """Retry with feedback: payload has 3 messages [user, assistant, user]."""
    captured_payloads = []
    call_count = [0]

    async def fake_post(url, **kwargs):
        call_count[0] += 1
        # Deep copy to avoid mutation issues
        messages = [dict(m) for m in kwargs.get("json", {})["messages"]]
        captured_payloads.append(messages)

        # Return different content on first vs second call
        content = "Task completed" if call_count[0] == 1 else "Fixed based on feedback"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": content},
            "done": True,
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = OllamaWorker()
        task = make_task()
        attempt = make_attempt()

        # First call
        events = [ev async for ev in w.execute(attempt, task)]

        # Retry with feedback
        events = [ev async for ev in w.execute(attempt, task, feedback="Missing X")]

    # First call should have 1 message
    assert len(captured_payloads[0]) == 1
    assert captured_payloads[0][0]["role"] == "user"

    # Second call should have 3 messages
    assert len(captured_payloads[1]) == 3
    assert captured_payloads[1][0]["role"] == "user"
    assert captured_payloads[1][1]["role"] == "assistant"
    assert captured_payloads[1][1]["content"] == "Task completed"
    assert captured_payloads[1][2]["role"] == "user"
    assert captured_payloads[1][2]["content"] == "Missing X"


async def test_execute_second_retry_has_five_messages():
    """Two retries → 5 messages in history."""
    captured_payloads = []

    async def fake_post(url, **kwargs):
        captured_payloads.append(kwargs.get("json", {})["messages"][:])
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Updated response"},
            "done": True,
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = OllamaWorker()
        task = make_task()
        attempt = make_attempt()

        # First call
        events = [ev async for ev in w.execute(attempt, task)]

        # First retry
        events = [ev async for ev in w.execute(attempt, task, feedback="First feedback")]

        # Second retry
        events = [ev async for ev in w.execute(attempt, task, feedback="Second feedback")]

    # Verify progression: 1, 3, 5 messages
    assert len(captured_payloads[0]) == 1
    assert len(captured_payloads[1]) == 3
    assert len(captured_payloads[2]) == 5
    # Final state: user, assistant, user, assistant, user
    assert captured_payloads[2][0]["role"] == "user"
    assert captured_payloads[2][1]["role"] == "assistant"
    assert captured_payloads[2][2]["role"] == "user"
    assert captured_payloads[2][3]["role"] == "assistant"
    assert captured_payloads[2][4]["role"] == "user"


async def test_clear_history_resets_task_state():
    """After clear_history(), next call with feedback is treated as fresh (1 message)."""
    captured_payloads = []

    async def fake_post(url, **kwargs):
        captured_payloads.append(kwargs.get("json", {})["messages"][:])
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Fresh start"},
            "done": True,
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = OllamaWorker()
        task = make_task()
        attempt = make_attempt()

        # First call
        events = [ev async for ev in w.execute(attempt, task)]

        # Retry with feedback
        events = [ev async for ev in w.execute(attempt, task, feedback="Try again")]

        # Clear history
        w.clear_history(task.id)

        # Next call with feedback should be treated as first call (1 message)
        events = [ev async for ev in w.execute(attempt, task, feedback="New feedback")]

    # Before clear: 1, 3 messages
    assert len(captured_payloads[0]) == 1
    assert len(captured_payloads[1]) == 3

    # After clear with feedback: should be 1 message (treated as first call)
    assert len(captured_payloads[2]) == 1
    assert captured_payloads[2][0]["role"] == "user"
