"""AiderAdapter — AgentAdapter interface for the Aider CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("refactor", confidence=0.90),
    AgentCapability("code",     confidence=0.70),
    AgentCapability("test",     confidence=0.70),
]


class AiderAdapter(AgentAdapter):
    """Routes tasks to AiderWorker (subprocess-based, supports Ollama for free inference)."""

    def __init__(
        self,
        binary_path: str = "aider",
        model: str = "ollama_chat/qwen3:4b",
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        self._binary = binary_path
        self._model = model
        self._capabilities = capabilities if capabilities is not None else _CAPABILITIES

    @property
    def name(self) -> str:
        return "aider"

    @property
    def worker_id(self) -> str:
        return "aider:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return self._capabilities

    def estimate_cost(self, task: "Task") -> CostEstimate:
        if "ollama" in self._model.lower():
            return CostEstimate(
                estimated_cost_usd=0.0,
                model=self._model,
                notes="Local Ollama model — free",
            )
        return CostEstimate(
            estimated_cost_usd=0.005,
            model=self._model,
            notes="API cost varies by provider",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="aider not found. Install: pip install aider-install && aider-install",
            )
        return AgentStatus(
            name=self.name, available=True, detail=f"binary={binary}, model={self._model}"
        )
