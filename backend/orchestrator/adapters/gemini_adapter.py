"""GeminiCLIAdapter — AgentAdapter interface for the Gemini CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",     confidence=0.85),
    AgentCapability("explain",  confidence=0.88),
    AgentCapability("research", confidence=0.82),
    AgentCapability("general",  confidence=0.80),
]


class GeminiCLIAdapter(AgentAdapter):
    """Routes tasks to GeminiWorker — Google's CLI with free tier and web search grounding."""

    def __init__(
        self,
        binary_path: str = "gemini",
        model: str | None = None,
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        self._binary = binary_path
        self._model = model  # None → gemini picks default (usually 2.0-flash on free tier)
        self._capabilities = capabilities if capabilities is not None else _CAPABILITIES

    @property
    def name(self) -> str:
        return "gemini-cli"

    @property
    def worker_id(self) -> str:
        return "gemini:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return self._capabilities

    def estimate_cost(self, task: "Task") -> CostEstimate:
        model = (self._model or "flash").lower()
        if "flash" in model:
            return CostEstimate(
                estimated_cost_usd=0.0,
                model=self._model or "gemini-2.0-flash",
                notes="Gemini Flash free tier: 60 RPM, 1000 req/day",
            )
        return CostEstimate(
            estimated_cost_usd=0.002,
            model=self._model or "gemini-pro",
            notes="Gemini Pro — paid tier",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="gemini not found. Install: npm install -g @google/gemini-cli",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
