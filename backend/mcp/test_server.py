"""Unit tests for MCP server tool handlers."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_handle_run_task_basic():
    from backend.mcp.server import _handle_run_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success", "agent": "ollama"}
        result = await _handle_run_task({"prompt": "create test.py"})
        mock.assert_called_once_with("/api/task", {"prompt": "create test.py"})
        assert result["status"] == "success"


@pytest.mark.asyncio
async def test_handle_run_task_with_overrides():
    from backend.mcp.server import _handle_run_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success"}
        await _handle_run_task({
            "prompt": "research JWT",
            "capability_hint": "general",
            "agent_override": "gemini-cli",
        })
        mock.assert_called_once_with("/api/task", {
            "prompt": "research JWT",
            "capability_hint": "general",
            "agent_override": "gemini-cli",
        })


@pytest.mark.asyncio
async def test_handle_route_task():
    from backend.mcp.server import _handle_route_task
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"bandit_selection": {"selected_agent": "aider"}}
        await _handle_route_task({"prompt": "refactor auth"})
        mock.assert_called_once_with("/api/routing/dry-run", {"prompt": "refactor auth"})


@pytest.mark.asyncio
async def test_handle_agent_status_merges_responses():
    from backend.mcp.server import _handle_agent_status
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            [{"name": "ollama", "available": True}],
            {"local_ollama": {"max_concurrent": 1}},
        ]
        result = await _handle_agent_status({})
        assert "agents" in result
        assert "resource_groups" in result


@pytest.mark.asyncio
async def test_handle_not_running_returns_error():
    from backend.mcp.server import _handle_routing_stats
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"error": "Mahoraga is not running. Start it with: ..."}
        result = await _handle_routing_stats({})
        assert "error" in result


@pytest.mark.asyncio
async def test_handle_switch_strategy():
    from backend.mcp.server import _handle_switch_strategy
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"strategy": "thompson"}
        await _handle_switch_strategy({"strategy": "thompson"})
        mock.assert_called_once_with("/api/routing/strategy", {"strategy": "thompson"})


@pytest.mark.asyncio
async def test_handle_recent_decisions_with_agent_filter():
    from backend.mcp.server import _handle_recent_decisions
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"decisions": [], "total_available": 0}
        await _handle_recent_decisions({"limit": 5, "agent_filter": "aider"})
        mock.assert_called_once_with(
            "/api/routing/decisions", {"limit": 5, "agent": "aider"}
        )
