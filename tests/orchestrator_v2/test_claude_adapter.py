"""Tests for ClaudeAdapter."""
from __future__ import annotations
import pytest
from backend.orchestrator.adapters.claude_adapter import ClaudeAdapter
from backend.orchestrator.domain.models import Task


def test_claude_adapter_cost_estimate_scales_with_task_length():
    adapter = ClaudeAdapter(api_key="sk-test", model="claude-haiku-4-5-20251001")
    short_task = Task.new(run_id="r1", title="t", goal="hi")
    long_task  = Task.new(run_id="r1", title="t", goal="x " * 500)
    short_est = adapter.estimate_cost(short_task)
    long_est  = adapter.estimate_cost(long_task)
    assert long_est.estimated_cost_usd > short_est.estimated_cost_usd


def test_claude_adapter_declares_high_confidence_capabilities():
    adapter = ClaudeAdapter(api_key="sk-test")
    caps = {c.name: c.confidence for c in adapter.capabilities}
    assert caps.get("code", 0) >= 0.9
    assert caps.get("general", 0) >= 0.9


@pytest.mark.asyncio
async def test_claude_adapter_health_check_no_key():
    adapter = ClaudeAdapter(api_key=None)
    status = await adapter.health_check()
    assert status.available is False
    assert "ANTHROPIC_API_KEY" in (status.detail or status.error or "")
