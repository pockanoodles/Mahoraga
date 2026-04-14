"""OpenCodeAdapter — AgentAdapter interface for the OpenCode CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",     confidence=0.92),
    AgentCapability("refactor", confidence=0.88),
    AgentCapability("test",     confidence=0.82),
    AgentCapability("explain",  confidence=0.75),
    AgentCapability("general",  confidence=0.70),
]


class OpenCodeAdapter(AgentAdapter):
    """Routes tasks to OpenCodeWorker — open-source Claude Code alternative with 75+ LLM providers."""

    def __init__(
        self,
        binary_path: str = "opencode",
        model: str | None = None,
    ) -> None:
        self._binary = binary_path
        self._model = model

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def worker_id(self) -> str:
        return "opencode:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        model = (self._model or "").lower()
        if not model or "ollama" in model:
            return CostEstimate(
                estimated_cost_usd=0.0,
                model=self._model or "auto",
                notes="No model specified or Ollama — free",
            )
        if "flash" in model:
            return CostEstimate(
                estimated_cost_usd=0.001,
                model=self._model,
                notes="Gemini Flash — low cost",
            )
        return CostEstimate(
            estimated_cost_usd=0.005,
            model=self._model,
            notes="API cost varies by configured provider",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="opencode not found. Install: npm install -g opencode-ai",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
