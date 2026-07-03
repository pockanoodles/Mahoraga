"""CodexAdapter — AgentAdapter interface for the Codex CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",    confidence=0.90),
    AgentCapability("refactor",confidence=0.85),
    AgentCapability("test",    confidence=0.80),
    AgentCapability("explain", confidence=0.70),
]


class CodexAdapter(AgentAdapter):
    """Routes tasks to CodexWorker (subprocess-based OpenAI Codex CLI)."""

    def __init__(
        self,
        binary_path: str = "codex",
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        self._binary = binary_path
        self._capabilities = capabilities if capabilities is not None else _CAPABILITIES

    @property
    def name(self) -> str:
        return "codex-cli"

    @property
    def worker_id(self) -> str:
        return "codex:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return self._capabilities

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.001,
            model="codex-cli",
            notes="Free tier with ChatGPT Plus; otherwise OpenAI API rates apply",
        )

    async def health_check(self) -> AgentStatus:
        import os
        # Use shutil.which for default "codex"; for explicit paths, verify the file exists
        if self._binary == "codex":
            binary = shutil.which(self._binary)
        else:
            binary = self._binary if os.path.isfile(self._binary) else None
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="codex not found. Install: npm install -g @openai/codex",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
