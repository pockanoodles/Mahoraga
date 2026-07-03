"""
Static keyword-based routing. No learning. Baseline for benchmarking.

Also exposes `classify_bucket()` as the shared route-time bucket classifier
used by per-bucket policies (memory α gating, reward-weight learning, etc.).
The function is deterministic over the 9-dim TaskContext vector — same
input → same bucket.
"""
from __future__ import annotations
from .base import RoutingStrategy


def classify_bucket(context) -> str:
    """Classify a TaskContext into one of the canonical routing buckets.

    Uses the 9-dim feature vector plus metadata fields (has_security_keywords,
    has_review_keywords, has_test_keywords, has_refactor_keywords) computed by
    TaskContext.from_task() but not included in the bandit's feature vector
    (d=9 stays fixed). First match wins; ordering is priority order.
    """
    if context.has_error_keywords > 0.5:
        return "debug"
    if context.has_test_keywords > 0.5:
        return "test"
    if context.has_refactor_keywords > 0.5:
        return "refactor"
    if context.has_security_keywords > 0.5:
        return "security"
    if context.has_review_keywords > 0.5:
        return "review"
    if context.has_research_keywords > 0.5 and context.code_keyword_density < 0.05:
        return "research"
    if context.has_creation_keywords > 0.5 and context.code_keyword_density > 0.05:
        return "code"
    if context.complexity_tier > 0.8:
        return "plan"
    if context.code_keyword_density > 0.1:
        return "code"
    return "general"


class StaticRouter(RoutingStrategy):
    name = "static"

    ROUTING_MAP = {
        "code":     ["ollama:qwen3.5",      "ollama:granite4.1-8b"],
        "test":     ["ollama:qwen3.5",      "ollama:granite4.1-8b"],
        "refactor": ["ollama:qwen3.5",      "ollama:granite4.1-8b"],
        "debug":    ["ollama:granite4.1-8b", "ollama:qwen3.5"],
        "research": ["ollama:granite4.1-8b", "ollama:qwen3.5"],
        "review":   ["ollama:granite4.1-8b", "ollama:qwen3.5"],
        "plan":     ["ollama:granite4.1-8b", "ollama:qwen3.5"],
        "security": ["ollama:granite4.1-8b", "ollama:qwen3.5"],
        "general":  ["ollama:qwen3.5",      "ollama:granite4.1-8b"],
    }

    def select_agent(self, context, available_agents: list[str]) -> str:
        if not available_agents:
            raise ValueError("available_agents must not be empty")
        task_type = self._classify(context)
        preferences = self.ROUTING_MAP.get(task_type, self.ROUTING_MAP["general"])
        for agent in preferences:
            if agent in available_agents:
                return agent
        return available_agents[0]

    def update(self, context, agent: str, reward: float) -> None:
        pass  # Static doesn't learn

    def _classify(self, context) -> str:
        return classify_bucket(context)
