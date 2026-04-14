"""
BanditRouter — self-learning router for Mahoraga.

Wraps a RoutingStrategy and integrates it with the adapter registry,
decision logging, and the existing gateway pipeline.

State persists to ~/.mahoraga/bandit_state.json across restarts.
Reward weight learning persists to ~/.mahoraga/bandit_state.learner.json.
Episodic memory persists to ~/.mahoraga/episodic_memory.{bin,meta.json}.
"""
from __future__ import annotations
import dataclasses
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

TUNED_HYPERPARAMS_PATH = Path.home() / ".mahoraga" / "tuned_hyperparams.json"

from .context import TaskContext
from .reward import RewardCalculator, TaskOutcome
from .reward_learner import RewardWeightLearner
from .episodic_memory import EpisodicMemory, MEMORY_ALPHA
from .decision_log import DecisionLogger
from .strategies import StaticRouter, UCB1Router, ThompsonSamplingRouter, LinUCBRouter
from .warm_start import load_compatibility_matrix, warm_start_from_matrix

if TYPE_CHECKING:
    from ..adapters.registry import AdapterRegistry

BANDIT_STATE_PATH = Path.home() / ".mahoraga" / "bandit_state.json"

STRATEGIES: dict[str, Any] = {
    "static":   StaticRouter,
    "ucb1":     UCB1Router,
    "thompson": ThompsonSamplingRouter,
    "linucb":   LinUCBRouter,
}


class BanditRouter:
    """Routes tasks to agents using a bandit strategy and learns from outcomes.

    Three learning layers work together:
      1. LinUCB (or UCB1/Thompson) — contextual bandit, updated on every outcome.
      2. RewardWeightLearner — OLS adapts per-bucket reward weights after 100+ tasks.
      3. EpisodicMemory — HNSW kNN retrieval blends past episode rewards into the
         agent-selection score as a 20 % bias term (MEMORY_ALPHA).
    """

    def __init__(
        self,
        strategy: str = "linucb",
        registry: Any = None,
        logger: DecisionLogger | None = None,
        state_path: str | Path = BANDIT_STATE_PATH,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy!r}. Options: {list(STRATEGIES)}")
        strategy_cls = STRATEGIES[strategy]
        self.strategy = strategy_cls()
        self.registry = registry
        self.logger = logger or DecisionLogger()
        self.state_path = Path(state_path)

        # Layer 2: OLS reward weight learner
        self._learner = RewardWeightLearner(state_path=self.state_path)
        self.reward_calc = RewardCalculator(learner=self._learner)

        # Layer 3: episodic memory — stored in the same dir as bandit_state.json
        self._memory = EpisodicMemory(state_dir=self.state_path.parent)

        # Load persisted bandit state if it exists
        if self.state_path.exists():
            try:
                self.strategy.load_state(str(self.state_path))
            except (ValueError, KeyError, TypeError):
                pass  # fresh start if state is corrupted or dimension mismatch

        # Load tuned hyperparameters from pareto-sweep if available.
        # Applied before warm-start so the injected pseudo-obs use the tuned alpha.
        if isinstance(self.strategy, LinUCBRouter) and TUNED_HYPERPARAMS_PATH.exists():
            try:
                tuned = json.loads(TUNED_HYPERPARAMS_PATH.read_text())
                if "alpha" in tuned:
                    self.strategy.alpha = float(tuned["alpha"])
                if "gamma" in tuned:
                    self.strategy.decay = float(tuned["gamma"])
                _log.info(
                    "bandit_router: loaded tuned hyperparams (alpha=%.2f, gamma=%.2f)",
                    self.strategy.alpha, self.strategy.decay,
                )
            except Exception as exc:
                _log.warning("bandit_router: failed to load tuned_hyperparams.json (%s)", exc)

        # Auto-warm-start: if this is a fresh LinUCB bandit (no arms yet) and a
        # compatibility matrix exists from a previous benchmark run, inject it as
        # pseudo-observations to skip the cold-start exploration phase.
        if isinstance(self.strategy, LinUCBRouter) and not self.strategy.A:
            matrix = load_compatibility_matrix()
            if matrix:
                warm_start_from_matrix(self.strategy, matrix)


    def route(
        self,
        task: Any,
        available_agents: list[str] | None = None,
        queue_depth_norm: float = 0.0,
    ) -> str:
        """Select the best agent for this task. Returns agent name.

        If episodic memory has enough history, the nearest-neighbour reward biases
        are blended with the bandit's exploitation scores before final selection.

        available_agents: if provided, restricts selection to these agent names.
        queue_depth_norm: fraction of resource group capacity in use at selection time.
        """
        context = TaskContext.from_task(task)
        if queue_depth_norm > 0.0:
            context = dataclasses.replace(
                context,
                queue_depth_norm=queue_depth_norm,
            )
        available = available_agents if available_agents is not None else self._available_agents()

        if not available:
            raise RuntimeError("No agents registered in the adapter registry")

        # Query episodic memory for similarity-weighted reward biases
        vec = context.to_vector()
        memory_biases = self._memory.query_biases(vec, available_agents=available)

        if memory_biases:
            # Re-rank available agents using memory-blended scores
            bandit_scores = self.strategy.compute_scores(context, available)
            blended: dict[str, float] = {}
            for a in available:
                exploit = bandit_scores.get(a, {}).get("exploit", 0.0)
                bias = memory_biases.get(a, exploit)  # fall back to exploit if no bias
                blended[a] = (1.0 - MEMORY_ALPHA) * exploit + MEMORY_ALPHA * bias
            agent = max(available, key=lambda a: blended[a])
            # Still let the strategy tick forward via select_agent for its internal state
            self.strategy.select_agent(context, available)
        else:
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

    def observe(self, task: Any, outcome: TaskOutcome) -> None:
        """Update all three learning layers after observing a task outcome."""
        context = TaskContext.from_task(task)
        reward = self.reward_calc.compute(outcome)

        # Layer 1: bandit update
        self.strategy.update(context, outcome.agent_name, reward)

        # Layer 2: OLS weight learner (successful tasks only)
        if outcome.success:
            self._learner.observe(
                bucket=outcome.bucket,
                latency_s=outcome.latency_s,
                cost_usd=outcome.cost_usd,
                quality=outcome.quality_score,
                reward=reward,
            )

        # Layer 3: episodic memory (all outcomes — failures inform the bandit too)
        self._memory.add(context.to_vector(), agent=outcome.agent_name, reward=reward)

        # Persist bandit state after every update
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.strategy.save_state(str(self.state_path))

        self.logger.log_outcome(task=task, outcome=outcome, reward=reward)

    def set_strategy(self, name: str) -> None:
        """Switch routing strategy at runtime. Resets to fresh state."""
        if name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {name!r}. Options: {list(STRATEGIES)}")
        strategy_cls = STRATEGIES[name]
        self.strategy = strategy_cls()
        if self.state_path.exists():
            self.state_path.unlink()

    def get_stats(self) -> dict[str, Any]:
        """Return current router state for the API/dashboard."""
        return {
            "strategy": self.strategy.name,
            "t": getattr(self.strategy, "t", 0),
            "scores": self.strategy.get_scores(),
            "weight_learning": self._learner.convergence_status(),
            "episodic_memory": {"size": self._memory.size},
        }

    def score_all(
        self,
        task: Any,
        available_agents: list[str] | None = None,
        queue_depth_norm: float = 0.0,
    ) -> dict[str, Any]:
        """Read-only UCB scoring — no logged decision, no state mutation.

        Used by POST /api/routing/dry-run.
        queue_depth_norm: optional fraction of resource group capacity in use.
        """
        context = TaskContext.from_task(task)
        if queue_depth_norm > 0.0:
            context = dataclasses.replace(context, queue_depth_norm=queue_depth_norm)
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
