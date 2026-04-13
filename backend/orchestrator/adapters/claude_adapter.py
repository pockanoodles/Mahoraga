"""ClaudeAdapter — wraps ClaudeWorker for the AgentAdapter routing interface."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

# Haiku input/output pricing per million tokens (USD) — update as pricing changes
_HAIKU_INPUT_PER_M  = 0.80
_HAIKU_OUTPUT_PER_M = 4.00
_SONNET_INPUT_PER_M = 3.00
_SONNET_OUTPUT_PER_M = 15.00

_CAPABILITIES = [
    AgentCapability("code",    confidence=0.95),
    AgentCapability("general", confidence=0.95),
    AgentCapability("plan",    confidence=0.95),
    AgentCapability("explain", confidence=0.95),
    AgentCapability("refactor",confidence=0.90),
    AgentCapability("test",    confidence=0.90),
]


class ClaudeAdapter(AgentAdapter):
    """Routes tasks to ClaudeWorker (Haiku/Sonnet/Opus depending on config)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude:sonnet",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._worker_id = worker_id

    @property
    def name(self) -> str:
        return "claude"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        # Rough estimate: ~4 chars per token, output ~= 2× input
        text = f"{task.title} {task.goal}"
        input_tokens  = max(100, len(text) // 4)
        output_tokens = input_tokens * 2

        is_haiku = "haiku" in self._model.lower()
        input_per_m  = _HAIKU_INPUT_PER_M  if is_haiku else _SONNET_INPUT_PER_M
        output_per_m = _HAIKU_OUTPUT_PER_M if is_haiku else _SONNET_OUTPUT_PER_M

        cost = (input_tokens * input_per_m + output_tokens * output_per_m) / 1_000_000
        return CostEstimate(
            estimated_tokens=input_tokens + output_tokens,
            estimated_cost_usd=round(cost, 6),
            model=self._model,
        )

    async def health_check(self) -> AgentStatus:
        if not self._api_key:
            return AgentStatus(
                name=self.name, available=False,
                detail="ANTHROPIC_API_KEY not set — Claude backend disabled",
            )
        # Key is present — assume available (lightweight; avoid burning API credits on health checks)
        return AgentStatus(
            name=self.name, available=True,
            detail=f"model={self._model}",
        )
