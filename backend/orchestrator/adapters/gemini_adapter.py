"""GeminiAdapter — AgentAdapter interface for the Gemini CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",     confidence=0.88),
    AgentCapability("refactor", confidence=0.85),
    AgentCapability("test",     confidence=0.80),
    AgentCapability("explain",  confidence=0.82),
    AgentCapability("general",  confidence=0.75),
]


class GeminiAdapter(AgentAdapter):
    """Routes tasks to GeminiWorker (Gemini CLI subprocess, Google AI free tier)."""

    def __init__(self, binary_path: str = "gemini") -> None:
        self._binary = binary_path

    @property
    def name(self) -> str:
        return "gemini-cli"

    @property
    def worker_id(self) -> str:
        return "gemini:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.001,
            model="gemini-cli",
            notes="Google AI free tier available; otherwise API rates apply",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="gemini not found. Install: npm install -g @google/gemini-cli",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
