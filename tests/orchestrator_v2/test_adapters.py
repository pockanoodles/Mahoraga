"""Tests for AgentAdapter interface and AdapterRegistry."""
from __future__ import annotations
import pytest
from backend.orchestrator.adapters.base import (
    AgentAdapter, AgentCapability, CostEstimate, AgentStatus,
)
from backend.orchestrator.domain.models import Task


class _ConcreteAdapter(AgentAdapter):
    """Minimal concrete implementation for testing the ABC contract."""

    @property
    def name(self) -> str:
        return "test-adapter"

    @property
    def worker_id(self) -> str:
        return "test:worker"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability("code", confidence=0.9), AgentCapability("general", confidence=0.7)]

    def estimate_cost(self, task: Task) -> CostEstimate:
        return CostEstimate(estimated_tokens=100, estimated_cost_usd=0.001, model="test")

    async def health_check(self) -> AgentStatus:
        return AgentStatus(name=self.name, available=True, latency_ms=10.0)


def test_agent_adapter_instantiation():
    adapter = _ConcreteAdapter()
    assert adapter.name == "test-adapter"
    assert adapter.worker_id == "test:worker"
    assert len(adapter.capabilities) == 2


def test_capability_confidence_range():
    cap = AgentCapability("code", confidence=0.9)
    assert cap.name == "code"
    assert 0.0 <= cap.confidence <= 1.0


def test_cost_estimate_defaults():
    est = CostEstimate()
    assert est.estimated_cost_usd == 0.0
    assert est.estimated_tokens == 0


@pytest.mark.asyncio
async def test_health_check_returns_agent_status():
    adapter = _ConcreteAdapter()
    status = await adapter.health_check()
    assert isinstance(status, AgentStatus)
    assert status.available is True
    assert status.name == "test-adapter"


def test_estimate_cost_receives_task():
    adapter = _ConcreteAdapter()
    task = Task.new(run_id="r1", title="Test", goal="write hello world")
    est = adapter.estimate_cost(task)
    assert isinstance(est, CostEstimate)
