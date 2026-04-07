from __future__ import annotations
import json
import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.orchestrator.planning.planner import generate_tasks, PlannerError
from backend.orchestrator.domain.models import Mission, Task


# Force Ollama path for all tests — tests don't need a real Anthropic API key
@pytest.fixture(autouse=True)
def force_ollama_backend(monkeypatch):
    monkeypatch.setattr("backend.orchestrator.planning.planner.ENABLED_BACKENDS", ["ollama"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mission(**kwargs) -> Mission:
    m = Mission.new(
        title="Build REST API",
        goal="Create user auth endpoints",
        success_condition="All endpoints return correct responses",
    )
    return dataclasses.replace(m, **kwargs) if kwargs else m


def _mock_ollama_response(tasks: list[dict]) -> MagicMock:
    """Return a mock httpx.Response that mimics an Ollama /api/chat reply."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "message": {"content": json.dumps(tasks)}
    })
    return resp


def _patch_httpx(response: MagicMock):
    """Context-manager helper: patch httpx.AsyncClient so .post() returns response."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=response)
    return patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_tasks_returns_task_list():
    raw = [
        {"title": "Set up project", "goal": "Create directories", "dependencies": [], "done_criteria": "Dirs exist"},
        {"title": "Write code", "goal": "Implement auth", "dependencies": ["Set up project"], "done_criteria": "Tests pass"},
    ]
    with _patch_httpx(_mock_ollama_response(raw)):
        tasks = await generate_tasks(make_mission(), run_id="run_1")

    assert len(tasks) == 2
    assert tasks[0].title == "Set up project"
    assert tasks[1].title == "Write code"
    assert tasks[1].dependencies[0].task_id == tasks[0].id


@pytest.mark.asyncio
async def test_generate_tasks_single_task_mission():
    raw = [{"title": "Do it all", "goal": "Complete the work", "done_criteria": "Done"}]
    with _patch_httpx(_mock_ollama_response(raw)):
        tasks = await generate_tasks(make_mission(), run_id="run_single")

    assert len(tasks) == 1
    assert tasks[0].title == "Do it all"
    assert tasks[0].dependencies == []


@pytest.mark.asyncio
async def test_generate_tasks_sets_run_id():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    with _patch_httpx(_mock_ollama_response(raw)):
        tasks = await generate_tasks(make_mission(), run_id="my_run")

    assert all(t.run_id == "my_run" for t in tasks)


@pytest.mark.asyncio
async def test_generate_tasks_context_refs_empty():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    with _patch_httpx(_mock_ollama_response(raw)):
        tasks = await generate_tasks(make_mission(), run_id="r1")

    assert tasks[0].context_refs == []


@pytest.mark.asyncio
async def test_generate_tasks_caps_at_max_tasks():
    from backend.orchestrator.planning.config import MAX_TASKS

    raw = [
        {"title": f"Task {i}", "goal": f"Goal {i}", "done_criteria": f"Done {i}"}
        for i in range(MAX_TASKS + 5)
    ]
    with _patch_httpx(_mock_ollama_response(raw)):
        tasks = await generate_tasks(make_mission(), run_id="r_cap")

    assert len(tasks) == MAX_TASKS


@pytest.mark.asyncio
async def test_generate_tasks_raises_planner_error_on_api_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("connection refused"))

    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(PlannerError):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_planner_error_on_http_status_error():
    bad_resp = MagicMock(spec=httpx.Response)
    bad_resp.status_code = 500
    bad_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("server error", request=MagicMock(), response=bad_resp)
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=bad_resp)

    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(PlannerError):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_invalid_json():
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"message": {"content": "not json at all"}})

    with _patch_httpx(resp):
        with pytest.raises(PlannerError, match="[Pp]arse"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_validation_failure():
    # Cycle: A depends on B, B depends on A
    raw = [
        {"title": "A", "goal": "Do A", "dependencies": ["B"], "done_criteria": "done"},
        {"title": "B", "goal": "Do B", "dependencies": ["A"], "done_criteria": "done"},
    ]
    with _patch_httpx(_mock_ollama_response(raw)):
        with pytest.raises(PlannerError, match="[Vv]alidat"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_strips_markdown_fences():
    raw = [{"title": "T1", "goal": "Do it", "done_criteria": "Done"}]
    fenced = f"```json\n{json.dumps(raw)}\n```"

    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"message": {"content": fenced}})

    with _patch_httpx(resp):
        tasks = await generate_tasks(make_mission(), run_id="r_fence")

    assert len(tasks) == 1
    assert tasks[0].title == "T1"
