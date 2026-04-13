"""Tests for CodexAdapter."""
from __future__ import annotations
import pytest
from backend.orchestrator.domain.models import Task


@pytest.mark.asyncio
async def test_codex_adapter_health_check_not_installed():
    """CodexAdapter returns available=False when `codex` binary is missing."""
    from backend.orchestrator.adapters.codex_adapter import CodexAdapter
    adapter = CodexAdapter(binary_path="/nonexistent/codex")
    status = await adapter.health_check()
    assert status.available is False


def test_codex_adapter_worker_id():
    from backend.orchestrator.adapters.codex_adapter import CodexAdapter
    adapter = CodexAdapter()
    assert adapter.worker_id == "codex:cli"


def test_codex_adapter_low_cost():
    from backend.orchestrator.adapters.codex_adapter import CodexAdapter
    adapter = CodexAdapter()
    task = Task.new(run_id="r1", title="t", goal="write a sort function")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd <= 0.01
