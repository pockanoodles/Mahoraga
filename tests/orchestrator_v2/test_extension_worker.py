import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from backend.orchestrator.workers.extension import ExtensionWorker
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task() -> Task:
    return Task.new(
        run_id="r1", title="Add test", goal="Add a test for login",
        context_refs=["src/auth.py"], constraints=["tests only"],
        done_criteria="tests pass",
    )


def make_attempt() -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id="extension")


def _mock_client(responses: list[dict]) -> MagicMock:
    """Build a mock httpx.AsyncClient that returns given response sequence."""
    call_count = [0]

    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_get(url, **kwargs):
        i = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=responses[i])
        return resp

    async def fake_delete(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.get = AsyncMock(side_effect=fake_get)
    client.delete = AsyncMock(side_effect=fake_delete)
    client.aclose = AsyncMock()
    return client


def test_extension_worker_id():
    assert ExtensionWorker().id == "extension"


def test_extension_worker_capabilities():
    w = ExtensionWorker()
    assert "file_editing" in w.capabilities
    assert "cheap_repetitive" in w.capabilities


async def test_execute_completed_path():
    task = make_task()
    attempt = make_attempt()

    with patch("backend.orchestrator.workers.extension.asyncio.sleep", new=AsyncMock()):
        with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_client([{"status": "completed", "summary": "tests pass"}])
            w = ExtensionWorker()
            events = [ev async for ev in w.execute(attempt, task)]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert completed[0].payload.get("summary") == "tests pass"


async def test_execute_failed_path():
    task = make_task()
    attempt = make_attempt()

    with patch("backend.orchestrator.workers.extension.asyncio.sleep", new=AsyncMock()):
        with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_client([{"status": "failed", "error_code": "lint_error", "error": "lint failed"}])
            w = ExtensionWorker()
            events = [ev async for ev in w.execute(attempt, task)]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("error_code") == "lint_error"


async def test_execute_blocked_path():
    task = make_task()
    attempt = make_attempt()

    with patch("backend.orchestrator.workers.extension.asyncio.sleep", new=AsyncMock()):
        with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_client([{"status": "blocked", "reason": "merge conflict"}])
            w = ExtensionWorker()
            events = [ev async for ev in w.execute(attempt, task)]

    blocked = [e for e in events if e.type == "attempt.blocked"]
    assert len(blocked) == 1
    assert blocked[0].payload.get("reason") == "merge conflict"


async def test_execute_polls_until_terminal():
    task = make_task()
    attempt = make_attempt()

    with patch("backend.orchestrator.workers.extension.asyncio.sleep", new=AsyncMock()):
        with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
            # running × 2 then completed
            MockClient.return_value = _mock_client([
                {"status": "running"},
                {"status": "running"},
                {"status": "completed", "summary": "done"},
            ])
            w = ExtensionWorker()
            events = [ev async for ev in w.execute(attempt, task)]

    assert any(e.type == "attempt.completed" for e in events)


async def test_execute_post_error_yields_failed():
    task = make_task()
    attempt = make_attempt()

    async def bad_post(*a, **kw):
        raise httpx.ConnectError("refused")

    client = MagicMock()
    client.post = AsyncMock(side_effect=bad_post)
    client.aclose = AsyncMock()

    with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = ExtensionWorker()
        events = [ev async for ev in w.execute(attempt, task)]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("error_code") == "http_error"


async def test_health_ok():
    async def fake_get(*a, **kw):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.aclose = AsyncMock()

    with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = ExtensionWorker()
        h = await w.health()

    assert h.worker_id == "extension"
    assert h.healthy is True


async def test_health_down_on_error():
    async def bad_get(*a, **kw):
        raise httpx.ConnectError("refused")

    client = MagicMock()
    client.get = AsyncMock(side_effect=bad_get)
    client.aclose = AsyncMock()

    with patch("backend.orchestrator.workers.extension.httpx.AsyncClient") as MockClient:
        MockClient.return_value = client
        w = ExtensionWorker()
        h = await w.health()

    assert h.worker_id == "extension"
    assert h.healthy is False
    assert "refused" in h.detail
