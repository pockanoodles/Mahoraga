from __future__ import annotations
import json
import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic

from backend.orchestrator.planning.planner import generate_tasks, PlannerError
from backend.orchestrator.domain.models import Mission, Task


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


def _mock_anthropic_response(tasks: list[dict]) -> MagicMock:
    """Return a mock anthropic.AsyncAnthropic client that yields a planner response."""
    message = MagicMock()
    message.content = [MagicMock(text=json.dumps(tasks))]

    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=message)

    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_tasks_returns_task_list():
    raw = [
        {"title": "Set up project", "goal": "Create directories", "dependencies": [], "done_criteria": "Dirs exist"},
        {"title": "Write code", "goal": "Implement auth", "dependencies": ["Set up project"], "done_criteria": "Tests pass"},
    ]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="run_1")

    assert len(tasks) == 2
    assert tasks[0].title == "Set up project"
    assert tasks[1].title == "Write code"
    assert tasks[1].dependencies[0].task_id == tasks[0].id


@pytest.mark.asyncio
async def test_generate_tasks_single_task_mission():
    raw = [{"title": "Do it all", "goal": "Complete the work", "done_criteria": "Done"}]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="run_single")

    assert len(tasks) == 1
    assert tasks[0].title == "Do it all"
    assert tasks[0].dependencies == []


@pytest.mark.asyncio
async def test_generate_tasks_sets_run_id():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="my_run")

    assert all(t.run_id == "my_run" for t in tasks)


@pytest.mark.asyncio
async def test_generate_tasks_context_refs_empty():
    raw = [{"title": "T1", "goal": "Do it", "dependencies": [], "done_criteria": "Done"}]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="r1")

    assert tasks[0].context_refs == []


@pytest.mark.asyncio
async def test_generate_tasks_caps_at_max_tasks():
    from backend.orchestrator.planning.config import MAX_TASKS

    raw = [
        {"title": f"Task {i}", "goal": f"Goal {i}", "done_criteria": f"Done {i}"}
        for i in range(MAX_TASKS + 5)
    ]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        tasks = await generate_tasks(make_mission(), run_id="r_cap")

    assert len(tasks) == MAX_TASKS


@pytest.mark.asyncio
async def test_generate_tasks_raises_planner_error_on_api_error():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=anthropic.APIStatusError(
        "server error",
        response=MagicMock(status_code=500),
        body={},
    ))
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=client):
        with pytest.raises(PlannerError):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_invalid_json():
    message = MagicMock()
    message.content = [MagicMock(text="not json at all")]

    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=message)

    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=client):
        with pytest.raises(PlannerError, match="[Pp]arse"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_raises_on_validation_failure():
    # Cycle: A depends on B, B depends on A
    raw = [
        {"title": "A", "goal": "Do A", "dependencies": ["B"], "done_criteria": "done"},
        {"title": "B", "goal": "Do B", "dependencies": ["A"], "done_criteria": "done"},
    ]
    mock_client = _mock_anthropic_response(raw)
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=mock_client):
        with pytest.raises(PlannerError, match="[Vv]alidat"):
            await generate_tasks(make_mission(), run_id="r1")


@pytest.mark.asyncio
async def test_generate_tasks_strips_markdown_fences():
    raw = [{"title": "T1", "goal": "Do it", "done_criteria": "Done"}]
    fenced = f"```json\n{json.dumps(raw)}\n```"

    message = MagicMock()
    message.content = [MagicMock(text=fenced)]

    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=message)

    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic", return_value=client):
        tasks = await generate_tasks(make_mission(), run_id="r_fence")

    assert len(tasks) == 1
    assert tasks[0].title == "T1"
