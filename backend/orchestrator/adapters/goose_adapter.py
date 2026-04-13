"""GooseAdapter — AgentAdapter interface for Block's Goose AI agent worker."""
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
    AgentCapability("refactor", confidence=0.82),
    AgentCapability("test",     confidence=0.80),
    AgentCapability("explain",  confidence=0.70),
    AgentCapability("general",  confidence=0.72),
]


class GooseAdapter(AgentAdapter):
    """Routes tasks to GooseWorker (Block's Goose AI agent, uses configured LLM)."""

    def __init__(self, binary_path: str = "goose") -> None:
        self._binary = binary_path

    @property
    def name(self) -> str:
        return "goose"

    @property
    def worker_id(self) -> str:
        return "goose:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.0,
            model="goose",
            notes="Uses configured LLM backend",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="goose not found. Install Block's AI agent: github.com/block/goose",
            )
        # Verify this is Block's AI goose by checking `goose run --help` exits cleanly.
        # The DB migration tool with the same name does not have a `run` subcommand.
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "run", "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                return AgentStatus(
                    name=self.name, available=False,
                    detail=f"goose at {binary} failed 'run --help' — may be DB migration tool, not Block's AI agent",
                )
        except Exception as exc:
            return AgentStatus(name=self.name, available=False, detail=str(exc))

        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
