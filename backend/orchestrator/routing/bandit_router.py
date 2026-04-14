"""
BanditRouter — self-learning router for Mahoraga.

Wraps a RoutingStrategy and integrates it with the adapter registry,
decision logging, and the existing gateway pipeline.

State persists to ~/.mahoraga/bandit_state.json across restarts.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from .context import TaskContext
from .reward import RewardCalculator, TaskOutcome
from .decision_log import DecisionLogger
from .strategies import StaticRouter, UCB1Router, ThompsonSamplingRouter, LinUCBRouter

if TYPE_CHECKING:
    from ..adapters.registry import AdapterRegistry

BANDIT_STATE_PATH = Path.home() / ".mahoraga" / "bandit_state.json"

STRATEGIES = {
    "static": StaticRouter,
    "ucb1": UCB1Router,
    "thompson": ThompsonSamplingRouter,
    "linucb": LinUCBRouter,
}


class BanditRouter:

    def __init__(
        self,
        strategy: str = "linucb",
        registry=None,
        logger=None,
        reward_weights: tuple[float, float, float] = (0.4, 0.3, 0.3),
        state_path: str | Path = BANDIT_STATE_PATH,
    ):
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy!r}. Options: {list(STRATEGIES)}")
        StrategyClass = STRATEGIES[strategy]
        self.strategy = StrategyClass()
        self.registry = registry
        self.logger = logger or DecisionLogger()
        self.reward_calc = RewardCalculator(*reward_weights)
        self.state_path = Path(state_path)

        # Load persisted state if it exists
        if self.state_path.exists():
            try:
                self.strategy.load_state(str(self.state_path))
            except Exception:
                pass  # fresh start if state is corrupted

        # Auto-warm-start: if compatibility_matrix.json exists and bandit state is fresh
        # (no routing decisions yet — t==0), inject benchmark priors.
        from .warm_start import load_compatibility_matrix, warm_start_from_matrix
        from .strategies.linucb import LinUCBRouter as _LinUCBRouter
        if isinstance(self.strategy, _LinUCBRouter) and not self.strategy.A:
            matrix = load_compatibility_matrix()
            if matrix:
                warm_start_from_matrix(self.strategy, matrix)

    def route(self, task, available_agents: list[str] | None = None, queue_depth_norm: float = 0.0) -> str:
        """Select the best agent for this task. Returns agent name.

        available_agents: if provided, restricts selection to these agent names.
        The gateway passes capable-only agents so the bandit never routes a code
        task to a non-code-capable agent during cold start.

        queue_depth_norm: optional fraction of resource group capacity in use at selection time.
        """
        context = TaskContext.from_task(task)
        if queue_depth_norm > 0.0:
            import dataclasses as _dc
            context = _dc.replace(context, queue_depth_norm=queue_depth_norm)
        available = available_agents if available_agents is not None else self._available_agents()

        if not available:
            raise RuntimeError("No agents registered in the adapter registry")

        agent = self.strategy.select_agent(context, available)

        self.logger.log_decision(
            task=task,
            context=context,
            selected_agent=agent,
            available_agents=available,
            strategy=self.strategy.name,
            scores=self.strategy.get_scores(),
        )

        return agent

    def observe(self, task, outcome: TaskOutcome) -> None:
        """Update the bandit after observing the result of task execution."""
        context = TaskContext.from_task(task)
        reward = self.reward_calc.compute(outcome)

        self.strategy.update(context, outcome.agent_name, reward)

        # Persist state after every update
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.strategy.save_state(str(self.state_path))

        self.logger.log_outcome(
            task=task,
            outcome=outcome,
            reward=reward,
        )

    def set_strategy(self, name: str) -> None:
        """Switch routing strategy at runtime. Resets to fresh state."""
        if name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {name!r}. Options: {list(STRATEGIES)}")
        StrategyClass = STRATEGIES[name]
        self.strategy = StrategyClass()
        # Clear stale state file from previous strategy
        if self.state_path.exists():
            self.state_path.unlink()

    def get_stats(self) -> dict:
        """Return current router state for the API/dashboard."""
        return {
            "strategy": self.strategy.name,
            "t": getattr(self.strategy, 't', 0),
            "scores": self.strategy.get_scores(),
        }

    def score_all(self, task, available_agents: list[str] | None = None, queue_depth_norm: float = 0.0) -> dict:
        """Read-only UCB scoring — no logged decision, no state mutation.

        Used by POST /api/routing/dry-run.

        queue_depth_norm: optional fraction of resource group capacity in use at selection time.
        """
        context = TaskContext.from_task(task)
        if queue_depth_norm > 0.0:
            import dataclasses as _dc
            context = _dc.replace(context, queue_depth_norm=queue_depth_norm)
        available = available_agents if available_agents is not None else self._available_agents()
        scores = self.strategy.compute_scores(context, available)
        return {
            "strategy": self.strategy.name,
            "scores": scores,
        }

    def _available_agents(self) -> list[str]:
        if self.registry is not None:
            return [a.name for a in self.registry.all()]
        return ["ollama"]  # fallback when no registry
