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
    AgentCapability("code",     confidence=0.84),
    AgentCapability("refactor", confidence=0.80),
    AgentCapability("test",     confidence=0.78),
    AgentCapability("explain",  confidence=0.72),
]

_OPENCODE_BINARY = "opencode"


class OpenCodeAdapter(AgentAdapter):
    """Routes tasks to OpenCodeWorker (OpenCode CLI, supports Ollama backend — free)."""

    def __init__(self, binary_path: str = _OPENCODE_BINARY) -> None:
        self._binary = binary_path

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def worker_id(self) -> str:
        return "opencode:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.0,
            model="opencode",
            notes="Uses configured LLM backend — Ollama = free",
        )

    async def health_check(self) -> AgentStatus:
        import os
        if self._binary == _OPENCODE_BINARY:
            binary = shutil.which(self._binary)
        else:
            binary = self._binary if os.path.isfile(self._binary) else None
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="opencode not found. Install from https://github.com/sst/opencode",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
