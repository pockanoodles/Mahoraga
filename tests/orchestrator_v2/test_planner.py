from __future__ import annotations
import json
import pytest
import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.planning.planner import generate_tasks, OllamaUnavailable, PlannerError
from backend.orchestrator.domain.models import Mission, Task


def make_mission(**kwargs) -> Mission:
    m = Mission.new(title="Build REST API", goal="Create user auth endpoints",
                    success_condition="All endpoints return correct responses")
    return dataclasses.replace(m, **kwargs) if kwargs else m


def _mock_ollama_response(tasks: list[dict]) -> MagicMock:
    """Build a mock httpx.AsyncClient that returns a planner response."""
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "message": {
                "role": "assistant",
                "content": json.dumps({"tasks": tasks}),
            }
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_generate_tasks_returns_task_list():
    raw = [
        {"title": "Set up project", "goal": "Create directories", "dependencies": [], "done_criteria": "Dirs exist"},
        {"title": "Write code", "goal": "Implement auth", "dependencies": ["Set up project"], "done_criteria": "Tests pass"},
    ]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="run_1")

    assert len(tasks) == 2
    assert tasks[0].title == "Set up project"
    assert tasks[1].title == "Write code"
    assert tasks[1].dependencies[0].task_id == tasks[0].id


@pytest.mark.asyncio
async def test_generate_tasks_sets_run_id():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="my_run")

    assert all(t.run_id == "my_run" for t in tasks)


@pytest.mark.asyncio
async def test_generate_tasks_context_refs_empty():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="r1")

    assert tasks[0].context_refs == []


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_ollama_unavailable():
    import httpx
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=client):
        with pytest.raises(OllamaUnavailable):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_invalid_json():
    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "message": {"role": "assistant", "content": "not json at all"}
        })
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=client):
        with pytest.raises(PlannerError, match="[Pp]arse"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_validation_failure():
    # Cycle: A depends on B, B depends on A
    raw = [
        {"title": "A", "goal": "Do A", "dependencies": ["B"], "done_criteria": "done"},
        {"title": "B", "goal": "Do B", "dependencies": ["A"], "done_criteria": "done"},
    ]
    mock_client = _mock_ollama_response(raw)
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(PlannerError, match="[Vv]alidat"):
            await generate_tasks(make_mission(), run_id="r1")
