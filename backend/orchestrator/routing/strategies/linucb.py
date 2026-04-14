"""
LinUCB with Disjoint Linear Models — contextual multi-armed bandit.
Reference: Li, Chu, Langford, Schapire. WWW 2010.
"""
from __future__ import annotations
import json
import numpy as np
from pathlib import Path
from .base import RoutingStrategy


# Cold-start priors: initial expected reward per agent, used to seed b on first encounter.
# Keys match adapter.name values registered in AdapterRegistry.
#
# Ollama gets the highest prior for general/plan (no subprocess overhead).
# Codex-cli and aider get the highest prior for code tasks — they can write files;
# ollama is no longer in the code pool so it never competes there anyway.
_DEFAULT_PRIORS: dict[str, float] = {
    "codex-cli":    0.90,   # can write files, strong on code
    "aider":        0.85,   # refactor / multi-file edits
    "gemini-cli":   0.80,   # Google AI, good general + code
    "opencode":     0.75,   # sst/opencode, Ollama-backed
    "ollama":       0.70,   # chat/plan — no file writes; still wins general/plan
    "goose":        0.60,   # Block's agent (needs separate install)
    "claude":       0.85,   # only registered if API key set
}


class LinUCBRouter(RoutingStrategy):
    name = "linucb"

    def __init__(
        self,
        d: int = 9,
        alpha: float = 1.0,
        decay: float = 0.98,
        priors: dict[str, float] | None = None,
    ):
        self.d = d
        self.alpha = alpha
        self.decay = decay
        self.priors: dict[str, float] = priors if priors is not None else _DEFAULT_PRIORS
        self.A: dict[str, np.ndarray] = {}
        self.b: dict[str, np.ndarray] = {}
        self.t: int = 0

    def _init_agent(self, agent: str) -> None:
        if agent in self.A:
            return  # already initialized

        existing = [a for a in self.A if a != agent]
        if not existing or self.t == 0:
            # First arm, or no real routing yet (e.g. during warm-start injection) — cold start.
            self.A[agent] = np.identity(self.d)
            prior = self.priors.get(agent, 0.5)
            self.b[agent] = prior * np.ones((self.d, 1))
            return

        # Average-init: blend average of existing arms with λI to give the new
        # arm moderate exploration without the huge UCB bonus of pure cold start.
        avg_A = np.mean([self.A[a] for a in existing], axis=0)
        avg_b = np.mean([self.b[a] for a in existing], axis=0)
        self.A[agent] = 0.5 * avg_A + 0.5 * np.identity(self.d)
        self.b[agent] = 0.5 * avg_b

        # Override with compatibility_matrix prior if available for this specific agent
        from ..warm_start import load_compatibility_matrix, warm_start_from_matrix
        matrix = load_compatibility_matrix()
        if matrix and agent in matrix:
            warm_start_from_matrix(self, {agent: matrix[agent]}, lambda_prior=2.0)

    def inject_pseudo_obs(self, agent: str, x: np.ndarray, reward: float, lambda_prior: float = 1.0) -> None:
        """Inject one pseudo-observation into arm `agent`.

        A[agent] += lambda_prior * outer(x, x)
        b[agent] += lambda_prior * reward * x.reshape(-1,1)
        """
        self._init_agent(agent)
        x = x.reshape(-1, 1)  # d×1
        self.A[agent] += lambda_prior * (x @ x.T)
        self.b[agent] += lambda_prior * reward * x

    def select_agent(self, context, available_agents: list[str]) -> str:
        if not available_agents:
            raise ValueError("available_agents must not be empty")
        self.t += 1
        x = context.to_vector().reshape(-1, 1)  # d×1
        best_agent = available_agents[0]
        best_ucb = -float('inf')
        scores = {}
        for a in available_agents:
            self._init_agent(a)
            theta = np.linalg.solve(self.A[a], self.b[a])
            exploit = float((x.T @ theta).item())
            explore_sq = float((x.T @ np.linalg.solve(self.A[a], x)).item())
            explore = self.alpha * float(np.sqrt(max(0.0, explore_sq)))
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

    def compute_scores(self, context, available_agents: list[str]) -> dict:
        """Compute UCB scores for all agents without incrementing t or storing _last_scores."""
        if not available_agents:
            return {}
        x = context.to_vector().reshape(-1, 1)
        scores = {}
        for a in available_agents:
            self._init_agent(a)  # idempotent — only initialises on first call
            theta = np.linalg.solve(self.A[a], self.b[a])
            exploit = float((x.T @ theta).item())
            explore_sq = float((x.T @ np.linalg.solve(self.A[a], x)).item())
            explore = self.alpha * float(np.sqrt(max(0.0, explore_sq)))
            scores[a] = {
                "ucb": round(exploit + explore, 4),
                "exploit": round(exploit, 4),
                "explore": round(explore, 4),
            }
        return scores

    def get_theta(self, agent: str) -> np.ndarray:
        self._init_agent(agent)
        return (np.linalg.inv(self.A[agent]) @ self.b[agent]).flatten()

    def get_feature_importance(self, agent: str, feature_names: list[str]) -> dict:
        theta = self.get_theta(agent)
        return {name: round(float(w), 4) for name, w in zip(feature_names, theta)}

    def save_state(self, path: str) -> None:
        import os
        state = {
            "d": self.d, "alpha": self.alpha, "decay": self.decay, "t": self.t,
            "agents": {
                a: {"A": self.A[a].tolist(), "b": self.b[a].tolist()}
                for a in self.A
            },
        }
        tmp = path + ".tmp"
        Path(tmp).write_text(json.dumps(state, indent=2))
        os.replace(tmp, path)

    def load_state(self, path: str) -> None:
        state = json.loads(Path(path).read_text())
        if state.get("d") != self.d:
            raise ValueError(
                f"Persisted state has d={state.get('d')}, but router expects d={self.d}. "
                "Delete the state file to reset."
            )
        self.d = state["d"]
        self.alpha = state["alpha"]
        # decay is a constructor hyperparameter — don't restore from file so that
        # changing the default takes effect without needing to delete the state file.
        self.t = state["t"]
        for a, data in state["agents"].items():
            self.A[a] = np.array(data["A"])
            self.b[a] = np.array(data["b"]).reshape(-1, 1)
