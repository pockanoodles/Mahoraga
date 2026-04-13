"""AdapterRegistry — central registry of all available agent adapters.

The router queries this registry to select the best adapter for a task.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {}

    def register(self, adapter: AgentAdapter) -> None:
        self._adapters[adapter.name] = adapter
        logger.info("adapter registered: %s → worker_id=%s", adapter.name, adapter.worker_id)

    def get(self, name: str) -> AgentAdapter | None:
        return self._adapters.get(name)

    def all(self) -> list[AgentAdapter]:
        return list(self._adapters.values())

    def find_capable(self, capability: str) -> list[tuple[AgentAdapter, float]]:
        """Return adapters that declare this capability, sorted by confidence descending."""
        matches = []
        for adapter in self._adapters.values():
            for cap in adapter.capabilities:
                if cap.name == capability:
                    matches.append((adapter, cap.confidence))
                    break
        return sorted(matches, key=lambda x: x[1], reverse=True)

    async def route(self, task: "Task", required_capability: str) -> AgentAdapter | None:
        """Select the best available adapter for a task.

        Scoring: capability_confidence × (1 / (1 + cost_usd))
        Returns None if no capable, healthy adapter exists.
        """
        candidates = self.find_capable(required_capability)
        if not candidates:
            return None

        scored: list[tuple[AgentAdapter, float]] = []
        for adapter, confidence in candidates:
            try:
                status = await adapter.health_check()
            except Exception as exc:
                logger.warning("health_check failed for %s: %s", adapter.name, exc)
                continue
            if not status.available:
                continue
            cost = adapter.estimate_cost(task).estimated_cost_usd
            score = confidence * (1.0 / (1.0 + cost))
            scored.append((adapter, score))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)
        best = scored[0][0]
        logger.info("adapter routed: %s (capability=%s)", best.name, required_capability)
        return best

    async def all_statuses(self) -> list[dict]:
        """Return health status for all registered adapters. Used by /api/agents/status."""
        results = []
        for adapter in self._adapters.values():
            try:
                status = await adapter.health_check()
            except Exception as exc:
                status = AgentStatus(name=adapter.name, available=False, error=str(exc))
            results.append({
                "name": adapter.name,
                "worker_id": adapter.worker_id,
                "available": status.available,
                "detail": status.detail,
                "latency_ms": status.latency_ms,
                "rate_limited": status.rate_limited,
                "error": status.error,
                "capabilities": [
                    {"name": c.name, "confidence": c.confidence}
                    for c in adapter.capabilities
                ],
            })
        return results
