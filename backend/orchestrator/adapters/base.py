"""AgentAdapter — the unified interface for all agents in Mahoraga.

Every agent (Ollama, Claude, Codex CLI, Aider, or any future agent) implements
this interface to plug into the orchestration layer. The interface covers:
- Identity (name, worker_id)
- Capability declaration (what the agent is good at)
- Cost estimation (for routing decisions)
- Health checking (availability before dispatch)

Note: Execution still goes through WorkerAdapter/executor.py for Phase 2.
The `worker_id` property maps this adapter to a WorkerRegistry entry.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.models import Task


@dataclass
class AgentCapability:
    """A capability this agent has, and how confident it is."""
    name: str                   # "code", "refactor", "explain", "test", "plan", "general"
    confidence: float = 1.0     # 0.0–1.0, higher = better at this capability


@dataclass
class CostEstimate:
    """Estimated cost for executing a task through this adapter."""
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = ""
    notes: str = ""


@dataclass
class AgentStatus:
    """Current health and availability of an agent."""
    name: str
    available: bool
    detail: str = ""
    latency_ms: float | None = None
    rate_limited: bool = False
    error: str | None = None


class AgentAdapter(ABC):
    """
    Base class for all agent adapters.

    Implement this to add a new agent to Mahoraga. The router uses
    `capabilities` and `estimate_cost` to select the best agent.
    `health_check` is called at startup and periodically to verify availability.

    The `worker_id` property maps this adapter to a `WorkerAdapter` entry in
    `WorkerRegistry` — the executor uses that entry for actual task execution.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short human-readable name: 'ollama', 'claude', 'codex-cli', 'aider'."""
        ...

    @property
    @abstractmethod
    def worker_id(self) -> str:
        """The WorkerRegistry key to use when this adapter is selected for routing.

        Example: 'ollama:coder', 'claude:sonnet', 'codex:cli', 'aider:default'
        """
        ...

    @property
    @abstractmethod
    def capabilities(self) -> list[AgentCapability]:
        """Declare what this agent can do and how well."""
        ...

    @abstractmethod
    def estimate_cost(self, task: "Task") -> CostEstimate:
        """Estimate cost before execution. Used by router to compare agents.

        For free agents (Ollama, Aider+Ollama): return CostEstimate(estimated_cost_usd=0.0).
        For API agents: estimate from task length and model pricing.
        """
        ...

    @abstractmethod
    async def health_check(self) -> AgentStatus:
        """Check if the agent is available and ready to accept tasks.

        Called at startup and by /api/agents/status.
        Must not raise — return AgentStatus(available=False, error=str(exc)) on failure.
        """
        ...
