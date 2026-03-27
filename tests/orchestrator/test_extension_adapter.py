import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from backend.orchestrator_svc.models import Task
from backend.workers.extension_adapter import ExtensionAdapter, EXTENSION_URL


def test_extension_adapter_has_correct_ids():
    adapter = ExtensionAdapter()
    assert adapter.worker_id == "extension"
    assert adapter.display_name == "Ollama Extension Worker"


async def test_submit_task_returns_task_id_immediately(monkeypatch):
    """submit_task fires off a background task and returns task.id without waiting."""
    adapter = ExtensionAdapter()
    completed = []

    async def fake_run_task(task):
        completed.append(task.id)

    monkeypatch.setattr(adapter, "_run_task", fake_run_task)

    task = Task.new(
        title="Fix bug", goal="Add test for login", task_type="code",
        context={"workspace": "/tmp/proj"}
    )
    returned_id = await adapter.submit_task(task)
    assert returned_id == task.id


async def test_health_ok_when_extension_responds_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("backend.workers.extension_adapter.httpx.AsyncClient", return_value=mock_client):
        adapter = ExtensionAdapter()
        result = await adapter.health()
        assert result["status"] == "ok"
        assert result["worker_id"] == "extension"


async def test_health_down_when_extension_unreachable():
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("backend.workers.extension_adapter.httpx.AsyncClient", return_value=mock_client):
        adapter = ExtensionAdapter()
        result = await adapter.health()
        assert result["status"] == "down"
        assert "error" in result


async def test_get_result_raises_when_not_ready():
    adapter = ExtensionAdapter()
    with pytest.raises(RuntimeError, match="not ready"):
        await adapter.get_result("nonexistent-task-id")
