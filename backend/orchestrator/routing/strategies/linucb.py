"""
LinUCB with Disjoint Linear Models — contextual multi-armed bandit.
Reference: Li, Chu, Langford, Schapire. WWW 2010.
"""
from __future__ import annotations
import json
import numpy as np
from pathlib import Path
from .base import RoutingStrategy


class LinUCBRouter(RoutingStrategy):
    name = "linucb"

    def __init__(self, d: int = 8, alpha: float = 1.0, decay: float = 1.0):
        self.d = d
        self.alpha = alpha
        self.decay = decay
        self.A: dict[str, np.ndarray] = {}
        self.b: dict[str, np.ndarray] = {}
        self.t: int = 0

    def _init_agent(self, agent: str) -> None:
        if agent not in self.A:
            self.A[agent] = np.identity(self.d)
            self.b[agent] = np.zeros((self.d, 1))

    def select_agent(self, context, available_agents: list[str]) -> str:
        self.t += 1
        x = context.to_vector().reshape(-1, 1)  # d×1
        best_agent = available_agents[0]
        best_ucb = -float('inf')
        scores = {}
        for a in available_agents:
            self._init_agent(a)
            A_inv = np.linalg.inv(self.A[a])
            theta = A_inv @ self.b[a]
            exploit = float((x.T @ theta).item())
            explore = self.alpha * float(np.sqrt((x.T @ A_inv @ x).item()))
            ucb = exploit + explore
            scores[a] = {
                "ucb": round(ucb, 4),
                "exploit": round(exploit, 4),
                "explore": round(explore, 4),
            }
            if ucb > best_ucb:
                best_ucb = ucb
                best_agent = a
        self._last_scores = scores
        return best_agent

    def update(self, context, agent: str, reward: float) -> None:
        self._init_agent(agent)
        x = context.to_vector().reshape(-1, 1)
        if self.decay < 1.0:
            self.A[agent] = self.decay * self.A[agent] + x @ x.T
            self.b[agent] = self.decay * self.b[agent] + reward * x
        else:
            self.A[agent] = self.A[agent] + x @ x.T
            self.b[agent] = self.b[agent] + reward * x

    def get_scores(self) -> dict:
        return getattr(self, '_last_scores', {})

    def get_theta(self, agent: str) -> np.ndarray:
        self._init_agent(agent)
        return (np.linalg.inv(self.A[agent]) @ self.b[agent]).flatten()

    def get_feature_importance(self, agent: str, feature_names: list[str]) -> dict:
        theta = self.get_theta(agent)
        return {name: round(float(w), 4) for name, w in zip(feature_names, theta)}

    def save_state(self, path: str) -> None:
        state = {
            "d": self.d, "alpha": self.alpha, "decay": self.decay, "t": self.t,
            "agents": {
                a: {"A": self.A[a].tolist(), "b": self.b[a].tolist()}
                for a in self.A
            },
        }
        Path(path).write_text(json.dumps(state, indent=2))

    def load_state(self, path: str) -> None:
        state = json.loads(Path(path).read_text())
        self.d = state["d"]
        self.alpha = state["alpha"]
        self.decay = state.get("decay", 1.0)
        self.t = state["t"]
        for a, data in state["agents"].items():
            self.A[a] = np.array(data["A"])
            self.b[a] = np.array(data["b"]).reshape(-1, 1)
