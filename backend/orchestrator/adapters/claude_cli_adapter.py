"""ClaudeCliAdapter — AgentAdapter interface for the Claude Code CLI worker.

Unlike ClaudeAdapter (Anthropic SDK, needs ANTHROPIC_API_KEY), this arm runs
the `claude` CLI authenticated via the local subscription login — health is
binary presence, not key presence.
"""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate
from ..tracking.pricing import calculate_cost

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",    confidence=0.95),
    AgentCapability("general", confidence=0.95),
    AgentCapability("plan",    confidence=0.95),
    AgentCapability("explain", confidence=0.95),
    AgentCapability("refactor",confidence=0.90),
    AgentCapability("test",    confidence=0.90),
]


class ClaudeCliAdapter(AgentAdapter):
    """Routes tasks to ClaudeCliWorker (subprocess-based Claude Code CLI)."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude-cli:sonnet",
        binary_path: str = "claude",
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._binary = binary_path
        self._capabilities = capabilities if capabilities is not None else _CAPABILITIES

    @property
    def name(self) -> str:
        return "claude-cli"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[AgentCapability]:
        return self._capabilities

    def estimate_cost(self, task: "Task") -> CostEstimate:
        # Rough estimate: ~4 chars per token, output ~= 2× input
        text = f"{task.title} {task.goal}"
        input_tokens  = max(100, len(text) // 4)
        output_tokens = input_tokens * 2
        return CostEstimate(
            estimated_tokens=input_tokens + output_tokens,
            estimated_cost_usd=calculate_cost(self._model, input_tokens, output_tokens),
            model=self._model,
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="claude not found. Install: npm install -g @anthropic-ai/claude-code",
            )
        return AgentStatus(
            name=self.name, available=True,
            detail=f"binary={binary} model={self._model}",
        )
