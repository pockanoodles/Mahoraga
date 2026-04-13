"""
Static keyword-based routing. No learning. Baseline for benchmarking.
"""
from __future__ import annotations
from .base import RoutingStrategy


class StaticRouter(RoutingStrategy):
    name = "static"

    ROUTING_MAP = {
        "code_generation": ["aider", "opencode", "ollama", "codex"],
        "code_editing":    ["aider", "opencode", "claude"],
        "debugging":       ["opencode", "aider", "claude"],
        "research":        ["goose", "gemini-cli", "claude"],
        "simple_qa":       ["ollama", "gemini-cli", "goose"],
        "complex":         ["claude", "opencode", "codex"],
        "terminal":        ["opencode", "goose", "ollama"],
        "default":         ["ollama", "opencode", "aider", "claude"],
    }

    def select_agent(self, context, available_agents: list[str]) -> str:
        if not available_agents:
            raise ValueError("available_agents must not be empty")
        task_type = self._classify(context)
        preferences = self.ROUTING_MAP.get(task_type, self.ROUTING_MAP["default"])
        for agent in preferences:
            if agent in available_agents:
                return agent
        return available_agents[0]

    def update(self, context, agent: str, reward: float) -> None:
        pass  # Static doesn't learn

    def _classify(self, context) -> str:
        if context.has_research_keywords > 0.5 and context.code_keyword_density < 0.05:
            return "research"
        if context.is_question > 0.5 and context.code_keyword_density < 0.1 and context.word_count_norm < 0.1:
            return "simple_qa"
        if context.has_error_keywords > 0.5:
            return "debugging"
        if context.has_creation_keywords > 0.5 and context.code_keyword_density > 0.05:
            return "code_generation"
        if context.complexity_tier > 0.8:
            return "complex"
        if context.code_keyword_density > 0.1:
            return "code_editing"
        return "default"
