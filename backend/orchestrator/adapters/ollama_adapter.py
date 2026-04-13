"""OllamaAdapter — wraps OllamaWorker for the AgentAdapter routing interface."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

import httpx

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_DEFAULT_CAPABILITIES = [
    # Ollama generates code *text* but cannot create or modify files on disk.
    # Keeping it out of the "code" pool forces file-writing tasks to subprocess
    # agents (codex-cli, aider, gemini-cli) that can actually write files.
    AgentCapability("general", confidence=0.90),
    AgentCapability("plan",    confidence=0.85),
    AgentCapability("explain", confidence=0.80),
]


class OllamaAdapter(AgentAdapter):
    """Routes tasks to the OllamaWorker pool (ollama:coder / ollama:fast / ollama:general)."""

    def __init__(
        self,
        model: str = "qwen3:4b-q4_K_M",
        ollama_base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._base_url = ollama_base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def worker_id(self) -> str:
        return "ollama:general"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _DEFAULT_CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        return CostEstimate(
            estimated_cost_usd=0.0,
            model=self._model,
            notes="Local inference — free",
        )

    async def health_check(self) -> AgentStatus:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            if response.status_code != 200:
                return AgentStatus(
                    name=self.name, available=False,
                    detail="Ollama /api/tags returned non-200",
                )
            model_names = [m["name"] for m in response.json().get("models", [])]
            model_base = self._model.split(":")[0]
            if not any(m.startswith(model_base) for m in model_names):
                return AgentStatus(
                    name=self.name, available=False,
                    detail=f"Model {self._model!r} not pulled. Run: ollama pull {self._model}",
                )
            return AgentStatus(name=self.name, available=True, detail=f"model={self._model}")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return AgentStatus(
                name=self.name, available=False,
                error=f"Ollama unreachable at {self._base_url}: {exc}",
            )
        except Exception as exc:
            return AgentStatus(name=self.name, available=False, error=str(exc))
