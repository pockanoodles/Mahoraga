"""Thompson Sampling — Bayesian multi-armed bandit with Beta(α, β) per agent."""
from __future__ import annotations
import json
import numpy as np
from pathlib import Path
from .base import RoutingStrategy


class ThompsonSamplingRouter(RoutingStrategy):
    name = "thompson"

    def __init__(self, success_threshold: float = 0.5):
        self.threshold = success_threshold
        self.alpha: dict[str, float] = {}
        self.beta_: dict[str, float] = {}

    def select_agent(self, context, available_agents: list[str]) -> str:
        if not available_agents:
            raise ValueError("available_agents must not be empty")
        samples = {}
        for a in available_agents:
            alpha = self.alpha.get(a, 1.0)
            beta = self.beta_.get(a, 1.0)
            samples[a] = float(np.random.beta(alpha, beta))
        self._last_samples = {a: round(s, 4) for a, s in samples.items()}
        return max(available_agents, key=lambda a: samples[a])

    def update(self, context, agent: str, reward: float) -> None:
        if agent not in self.alpha:
            self.alpha[agent] = 1.0
            self.beta_[agent] = 1.0
        if reward > self.threshold:
            self.alpha[agent] += 1.0
        else:
            self.beta_[agent] += 1.0

    def get_scores(self) -> dict:
        return getattr(self, '_last_samples', {})

    def get_distributions(self) -> dict[str, tuple[float, float]]:
        agents = set(self.alpha) | set(self.beta_)
        return {a: (self.alpha.get(a, 1.0), self.beta_.get(a, 1.0)) for a in agents}

    def save_state(self, path: str) -> None:
        state = {"threshold": self.threshold, "alpha": self.alpha, "beta": self.beta_}
        Path(path).write_text(json.dumps(state))

    def load_state(self, path: str) -> None:
        state = json.loads(Path(path).read_text())
        self.threshold = state["threshold"]
        self.alpha = state["alpha"]
        self.beta_ = state["beta"]
