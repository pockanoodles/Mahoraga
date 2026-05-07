"""Tests for R1.1 — MCP server resilience hardening.

Covers the upgraded `_handle_health_check` (degradation ladder
composition) and the retry/backoff helpers. The HTTP layer is mocked
end-to-end so we never need a running FastAPI server.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.mcp import server as mcp_server


# ── retry helpers ─────────────────────────────────────────────────────────────


def test_retry_delay_returns_per_attempt_value():
    assert mcp_server._retry_delay(0) == 1.0
    assert mcp_server._retry_delay(1) == 3.0


def test_retry_delay_clamps_past_table_end():
    """Beyond the configured backoffs, just keep using the last value
    (don't crash on long-tail retries)."""
    assert mcp_server._retry_delay(99) == 3.0


# ── health_check degradation ladder ───────────────────────────────────────────


def _stub_responses(
    base: dict,
    routing: dict | None = None,
    agents: dict | None = None,
):
    """Build a side_effect that returns each endpoint's stub in order."""
    routing = routing if routing is not None else {}
    agents = agents if agents is not None else []

    async def fake_get(path: str, params: dict | None = None) -> dict:
        if path == "/api/health":
            return base
        if path == "/api/health/routing":
            return routing
        if path == "/api/agents/status":
            return agents
        raise AssertionError(f"unexpected path: {path}")

    return fake_get


@pytest.mark.asyncio
async def test_health_check_ok_when_all_systems_up():
    fake = _stub_responses(
        base={"status": "ok", "agents_registered": 5, "agents_online": 5},
        routing={
            "quarantine": {"entries": [], "n_drift_events_unresolved": 0},
            "budget_pacer": {"avg_cost": 0.01, "ceiling": 0.05},
            "execution_pool": {"depth_norm": 0.0},
        },
        agents=[{"name": "ollama"}],
    )
    with patch.object(mcp_server, "_get", side_effect=fake):
        result = await mcp_server._handle_health_check({})
    assert result["status"] == "ok"
    assert result["degradation_level"] == 0
    assert result["level_name"] == "ok"


@pytest.mark.asyncio
async def test_health_check_degraded_on_partial_agents_down():
    fake = _stub_responses(
        base={"status": "ok", "agents_registered": 5, "agents_online": 3},
        routing={
            "quarantine": {"entries": [], "n_drift_events_unresolved": 0},
            "budget_pacer": {"avg_cost": 0.01, "ceiling": 0.05},
            "execution_pool": {"depth_norm": 0.0},
        },
    )
    with patch.object(mcp_server, "_get", side_effect=fake):
        result = await mcp_server._handle_health_check({})
    assert result["status"] == "degraded"
    assert result["degradation_level"] == 2


@pytest.mark.asyncio
async def test_health_check_degraded_on_drift_quarantine():
    """All agents online but a quarantine fired → degraded, level 1."""
    fake = _stub_responses(
        base={"status": "ok", "agents_registered": 5, "agents_online": 5},
        routing={
            "quarantine": {
                "entries": [
                    {"bucket": "code", "agent": "ollama"},
                ],
                "n_drift_events_unresolved": 1,
            },
            "budget_pacer": {"avg_cost": 0.01, "ceiling": 0.05},
            "execution_pool": {"depth_norm": 0.0},
        },
    )
    with patch.object(mcp_server, "_get", side_effect=fake):
        result = await mcp_server._handle_health_check({})
    assert result["status"] == "degraded"
    assert result["degradation_level"] == 1
    assert result["level_name"] == "agent_drift"
    assert result["quarantined_agents"] == ["code/ollama"]
    assert result["drift_alerts_active"] == 1


@pytest.mark.asyncio
async def test_health_check_down_when_fastapi_unreachable():
    """The base /api/health returning an error short-circuits the
    whole health check to status=down without making the other calls."""
    async def fake_get(path: str, params: dict | None = None) -> dict:
        if path == "/api/health":
            return {"error": "Mahoraga is not running."}
        raise AssertionError(
            f"shouldn't reach {path} when /api/health is down",
        )

    with patch.object(mcp_server, "_get", side_effect=fake_get):
        result = await mcp_server._handle_health_check({})
    assert result["status"] == "down"
    assert result["degradation_level"] == 3
    assert result["level_name"] == "fastapi_unreachable"
    assert "error" in result


@pytest.mark.asyncio
async def test_health_check_down_when_no_agents():
    """Zero agents online → top of the degradation ladder."""
    fake = _stub_responses(
        base={"status": "ok", "agents_registered": 5, "agents_online": 0},
        routing={
            "quarantine": {"entries": []},
            "budget_pacer": {},
            "execution_pool": {},
        },
    )
    with patch.object(mcp_server, "_get", side_effect=fake):
        result = await mcp_server._handle_health_check({})
    assert result["status"] == "down"
    assert result["degradation_level"] == 4
    assert result["level_name"] == "all_agents_down"


@pytest.mark.asyncio
async def test_health_check_degrades_gracefully_on_routing_error():
    """If /api/health/routing fails but /api/health is up, we still
    report what we know rather than propagating the error."""
    async def fake_get(path: str, params: dict | None = None) -> dict:
        if path == "/api/health":
            return {"status": "ok", "agents_registered": 5, "agents_online": 5}
        if path == "/api/health/routing":
            return {"error": "F1.4 endpoint unreachable"}
        return []  # /api/agents/status

    with patch.object(mcp_server, "_get", side_effect=fake_get):
        result = await mcp_server._handle_health_check({})
    # We still get an answer — just with empty derived fields.
    assert result["status"] == "ok"  # fastapi up, all agents online
    assert result["routing_health"] is None  # the failure surfaced here
    assert result["quarantined_agents"] == []


@pytest.mark.asyncio
async def test_health_check_carries_base_fields_through():
    """Original /api/health fields like uptime_s + strategy must
    survive the composition (backwards compat for skill consumers
    that already read them)."""
    fake = _stub_responses(
        base={
            "status": "ok",
            "agents_registered": 5,
            "agents_online": 5,
            "uptime_s": 1234,
            "strategy": "linucb_per_bucket",
            "total_decisions": 250,
        },
        routing={
            "quarantine": {"entries": [], "n_drift_events_unresolved": 0},
            "budget_pacer": {"avg_cost": 0.01, "ceiling": 0.05},
            "execution_pool": {"depth_norm": 0.0},
        },
    )
    with patch.object(mcp_server, "_get", side_effect=fake):
        result = await mcp_server._handle_health_check({})
    assert result["uptime_s"] == 1234
    assert result["strategy"] == "linucb_per_bucket"
    assert result["total_decisions"] == 250
