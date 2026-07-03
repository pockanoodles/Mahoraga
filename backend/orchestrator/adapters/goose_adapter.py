"""GooseAdapter — AgentAdapter interface for the Goose CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("research", confidence=0.85),
    AgentCapability("general",  confidence=0.82),
    AgentCapability("explain",  confidence=0.78),
]


class GooseAdapter(AgentAdapter):
    """Routes tasks to GooseWorker — Block's general-purpose open-source AI agent."""

    def __init__(
        self,
        binary_path: str = "goose",
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        self._binary = binary_path
        self._capabilities = capabilities if capabilities is not None else _CAPABILITIES

    @property
    def name(self) -> str:
        return "goose"

    @property
    def worker_id(self) -> str:
        return "goose:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return self._capabilities

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.0,
            model="goose-provider",
            notes="Cost depends on Goose's configured provider (Ollama = free)",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="goose not found. Install: brew install goose",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
