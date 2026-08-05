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
from . import uncertainty as _uncertainty
from . import brain_retrieval as _brain_retrieval
from . import composer as _composer
from . import quality_predictor as _quality_predictor
from . import policy_correction as _policy_correction
from .budget_pacer import BUDGET_PACER_STATE_PATH, BudgetPacer
from .drift_detector import DriftDetector, resolve_enabled as _drift_enabled
from .execution_pool import get_default_pool
from .quarantine import (
    QUARANTINE_STATE_PATH,
    QuarantineManager,
    resolve_enabled as _quarantine_enabled,
)
from .strategies import (
    StaticRouter, UCB1Router, ThompsonSamplingRouter, LinUCBRouter,
    LinUCBPerBucketRouter,
)
from .strategies.static import classify_bucket
from .warm_start import load_compatibility_matrix, warm_start_from_matrix
from ..config import MahoragaConfig

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


def _route_meta_key(task: Any) -> Optional[str]:
    """Stable key for the in-process route→observe meta dict.

    Prefers task.id (matches the decisions-DB join column) and falls back
    to a hash of the goal text. Returns None for genuinely identifier-less
    inputs; callers can still observe(), they just won't get the weight
    threaded through (defaults to 1.0).
    """
    if task is None:
        return None
    if hasattr(task, "id"):
        tid = getattr(task, "id")
        if tid is not None:
            return f"id:{tid}"
    if isinstance(task, dict):
        if task.get("id") is not None:
            return f"id:{task.get('id')}"
    goal = _extract_goal(task)
    h = _hash_goal(goal)
    return f"goal:{h}" if h else None

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

        # F1 budget pacer: persists across restarts so rolling-average
        # state survives FastAPI reloads. Loaded BEFORE the reward calc
        # so the calc gets the populated pacer reference at construction.
        self._budget_pacer = BudgetPacer.load()

        # F5 drift detector + quarantine manager. Drift is in-memory only
        # (rebuilds in a few episodes after a restart); quarantine state
        # persists so a broken agent stays excluded across reloads.
        self._drift = DriftDetector()
        self._quarantine = QuarantineManager.load()
        # Marks the next observation as a recovery probe. Set in route()
        # when probe scheduler picks the agent; consumed in observe().
        self._pending_probe: Optional[dict[str, str]] = None

        # Layer 2: OLS reward weight learner
        self._learner = RewardWeightLearner(state_path=self.state_path)
        self.reward_calc = RewardCalculator(
            learner=self._learner,
            pacer=self._budget_pacer,
        )

        # Layer 3: episodic memory — stored in the same dir as bandit_state.json
        self._memory = EpisodicMemory(state_dir=self.state_path.parent)

        # Lazy-init embedding service. Loaded on first access; if
        # sentence-transformers isn't installed it returns an unavailable
        # service and we fall back to handcraft retrieval transparently.
        self._embedding_service: Any = None
        self._embedding_init_attempted: bool = False

        # A2: most-recent uncertainty hint (telemetry / API surface).
        self._last_uncertainty: Optional[_uncertainty.UncertaintyHint] = None

        # A4: lazy-built brain index + most-recent retrieved entries.
        # Built on first route() when MAHORAGA_BRAIN_INTEGRATION_ENABLED is on.
        self._brain_index: Optional[_brain_retrieval.BrainIndex] = None
        self._brain_init_attempted: bool = False
        self._last_brain_hits: list[_brain_retrieval.BrainHit] = []

        # Cross-axis composer: most-recent ComposedDecision (telemetry).
        self._last_composed: Optional[_composer.ComposedDecision] = None

        # A1 off-policy correction: route() captures meta about each
        # decision (bandit_pick, ucb_scores, bandit_probs, override info,
        # importance_weight) keyed by a stable task identifier. observe()
        # pulls the entry and threads weight into strategy.update().
        # Bounded FIFO so a long-running service doesn't grow unbounded.
        self._pending_route_meta: dict[str, dict[str, Any]] = {}
        self._pending_route_meta_max = 1024

        # Load persisted bandit state if it exists
        if self.state_path.exists():
            try:
                self.strategy.load_state(str(self.state_path))
            except (ValueError, KeyError, TypeError):
                pass  # fresh start if state is corrupted or dimension mismatch

        # Load tuned hyperparameters from pareto-sweep if available.
        # Applied before warm-start so the injected pseudo-obs use the tuned alpha.
        # Duck-type so LinUCBPerBucketRouter (not a subclass of LinUCBRouter) also benefits.
        if (
            hasattr(self.strategy, "alpha")
            and hasattr(self.strategy, "decay")
            and TUNED_HYPERPARAMS_PATH.exists()
        ):
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

        # Auto-warm-start: if this is a fresh bandit (no arms yet) and a compatibility
        # matrix exists from a previous benchmark run, inject it as pseudo-observations.
        # Duck-type via inject_pseudo_obs so LinUCBPerBucketRouter is included.
        if hasattr(self.strategy, "inject_pseudo_obs") and not self.strategy.A:
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
        # F2: when caller doesn't supply an explicit queue_depth_norm,
        # read the live value from the process-wide ExecutionPool. This
        # turns context feature 9 into a real contention signal — the
        # bandit can finally learn "if the queue is full, prefer fast
        # agents." Explicit non-zero overrides still win (used by tests
        # and historical sequential paths).
        effective_qdn = queue_depth_norm
        if effective_qdn <= 0.0:
            try:
                effective_qdn = get_default_pool().queue_depth_norm
            except Exception:  # noqa: BLE001
                effective_qdn = 0.0
        if effective_qdn > 0.0:
            context = dataclasses.replace(
                context,
                queue_depth_norm=effective_qdn,
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

        # F1 budget pacer hard-limit filter. Estimates per-task cost from
        # adapter capabilities (or 0 for unknowns) and removes any agent
        # whose estimate exceeds the hard limit. Falls back to the
        # cheapest agent if everything would be filtered — guard rail
        # never starves the bandit of a choice.
        if self._budget_pacer is not None and self.registry is not None:
            cost_estimates = self._estimate_agent_costs(task, available)
            available = self._budget_pacer.filter_agents(available, cost_estimates)

        # F5 quarantine filter. Cells with active drift quarantines are
        # excluded from selection until probe-driven recovery clears
        # them. Per-bucket: an agent quarantined in "code" can still be
        # picked for "research". If the filter would empty the candidate
        # set, fall back to the least-bad quarantined agent (smallest
        # deviation_sigmas) so the bandit always has something to pick.
        # Bucket is computed once here and reused below for memory α
        # resolution; the F5 filter runs BEFORE memory blending so
        # quarantined cells never influence retrieval-driven picks.
        bucket = classify_bucket(context)
        probe_target: Optional[str] = None
        if _quarantine_enabled() and self._quarantine is not None:
            quarantined_in_bucket = set(
                self._quarantine.quarantined_in_bucket(bucket)
            )
            filtered = [a for a in available if a not in quarantined_in_bucket]
            if not filtered and available:
                lb = self._quarantine.least_bad_in_bucket(bucket)
                if lb in available:
                    _log.warning(
                        "quarantine: every agent in %s quarantined; "
                        "falling back to least-bad %s",
                        bucket, lb,
                    )
                    filtered = [lb]
            available = filtered or available
            # Probe scheduler — short-circuits the bandit on tick boundary
            # to send this task to a quarantined agent as a recovery probe.
            probe_target = self._quarantine.maybe_probe(
                bucket, self._available_agents() if self.registry else available,
            )

        # Query episodic memory for similarity-weighted reward biases.
        # Mode, α, and confidence-weighting are resolved per-call so env/config
        # changes take effect live (important for benchmarks and dry runs).
        memory_mode = _resolve_memory_mode()
        global_alpha = _resolve_memory_alpha()
        per_bucket_alpha = _resolve_per_bucket_alpha()
        confidence_weighted = _resolve_confidence_weighting()

        # Per-bucket gating: when the task's classified bucket has an α
        # override (e.g. {"research": 0.0}), use it; otherwise fall through
        # to the global α. `bucket` was computed earlier for the F5
        # quarantine filter; reuse the same value here so both layers
        # see the identical classification.
        memory_alpha = per_bucket_alpha.get(bucket, global_alpha)

        memory_biases = self._retrieve_memory_biases_rich(
            task=task, context=context, available=available, mode=memory_mode,
        )

        # Only enter the blending branch when memory will *actually*
        # contribute. With memory_alpha == 0 the blending term collapses
        # to (1-0)*exploit + 0*bias = exploit, which loses LinUCB's
        # exploration term — making α=0 behave worse than off-mode. Bail
        # out to the strategy's own selector (UCB-aware) in that case.
        # We compute scores up-front so the same dict drives both the
        # memory-blend ranking AND the A2 uncertainty hint. compute_scores()
        # is idempotent (no t tick), so it's safe to call before
        # select_agent().
        precomputed_scores = self.strategy.compute_scores(context, available)

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
            bandit_scores = precomputed_scores
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

        # Snapshot the bandit-with-memory pick BEFORE the composer can override
        # it. Used by A1 off-policy correction to compute importance weights.
        bandit_pick = agent

        # F5 recovery probe — overrides the bandit pick on tick boundaries
        # to send this task to a quarantined agent. If the probe succeeds
        # (reward >= probe_quality_floor), the cell moves toward release;
        # after auto_release consecutive successes it leaves quarantine.
        # observe() reads `_pending_probe` to record the outcome.
        if probe_target is not None:
            agent = probe_target
            self._pending_probe = {"bucket": bucket, "agent": probe_target}
            _log.info("quarantine probe: routing %s task to %s", bucket, probe_target)
        else:
            self._pending_probe = None

        # A2: compute confidence-aware escalation hint. Pure read-only
        # signal — caller decides whether to act on `should_escalate`.
        # Uses precomputed scores so we don't double-tick the bandit.
        try:
            self._last_uncertainty = _uncertainty.compute_hint(
                selected_agent=agent,
                scores=precomputed_scores,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("uncertainty hint failed: %s", exc)
            self._last_uncertainty = None

        # A4: brain retrieval. Read-only context signal — does NOT alter
        # the agent pick yet. Surfaces top-k similar brain entries on
        # `_last_brain_hits` for telemetry / dashboards. Gated by env so
        # we don't pay the embed cost on every route() unless enabled.
        self._last_brain_hits = []
        if _brain_retrieval.resolve_enabled():
            try:
                idx = self._get_brain_index()
                if idx is not None and idx.available:
                    self._last_brain_hits = idx.query(
                        _extract_goal(task),
                        k=_brain_retrieval.resolve_top_k(),
                    )
            except Exception as exc:  # noqa: BLE001
                _log.warning("brain retrieval failed: %s", exc)

        # Cross-axis composer: combine A2/A3/A4 signals into one structured
        # decision. Pass-through unless MAHORAGA_COMPOSER_ENABLED is set —
        # but contributors / signals are recorded either way for shadow
        # telemetry. A3 predictions are looked up only if a trained model
        # exists on disk (lazy via quality_predictor.get_model()).
        try:
            a3_predictions: Optional[dict[str, float]] = None
            qmodel = _quality_predictor.get_model()
            if qmodel is not None:
                hc = context.to_vector()
                a3_predictions = {
                    a: qmodel.predict_proba(hc, a) for a in available
                }
            self._last_composed = _composer.compose_decision(
                bandit_pick=agent,
                available=available,
                uncertainty=self._last_uncertainty,
                a3_predictions=a3_predictions,
                brain_hits=self._last_brain_hits or None,
            )
            # If the composer is enabled AND it overrode the pick, honour it.
            if self._last_composed.enabled and self._last_composed.agent != agent:
                _log.info(
                    "composer overrode bandit pick: %s → %s (%s)",
                    agent, self._last_composed.agent,
                    [a["kind"] for a in self._last_composed.adjustments],
                )
                agent = self._last_composed.agent
        except Exception as exc:  # noqa: BLE001
            _log.warning("composer failed: %s", exc)
            self._last_composed = None

        # A1 off-policy correction: compute importance weight and stash
        # route meta keyed by task identifier. observe() will read it.
        try:
            bandit_probs = _policy_correction.bandit_probs_from_scores(
                precomputed_scores
            )
            iw = _policy_correction.importance_weight(
                bandit_pick=bandit_pick,
                final_agent=agent,
                scores=precomputed_scores,
            )
            override_reason: Optional[str] = None
            if self._last_composed and self._last_composed.adjustments:
                override_kinds = [
                    a["kind"] for a in self._last_composed.adjustments
                    if a.get("kind", "").endswith("_override")
                ]
                if override_kinds:
                    override_reason = override_kinds[0]
            meta_key = _route_meta_key(task)
            if meta_key is not None:
                self._stash_route_meta(meta_key, {
                    "bandit_pick": bandit_pick,
                    "final_pick": agent,
                    "ucb_scores": {
                        a: float(s.get("ucb", s.get("exploit", 0.0)))
                        for a, s in precomputed_scores.items()
                    },
                    "bandit_probs": bandit_probs,
                    "override_reason": override_reason,
                    "importance_weight": iw,
                })
        except Exception as exc:  # noqa: BLE001
            _log.warning("off-policy meta capture failed: %s", exc)

        # Pull the just-stashed off-policy meta back out so the same fields
        # land in the decisions DB as the bandit will use on observe().
        meta_for_log = self._pending_route_meta.get(_route_meta_key(task) or "", {})

        # A5 shadow telemetry: capture the composer's would-be decision
        # (always populated, even when disabled — see ComposedDecision
        # docstring) plus the input signals that drove it.
        composer = self._last_composed
        composer_would_pick = (
            composer.would_be_agent if composer else None
        )
        composer_would_escalate = (
            composer.would_be_escalate if composer else None
        )
        a3_predictions_log: Optional[dict] = None
        try:
            qmodel_log = _quality_predictor.get_model()
            if qmodel_log is not None:
                hc_log = context.to_vector()
                a3_predictions_log = {
                    a: round(float(qmodel_log.predict_proba(hc_log, a)), 4)
                    for a in available
                }
        except Exception:  # noqa: BLE001
            a3_predictions_log = None
        brain_hit_count_log = len(self._last_brain_hits) if self._last_brain_hits else 0
        brain_top_sim_log = (
            max((h.similarity for h in self._last_brain_hits), default=None)
            if self._last_brain_hits else None
        )

        self.logger.log_decision(
            task=task,
            context=context,
            selected_agent=agent,
            available_agents=available,
            strategy=self.strategy.name,
            scores=self.strategy.get_scores(),
            bench_run_id=bench_run_id,
            bandit_pick=meta_for_log.get("bandit_pick", bandit_pick),
            ucb_scores=meta_for_log.get("ucb_scores"),
            bandit_probs=meta_for_log.get("bandit_probs"),
            override_reason=meta_for_log.get("override_reason"),
            importance_weight=meta_for_log.get("importance_weight", 1.0),
            composer_would_pick=composer_would_pick,
            composer_would_escalate=composer_would_escalate,
            a3_predictions=a3_predictions_log,
            brain_hit_count=brain_hit_count_log,
            brain_top_sim=brain_top_sim_log,
            escalation_strategy=(
                composer.escalation_strategy if composer else None
            ),
        )

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

        # A1 off-policy correction: pull the importance weight stashed by
        # route(). Falls back to 1.0 (standard update) when route() wasn't
        # called for this task — e.g. orch-batch overrides or implicit-only
        # paths.
        route_meta = self._pop_route_meta(task)
        weight = float(route_meta["importance_weight"]) if route_meta else 1.0

        # Layer 1: bandit update — weighted when the composer overrode.
        try:
            self.strategy.update(context, outcome.agent_name, reward, weight=weight)
        except TypeError:
            # Strategies that haven't adopted the weight kwarg fall back to
            # the standard update. Acceptable for UCB1/Thompson — they
            # don't materially benefit from importance weighting.
            self.strategy.update(context, outcome.agent_name, reward)

        # Layer 2: OLS weight learner (successful tasks only)
        if outcome.success:
            self._learner.observe(
                bucket=outcome.bucket,
                latency_s=outcome.latency_s,
                cost_usd=outcome.cost_usd,
                quality=outcome.quality_score,
                reward=reward,
                correctness=outcome.correctness if outcome.correctness is not None else 1.0,
            )

        # Layer 3: episodic memory (all outcomes — failures inform the bandit too).
        # Even in mode=off we store the handcraft history so a future mode swap
        # has data to retrieve. Only the *retrieval* side respects the mode.
        memory_mode = _resolve_memory_mode()
        self._store_episode(
            task=task, context=context, agent=outcome.agent_name,
            reward=reward, mode=memory_mode,
        )

        # F1 budget pacer: feed observed cost into the rolling window
        # and run one dual-ascent step. Persists alongside bandit state.
        try:
            self._budget_pacer.update(outcome.cost_usd)
            self._budget_pacer.save()
        except Exception as exc:  # noqa: BLE001
            _log.warning("budget_pacer update/save failed: %s", exc)

        # F5 drift detection + probe accounting.
        try:
            if _drift_enabled() and self._drift is not None:
                alert = self._drift.check(
                    bucket=outcome.bucket,
                    agent=outcome.agent_name,
                    reward=reward,
                )
                if alert is not None and self._quarantine is not None:
                    self._quarantine.quarantine(alert, kind="drift_auto")
                    self.logger.log_drift_event(alert)
            # Probe accounting: if route() routed this task as a probe,
            # record the outcome and possibly auto-release.
            if self._pending_probe is not None and self._quarantine is not None:
                pp = self._pending_probe
                if pp["agent"] == outcome.agent_name:
                    probe_status = self._quarantine.record_probe(
                        bucket=pp["bucket"],
                        agent=pp["agent"],
                        reward=reward,
                    )
                    if probe_status == "released":
                        # Close out the corresponding drift_events row(s).
                        self.logger.mark_drift_resolved(
                            bucket=pp["bucket"],
                            agent=pp["agent"],
                            resolution="auto_released",
                        )
                self._pending_probe = None
            if self._quarantine is not None:
                self._quarantine.save()
        except Exception as exc:  # noqa: BLE001
            _log.warning("drift/quarantine update failed: %s", exc)

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

        # Backfill the corresponding decision row's outcome columns IF it
        # hasn't already been labelled explicitly. This is what unblocks
        # A3 (`orch quality train`) on real organic traffic — without it
        # the only labels in the decisions DB came from explicit observe()
        # calls, which only fire on full task completion.
        try:
            updated = self.logger.log_implicit_outcome(
                task_id=task_id,
                task_goal=task_goal,
                agent_name=agent_name,
                implicit_signal=implicit_signal,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("log_implicit_outcome failed: %s", exc)
            updated = False

        _log.debug(
            "implicit reward applied: agent=%s signal=%.2f task_id=%s db_updated=%s",
            agent_name,
            implicit_signal,
            task_id,
            updated,
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
        last_unc = self._last_uncertainty
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
            "uncertainty": {
                "enabled": _uncertainty.resolve_enabled(),
                "policy": _uncertainty.resolve_policy(),
                "variance_threshold": _uncertainty.resolve_variance_threshold(),
                "gap_threshold": _uncertainty.resolve_gap_threshold(),
                "last": last_unc.to_dict() if last_unc else None,
            },
            "brain": {
                "enabled": _brain_retrieval.resolve_enabled(),
                "top_k": _brain_retrieval.resolve_top_k(),
                "indexed": (
                    self._brain_index.size if self._brain_index else 0
                ),
                "available": (
                    bool(self._brain_index and self._brain_index.available)
                ),
                "last_hits": [h.to_dict() for h in self._last_brain_hits],
            },
            "composer": (
                self._last_composed.to_dict() if self._last_composed else None
            ),
            "budget_pacer": (
                self._budget_pacer.to_status_dict()
                if self._budget_pacer is not None else None
            ),
        }

    def get_last_uncertainty(self) -> Optional[_uncertainty.UncertaintyHint]:
        """Return the most-recent A2 uncertainty hint, or None if route()
        has not been called yet."""
        return self._last_uncertainty

    def _get_brain_index(self) -> Optional["_brain_retrieval.BrainIndex"]:
        """Lazy-build the brain index. Returns None if disabled or unavailable.

        Index reuses the existing embedding service so we don't load MiniLM
        twice. Built once per process; rebuild via `reset_brain_index()`.
        """
        if self._brain_init_attempted:
            return self._brain_index
        self._brain_init_attempted = True
        svc = self._get_embedding_service()
        if svc is None or not getattr(svc, "available", False):
            self._brain_index = None
            return None
        try:
            idx = _brain_retrieval.BrainIndex(embedding_service=svc)
            n = idx.build()
            _log.info("bandit_router: brain index built with %d entries", n)
            self._brain_index = idx
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "bandit_router: brain index build failed (%s); A4 disabled",
                exc,
            )
            self._brain_index = None
        return self._brain_index

    def _estimate_agent_costs(
        self, task: Any, agent_names: list[str],
    ) -> dict[str, float]:
        """Per-agent USD cost estimates for the F1 hard-limit filter.

        Asks each adapter for its `estimate_cost(task)`. Adapters that
        can't be reached or that lack a Task-shaped object return 0.0.
        Defensive: never raises into route()."""
        out: dict[str, float] = {}
        if self.registry is None:
            return out
        for name in agent_names:
            try:
                adapter = self.registry.get(name)
            except Exception:  # noqa: BLE001
                adapter = None
            if adapter is None:
                out[name] = 0.0
                continue
            try:
                est = adapter.estimate_cost(task)
                out[name] = float(getattr(est, "estimated_cost_usd", 0.0))
            except Exception:  # noqa: BLE001
                out[name] = 0.0
        return out

    def _stash_route_meta(self, key: str, meta: dict[str, Any]) -> None:
        """Bounded FIFO store. Drops oldest entries past the cap."""
        self._pending_route_meta[key] = meta
        if len(self._pending_route_meta) > self._pending_route_meta_max:
            # Drop the oldest ~10% in one go; cheap amortised cost.
            drop_n = max(1, self._pending_route_meta_max // 10)
            for k in list(self._pending_route_meta.keys())[:drop_n]:
                del self._pending_route_meta[k]

    def _pop_route_meta(self, task: Any) -> Optional[dict[str, Any]]:
        """Look up + remove the route meta entry for this task. Returns
        None if route() wasn't called (e.g. apply_implicit_reward path)."""
        key = _route_meta_key(task)
        if key is None:
            return None
        return self._pending_route_meta.pop(key, None)

    def reset_brain_index(self) -> None:
        """Force rebuild of the brain index on next route() call."""
        self._brain_index = None
        self._brain_init_attempted = False
        self._last_brain_hits = []

    def get_last_brain_hits(self) -> list:
        """Return most-recent A4 brain retrieval hits as plain dicts."""
        return [h.to_dict() for h in self._last_brain_hits]

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

    def _brain_summary_for(self, task_text: str) -> str:
        """A4: derive a keyword summary of the top brain hits for this task.

        Returns the cached `_last_brain_hits` summary when a brain query
        already ran in route(); otherwise returns "" (cold path / brain
        disabled). Pure read of state populated earlier in the call.
        """
        if not self._last_brain_hits:
            return ""
        try:
            return _brain_retrieval.summarise_brain_hits(
                self._last_brain_hits,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("summarise_brain_hits failed: %s", exc)
            return ""

    def _retrieve_memory_biases_rich(
        self,
        task: Any,
        context: TaskContext,
        available: list[str],
        mode: str,
    ) -> dict[str, dict[str, float]]:
        """Mode-aware retrieval that includes per-agent confidence and count.
        Returns {} when mode=off or memory is empty.

        A4: when brain hits are present, the task text is augmented with
        their keyword summary BEFORE embedding. The query embedding then
        captures project-specific context — "race condition in connection
        pool [PostgreSQL, pgBouncer]" retrieves different episodes than
        bare "race condition in connection pool".
        """
        if mode == MEMORY_MODE_OFF:
            return {}
        if mode == MEMORY_MODE_SEMANTIC:
            goal = _extract_goal(task)
            augmented = _brain_retrieval.augment_for_embedding(
                goal, self._brain_summary_for(goal),
            )
            embedding = self._encode_query(augmented)
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
        mode=semantic and the embedding service is available.

        A4: storage uses the SAME augmented embedding the retrieval side
        uses, so retrieval-time queries land in the same project-context
        cluster the storage built. task_hash is computed from the
        unaugmented goal so dedup behaviour is unchanged.
        """
        embedding: Optional[np.ndarray] = None
        task_hash: Optional[str] = None
        if mode == MEMORY_MODE_SEMANTIC:
            goal_text = _extract_goal(task)
            augmented = _brain_retrieval.augment_for_embedding(
                goal_text, self._brain_summary_for(goal_text),
            )
            embedding = self._encode_query(augmented)
            if embedding is not None:
                task_hash = _hash_goal(goal_text)
        self._memory.add_episode(
            handcraft_vector=context.to_vector(),
            agent=agent,
            reward=reward,
            embedding=embedding,
            task_hash=task_hash,
        )
