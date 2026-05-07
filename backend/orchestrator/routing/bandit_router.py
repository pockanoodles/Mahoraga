"""
BanditRouter — self-learning router for Mahoraga.

Wraps a RoutingStrategy and integrates it with the adapter registry,
decision logging, and the existing gateway pipeline.

State persists to ~/.mahoraga-v2/bandit_state.json across restarts.
Reward weight learning persists to ~/.mahoraga-v2/bandit_state.learner.json.
Episodic memory persists to ~/.mahoraga-v2/episodic_memory.{bin,meta.json}.
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

_log = logging.getLogger(__name__)

TUNED_HYPERPARAMS_PATH = Path.home() / ".mahoraga-v2" / "tuned_hyperparams.json"

from .context import TaskContext
from .reward import RewardCalculator, TaskOutcome
from .reward_learner import RewardWeightLearner
from .episodic_memory import EpisodicMemory, MEMORY_ALPHA
from .decision_log import DecisionLogger
from .strategies import (
    StaticRouter, UCB1Router, ThompsonSamplingRouter, LinUCBRouter,
    LinUCBPerBucketRouter,
)
from .strategies.static import classify_bucket
from .warm_start import load_compatibility_matrix, warm_start_from_matrix
from ..config import MahoragaConfig
from ..brain_logger import log_decision as brain_log_decision

if TYPE_CHECKING:
    from ..adapters.registry import AdapterRegistry

BANDIT_STATE_PATH = Path.home() / ".mahoraga-v2" / "bandit_state.json"

# Memory-mode feature flag (locked design decision #8). Resolved per-call so
# that env / config changes take effect without restart. Values:
#   semantic — semantic retrieval with handcraft fallback (default)
#   keyword  — handcraft-only retrieval (v1 behaviour)
#   off      — no memory bias on read; episodes still stored
MEMORY_MODE_SEMANTIC = "semantic"
MEMORY_MODE_KEYWORD = "keyword"
MEMORY_MODE_OFF = "off"
_VALID_MEMORY_MODES = {MEMORY_MODE_SEMANTIC, MEMORY_MODE_KEYWORD, MEMORY_MODE_OFF}
DEFAULT_MEMORY_MODE = MEMORY_MODE_SEMANTIC


def _resolve_memory_mode() -> str:
    """Resolve memory mode: env var > config > default."""
    env = os.environ.get("MAHORAGA_MEMORY_MODE")
    if env:
        normalised = env.strip().lower()
        if normalised in _VALID_MEMORY_MODES:
            return normalised
        _log.warning(
            "MAHORAGA_MEMORY_MODE=%r is invalid; falling back to %s",
            env, DEFAULT_MEMORY_MODE,
        )
    try:
        cfg = MahoragaConfig().get("memory_mode")
    except (KeyError, FileNotFoundError):
        cfg = None
    if cfg in _VALID_MEMORY_MODES:
        return cfg
    return DEFAULT_MEMORY_MODE


def _resolve_memory_alpha() -> float:
    """Resolve memory bias weight: env var > config > MEMORY_ALPHA constant.
    Clamped to [0.0, 1.0]."""
    env = os.environ.get("MAHORAGA_MEMORY_ALPHA")
    if env:
        try:
            v = float(env)
            if 0.0 <= v <= 1.0:
                return v
            _log.warning(
                "MAHORAGA_MEMORY_ALPHA=%r out of [0,1]; falling back to %.2f",
                env, MEMORY_ALPHA,
            )
        except ValueError:
            _log.warning(
                "MAHORAGA_MEMORY_ALPHA=%r is not a number; falling back to %.2f",
                env, MEMORY_ALPHA,
            )
    try:
        cfg = MahoragaConfig().get("memory_alpha")
    except (KeyError, FileNotFoundError):
        cfg = None
    if isinstance(cfg, (int, float)) and 0.0 <= float(cfg) <= 1.0:
        return float(cfg)
    return MEMORY_ALPHA


def _resolve_confidence_weighting() -> bool:
    """When True, the per-agent memory bias is scaled by the agent's
    neighbour-count confidence. With sparse evidence the bias contribution
    fades smoothly to zero rather than being applied at full strength."""
    env = os.environ.get("MAHORAGA_MEMORY_CONFIDENCE_WEIGHTED", "")
    if env.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if env.strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        cfg = MahoragaConfig().get("memory_confidence_weighted")
    except (KeyError, FileNotFoundError):
        cfg = None
    if isinstance(cfg, bool):
        return cfg
    return False


def _resolve_per_bucket_alpha() -> dict[str, float]:
    """Per-bucket α overrides. Returns a {bucket: α} map (possibly empty).

    Sources, in priority order:
      1. MAHORAGA_MEMORY_ALPHA_PER_BUCKET — JSON-encoded mapping
         (e.g. '{"research": 0.0, "code_editing": 0.15}')
      2. config key "memory_alpha_per_bucket" (a dict)

    Buckets not in the map fall through to the global α
    (`MAHORAGA_MEMORY_ALPHA` / `_resolve_memory_alpha()`).

    Use case: empirical per-bucket data (see spec §15.4) shows memory
    helps on deterministic-pattern buckets (refactoring, file_ops) but
    hurts on exploratory buckets (research). Per-bucket α lets us turn
    memory off for buckets where it hurts without disabling globally.
    """
    raw = os.environ.get("MAHORAGA_MEMORY_ALPHA_PER_BUCKET", "").strip()
    if raw:
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning(
                "MAHORAGA_MEMORY_ALPHA_PER_BUCKET is not valid JSON (%s); "
                "ignoring",
                exc,
            )
            mapping = None
        if isinstance(mapping, dict):
            return {
                str(k): float(v)
                for k, v in mapping.items()
                if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0
            }

    try:
        cfg = MahoragaConfig().get("memory_alpha_per_bucket")
    except (KeyError, FileNotFoundError):
        cfg = None
    if isinstance(cfg, dict):
        return {
            str(k): float(v)
            for k, v in cfg.items()
            if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0
        }
    return {}


def _extract_goal(task: Any) -> str:
    """Best-effort extraction of the human-readable task description."""
    if task is None:
        return ""
    if hasattr(task, "goal"):
        return str(getattr(task, "goal") or "")
    if isinstance(task, dict):
        return str(task.get("goal", ""))
    return str(task)


def _hash_goal(text: str) -> Optional[str]:
    """Stable cache/dedup key — must match EmbeddingService normalisation."""
    if not text or not text.strip():
        return None
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()

STRATEGIES: dict[str, Any] = {
    "static":             StaticRouter,
    "ucb1":               UCB1Router,
    "thompson":           ThompsonSamplingRouter,
    "linucb":             LinUCBRouter,
    "linucb_per_bucket":  LinUCBPerBucketRouter,
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

        # Lazy-init embedding service. Loaded on first access; if
        # sentence-transformers isn't installed it returns an unavailable
        # service and we fall back to handcraft retrieval transparently.
        self._embedding_service: Any = None
        self._embedding_init_attempted: bool = False

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
        bench_run_id: int | None = None,
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

        # Apply routing_mode preference
        _mode = MahoragaConfig().get("routing_mode") or "balanced"
        _FREE = {"ollama", "aider", "gemini-cli"}

        if _mode == "local_first":
            free_available = [a for a in available if a in _FREE]
            if free_available:
                available = free_available
        elif _mode == "quality_first":
            pass  # bandit selects freely — cost weight in reward function handles this naturally

        # Query episodic memory for similarity-weighted reward biases.
        # Mode, α, and confidence-weighting are resolved per-call so env/config
        # changes take effect live (important for benchmarks and dry runs).
        memory_mode = _resolve_memory_mode()
        global_alpha = _resolve_memory_alpha()
        per_bucket_alpha = _resolve_per_bucket_alpha()
        confidence_weighted = _resolve_confidence_weighting()

        # Per-bucket gating: when the task's classified bucket has an α
        # override (e.g. {"research": 0.0}), use it; otherwise fall through
        # to the global α. The bucket comes from the same classifier the
        # static router uses, so the names are consistent across
        # production code paths.
        bucket = classify_bucket(context)
        memory_alpha = per_bucket_alpha.get(bucket, global_alpha)

        memory_biases = self._retrieve_memory_biases_rich(
            task=task, context=context, available=available, mode=memory_mode,
        )

        # Only enter the blending branch when memory will *actually*
        # contribute. With memory_alpha == 0 the blending term collapses
        # to (1-0)*exploit + 0*bias = exploit, which loses LinUCB's
        # exploration term — making α=0 behave worse than off-mode. Bail
        # out to the strategy's own selector (UCB-aware) in that case.
        if memory_biases and memory_alpha > 0:
            # Re-rank available agents using memory-blended scores.
            # We blend against the strategy's full UCB score (exploit +
            # exploration) — NOT just exploit. Using only exploit collapses
            # LinUCB to greedy max-θ·x, which loses exploration entirely
            # and biases the ranking toward early-converged arms regardless
            # of the memory bias magnitude. With ucb, α=0 is equivalent to
            # off-mode, and small α values produce smooth interpolation.
            #
            # Effective α per agent is α * confidence(a) when confidence
            # weighting is on; otherwise α * 1.0 = α (legacy behaviour).
            bandit_scores = self.strategy.compute_scores(context, available)
            blended: dict[str, float] = {}
            for a in available:
                arm = bandit_scores.get(a, {})
                # Prefer the full UCB; fall back to exploit if a strategy
                # (e.g. UCB1, Thompson) doesn't expose UCB explicitly.
                ucb = arm.get("ucb", arm.get("exploit", 0.0))
                entry = memory_biases.get(a)
                if entry is None:
                    blended[a] = ucb
                    continue
                conf = entry["confidence"] if confidence_weighted else 1.0
                eff_alpha = memory_alpha * conf
                blended[a] = (1.0 - eff_alpha) * ucb + eff_alpha * entry["bias"]
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
            bench_run_id=bench_run_id,
        )
        try:
            brain_log_decision(
                decision=f"Routed to {agent}",
                reasoning=f"strategy={self.strategy.__class__.__name__}",
                context="mahoraga-router",
            )
        except Exception:
            pass

        return agent

    def log_override(
        self,
        task: Any,
        agent: str,
        bench_run_id: int | None = None,
        available_agents: list[str] | None = None,
    ) -> int:
        """Log a decisions row for a manually-pinned agent (agent_override /
        batch _run_single). Does NOT run the bandit strategy — no learning
        happens from the pick itself. The row is marked `strategy='override'`
        so bandit analytics can exclude it, and `observe()` can still back-fill
        success/reward/quality via log_outcome() on the same task_id.

        Returns the inserted decisions.id.
        """
        available = available_agents if available_agents is not None else self._available_agents()
        context = TaskContext.from_task(task)
        return self.logger.log_decision(
            task=task,
            context=context,
            selected_agent=agent,
            available_agents=available,
            strategy="override",
            scores=None,
            bench_run_id=bench_run_id,
        )

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

        # Layer 3: episodic memory (all outcomes — failures inform the bandit too).
        # Even in mode=off we store the handcraft history so a future mode swap
        # has data to retrieve. Only the *retrieval* side respects the mode.
        memory_mode = _resolve_memory_mode()
        self._store_episode(
            task=task, context=context, agent=outcome.agent_name,
            reward=reward, mode=memory_mode,
        )

        # Persist bandit state after every update
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.strategy.save_state(str(self.state_path))

        self.logger.log_outcome(task=task, outcome=outcome, reward=reward)

    def apply_implicit_reward(
        self,
        task_id: str,
        agent_name: str,
        task_goal: str,
        implicit_signal: float,
    ) -> None:
        """Nudge the bandit with an implicit quality signal (retry=0.0, accept=0.6).

        Does NOT call reward_calc — the signal is already a reward value.
        Does NOT update the OLS learner — we lack the full outcome.
        """
        # Build a minimal task-like object so TaskContext.from_task() has what it needs.
        @dataclasses.dataclass
        class _MinimalTask:
            title: str
            goal: str

        context = TaskContext.from_task(_MinimalTask(title=task_goal, goal=task_goal))

        self.strategy.update(context, agent_name, implicit_signal)
        # Implicit-reward path: route() already handled mode-resolved storage
        # for the explicit task; this is a separate signal so we mirror the
        # same mode-aware storage logic.
        memory_mode = _resolve_memory_mode()
        self._store_episode(
            task=_MinimalTask(title=task_goal, goal=task_goal),
            context=context, agent=agent_name, reward=implicit_signal,
            mode=memory_mode,
        )

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.strategy.save_state(str(self.state_path))

        _log.debug(
            "implicit reward applied: agent=%s signal=%.2f task_id=%s",
            agent_name,
            implicit_signal,
            task_id,
        )

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
            "episodic_memory": {
                "size": self._memory.size,
                "semantic_size": self._memory.semantic_size,
                "memory_mode": _resolve_memory_mode(),
                "memory_alpha": _resolve_memory_alpha(),
                "memory_alpha_per_bucket": _resolve_per_bucket_alpha(),
                "confidence_weighted": _resolve_confidence_weighting(),
            },
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

    # ── Memory-mode helpers (Phase 3) ──────────────────────────────────────────

    def _get_embedding_service(self) -> Any:
        """Lazy-load the embedding service. Returns None if unavailable."""
        if self._embedding_init_attempted:
            return self._embedding_service
        self._embedding_init_attempted = True
        try:
            from .embeddings import EmbeddingService
            self._embedding_service = EmbeddingService()
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "bandit_router: failed to init EmbeddingService (%s); "
                "semantic memory disabled",
                exc,
            )
            self._embedding_service = None
        return self._embedding_service

    def _encode_query(self, text: str) -> Optional[np.ndarray]:
        """Best-effort embed. Returns None if text is empty or service is offline."""
        if not text or not text.strip():
            return None
        svc = self._get_embedding_service()
        if svc is None or not svc.available:
            return None
        return svc.encode(text)

    def _retrieve_memory_biases(
        self,
        task: Any,
        context: TaskContext,
        available: list[str],
        mode: str,
    ) -> dict[str, float]:
        """Mode-aware retrieval — backward-compat wrapper that returns the
        flat {agent: bias} shape. New callers should use the _rich variant."""
        rich = self._retrieve_memory_biases_rich(
            task=task, context=context, available=available, mode=mode,
        )
        return {a: data["bias"] for a, data in rich.items()}

    def _retrieve_memory_biases_rich(
        self,
        task: Any,
        context: TaskContext,
        available: list[str],
        mode: str,
    ) -> dict[str, dict[str, float]]:
        """Mode-aware retrieval that includes per-agent confidence and count.
        Returns {} when mode=off or memory is empty."""
        if mode == MEMORY_MODE_OFF:
            return {}
        if mode == MEMORY_MODE_SEMANTIC:
            embedding = self._encode_query(_extract_goal(task))
            if embedding is not None:
                semantic_biases = self._memory.query_semantic_with_confidence(
                    embedding, available_agents=available,
                )
                if semantic_biases:
                    return semantic_biases
            # Fall through to handcraft on cold start, embedding-service
            # outage, or insufficient embedded neighbours.
        return self._memory.query_biases_with_confidence(
            context.to_vector(), available_agents=available,
        )

    def _store_episode(
        self,
        task: Any,
        context: TaskContext,
        agent: str,
        reward: float,
        mode: str,
    ) -> None:
        """Mode-aware ingest. Always stores handcraft. Adds embedding when
        mode=semantic and the embedding service is available."""
        embedding: Optional[np.ndarray] = None
        task_hash: Optional[str] = None
        if mode == MEMORY_MODE_SEMANTIC:
            goal_text = _extract_goal(task)
            embedding = self._encode_query(goal_text)
            if embedding is not None:
                task_hash = _hash_goal(goal_text)
        self._memory.add_episode(
            handcraft_vector=context.to_vector(),
            agent=agent,
            reward=reward,
            embedding=embedding,
            task_hash=task_hash,
        )
