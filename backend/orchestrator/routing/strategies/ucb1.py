"""UCB1 — non-contextual multi-armed bandit. c=1.414 default."""
from __future__ import annotations
import math
import json
from pathlib import Path
from .base import RoutingStrategy


class UCB1Router(RoutingStrategy):
    name = "ucb1"

    def __init__(self, c: float = 1.414):
        self.c = c
        self.N: dict[str, int] = {}
        self.Q: dict[str, float] = {}
        self.t: int = 0

    def select_agent(self, context, available_agents: list[str]) -> str:
        self.t += 1
        for a in available_agents:
            if a not in self.N:
                self.N[a] = 0
                self.Q[a] = 0.0
        # Explore untried agents first
        untried = [a for a in available_agents if self.N[a] == 0]
        if untried:
            return untried[0]
        # UCB1 formula
        scores = {}
        for a in available_agents:
            exploit = self.Q[a]
            explore = self.c * math.sqrt(math.log(self.t) / self.N[a])
            scores[a] = exploit + explore
        self._last_scores = {a: round(s, 4) for a, s in scores.items()}
        return max(available_agents, key=lambda a: scores[a])

    def update(self, context, agent: str, reward: float) -> None:
        self.N[agent] = self.N.get(agent, 0) + 1
        n = self.N[agent]
        old_q = self.Q.get(agent, 0.0)
        self.Q[agent] = old_q + (reward - old_q) / n  # incremental mean

    def get_scores(self, available_agents=None) -> dict:
        return getattr(self, '_last_scores', {})

    def save_state(self, path: str) -> None:
        state = {"c": self.c, "N": self.N, "Q": self.Q, "t": self.t}
        Path(path).write_text(json.dumps(state))

    def load_state(self, path: str) -> None:
        state = json.loads(Path(path).read_text())
        self.c = state["c"]
        self.N = state["N"]
        self.Q = state["Q"]
        self.t = state["t"]
