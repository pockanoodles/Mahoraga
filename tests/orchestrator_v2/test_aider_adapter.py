"""Tests for AiderAdapter."""
from __future__ import annotations
import pytest
from backend.orchestrator.domain.models import Task


@pytest.mark.asyncio
async def test_aider_adapter_health_check_not_installed():
    from backend.orchestrator.adapters.aider_adapter import AiderAdapter
    adapter = AiderAdapter(binary_path="/nonexistent/aider")
    status = await adapter.health_check()
    assert status.available is False


def test_aider_adapter_worker_id():
    from backend.orchestrator.adapters.aider_adapter import AiderAdapter
    adapter = AiderAdapter()
    assert adapter.worker_id == "aider:default"


def test_aider_free_when_using_ollama():
    from backend.orchestrator.adapters.aider_adapter import AiderAdapter
    adapter = AiderAdapter(model="ollama_chat/qwen3:4b")
    task = Task.new(run_id="r1", title="t", goal="refactor this function")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd == 0.0
