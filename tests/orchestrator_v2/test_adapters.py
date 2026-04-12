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


from backend.orchestrator.adapters.registry import AdapterRegistry


def _make_adapter(name: str, worker_id: str, capabilities: list[AgentCapability], cost: float = 0.0) -> AgentAdapter:
    class _A(AgentAdapter):
        @property
        def name(self): return name
        @property
        def worker_id(self): return worker_id
        @property
        def capabilities(self): return capabilities
        def estimate_cost(self, task): return CostEstimate(estimated_cost_usd=cost)
        async def health_check(self): return AgentStatus(name=name, available=True)
    return _A()


def test_registry_register_and_get():
    reg = AdapterRegistry()
    adapter = _make_adapter("ollama", "ollama:fast", [AgentCapability("general")])
    reg.register(adapter)
    assert reg.get("ollama") is adapter


def test_registry_all():
    reg = AdapterRegistry()
    reg.register(_make_adapter("a", "a:1", []))
    reg.register(_make_adapter("b", "b:1", []))
    assert len(reg.all()) == 2


def test_find_capable_returns_sorted_by_confidence():
    reg = AdapterRegistry()
    reg.register(_make_adapter("fast",  "f:1", [AgentCapability("code", confidence=0.7)]))
    reg.register(_make_adapter("smart", "s:1", [AgentCapability("code", confidence=0.95)]))
    results = reg.find_capable("code")
    assert results[0][0].name == "smart"   # higher confidence first
    assert results[1][0].name == "fast"


@pytest.mark.asyncio
async def test_route_picks_highest_scoring_available():
    reg = AdapterRegistry()
    reg.register(_make_adapter("cheap",     "c:1", [AgentCapability("code", 0.7)], cost=0.0))
    reg.register(_make_adapter("expensive", "e:1", [AgentCapability("code", 0.9)], cost=0.05))
    task = Task.new(run_id="r1", title="test", goal="write a hello world function")
    result = await reg.route(task, required_capability="code")
    assert result is not None


@pytest.mark.asyncio
async def test_route_skips_unavailable_adapters():
    class _Unavailable(AgentAdapter):
        @property
        def name(self): return "down"
        @property
        def worker_id(self): return "down:1"
        @property
        def capabilities(self): return [AgentCapability("code", 1.0)]
        def estimate_cost(self, task): return CostEstimate()
        async def health_check(self): return AgentStatus(name="down", available=False, error="not running")

    reg = AdapterRegistry()
    reg.register(_Unavailable())
    task = Task.new(run_id="r1", title="test", goal="write code")
    result = await reg.route(task, required_capability="code")
    assert result is None
